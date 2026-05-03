"""
Camada de retrieval de cases.

Três funções públicas, com propósitos distintos (planejamento sec. 3.5):

- `buscar_deterministico(filtros)`  -> SQL puro com WHERE. Retorna todos os matches.
- `buscar_semantico(query, top_k)`  -> Embedda a query, busca por similaridade.
- `buscar_hibrido(filtros, query, top_k)` -> WHERE + ORDER BY similaridade.

Os casos extremos do híbrido caem nos branches "puros":
- filtros vazios + query        -> equivalente a buscar_semantico
- filtros + query vazia         -> equivalente a buscar_deterministico
- ambos vazios                  -> retorna todos os cases (sem ordem)

Filtros suportados (chaves do dict `filtros`):
- "setor"      -> match em `setor_empresa` OU em `setores_associados`
                  (a ideia é "cases relevantes para esse setor", não importa
                  se ele é o setor primário ou um setor associado).
- "area_pj"    -> match exato em `area_pj`.
- "servico_pj" -> match exato em `servico_pj`.

Filtros desconhecidos disparam ValueError, em vez de serem silenciosamente
ignorados. Falha barulhenta protege contra typos.
"""

from typing import Optional, TypedDict

from sqlalchemy import func, literal, or_, select, text
from sqlmodel import Session

from src.db.models import CasePJ, CasePJEmbedding
from src.db.session import get_engine
from src.services.embeddings import embed, model_version_tag
from src.schemas.case import CaseResultado


# Lista canônica de chaves aceitas em `filtros`. Mantida como constante
# para validar entradas e ficar fácil de estender no futuro.
FILTROS_SUPORTADOS: frozenset[str] = frozenset({"setor", "area_pj", "servico_pj"})


class FiltrosCase(TypedDict, total=False):
    """
    Tipo opcional para autocomplete em editores. Não obrigatório — o código
    aceita qualquer dict com as chaves listadas em FILTROS_SUPORTADOS.
    """

    setor: str
    area_pj: str
    servico_pj: str


# -----------------------------------------------------------------------------
# Helpers internos
# -----------------------------------------------------------------------------
def _validar_filtros(filtros: dict) -> None:
    """Levanta ValueError se houver chave desconhecida em `filtros`."""
    desconhecidas = set(filtros) - FILTROS_SUPORTADOS
    if desconhecidas:
        raise ValueError(
            f"Filtro(s) desconhecido(s): {sorted(desconhecidas)}. "
            f"Aceitos: {sorted(FILTROS_SUPORTADOS)}."
        )


def _norm(value):
    """
    Normaliza um valor (coluna ou string literal) para comparação
    case-insensitive E accent-insensitive.

    `unaccent("Saúde")` -> "Saude"
    `lower("Saude")`    -> "saude"

    Combinando os dois, "Saúde", "saude", "SAÚDE" e "SAUDE" colapsam
    todos para "saude" — e a comparação == funciona como esperado.

    Requer a extensão `unaccent` habilitada no Postgres (migração
    b2c3d4e5f6a7).
    """
    return func.lower(func.unaccent(value))


def _aplicar_filtros(stmt, filtros: dict):
    """
    Aplica os filtros do dict numa query SQLAlchemy e retorna a query nova.

    Recebe e devolve um statement (não muta nada in-place). É chamado tanto
    pela busca determinística quanto pela híbrida, por isso está extraído.

    Todos os matches são case-insensitive e accent-insensitive — ver `_norm`.
    """
    if "setor" in filtros:
        setor = filtros["setor"]
        # Match em setor primário OU em setores associados.
        #
        # Para o array (setores_associados), não dá pra usar .any() direto
        # com normalização — o ANY do Postgres compara o valor literal, sem
        # passar por unaccent/lower em cada elemento. Solução: subquery
        # EXISTS que faz unnest do array e aplica a normalização em cada
        # elemento. Custo: irrelevante para 24-1000 cases.
        stmt = stmt.where(
            or_(
                _norm(CasePJ.setor_empresa) == _norm(setor),
                text(
                    "EXISTS ("
                    "  SELECT 1 FROM unnest(casepj.setores_associados) AS s"
                    "  WHERE lower(unaccent(s)) = lower(unaccent(:setor_filtro))"
                    ")"
                ).bindparams(setor_filtro=setor),
            )
        )
    if "area_pj" in filtros:
        stmt = stmt.where(_norm(CasePJ.area_pj) == _norm(filtros["area_pj"]))
    if "servico_pj" in filtros:
        stmt = stmt.where(_norm(CasePJ.servico_pj) == _norm(filtros["servico_pj"]))
    return stmt


def _motivo_filtros(filtros: dict) -> str:
    """Formata os filtros aplicados em string legível para o motivo_match."""
    if not filtros:
        return ""
    pares = [f"{k}={v}" for k, v in filtros.items()]
    return "filtro: " + ", ".join(pares)


def _case_to_resultado(
    case: CasePJ,
    *,
    score: Optional[float] = None,
    motivo: str,
) -> CaseResultado:
    """Converte uma linha CasePJ + score + motivo em CaseResultado."""
    return CaseResultado(
        id=case.id,  # type: ignore[arg-type]
        cliente=case.cliente,
        setor_empresa=case.setor_empresa,
        setores_associados=case.setores_associados or [],
        area_pj=case.area_pj,
        servico_pj=case.servico_pj,
        problema=case.problema,
        solucao=case.solucao,
        impacto_roi=case.impacto_roi,
        score_similaridade=score,
        motivo_match=motivo,
    )


# -----------------------------------------------------------------------------
# Busca determinística
# -----------------------------------------------------------------------------
def buscar_deterministico(filtros: dict) -> list[CaseResultado]:
    """
    Filtra cases por categoria, sem usar embeddings.

    Parameters
    ----------
    filtros : dict
        Dict com as chaves de FILTROS_SUPORTADOS. Pode estar vazio (retorna
        todos os cases).

    Returns
    -------
    list[CaseResultado]
        Todos os cases que casam com os filtros. Ordem indefinida (a do banco).
        `score_similaridade` é sempre None aqui.
    """
    _validar_filtros(filtros)

    stmt = select(CasePJ)
    stmt = _aplicar_filtros(stmt, filtros)

    motivo = _motivo_filtros(filtros) or "filtro: (nenhum — todos os cases)"

    with Session(get_engine()) as session:
        cases = session.scalars(stmt).all()

    return [_case_to_resultado(c, motivo=motivo) for c in cases]


# -----------------------------------------------------------------------------
# Busca por nomes de cliente (Lane B da integração com deep_research)
# -----------------------------------------------------------------------------
def buscar_por_clientes(nomes: list[str]) -> list[CaseResultado]:
    """
    Retorna cases cujo `cliente` bate (case+accent insensitive) com algum
    nome da lista.

    Usado pela integração com o briefing (PLANEJAMENTO_INTEGRACAO_SETOR.md,
    seção 3.3) para encontrar cases de clientes que sejam parceiros ou
    concorrentes do lead. Os dois campos do briefing
    (`notable_partners_or_clients` e `direct_competitors`) chegam aqui
    unidos numa lista única — quem chama é responsável por essa união.

    Match exato após normalização — sem fuzzy/trigram. Decisão consciente
    do MVP: o risco de falso positivo com nomes curtos não compensa hoje
    (ver planejamento 3.3, "Match de parceiros/concorrentes").

    Parameters
    ----------
    nomes : list[str]
        Nomes de clientes a buscar. Strings vazias / só-whitespace são
        ignoradas. Lista vazia (após filtro) curto-circuita para `[]`
        sem tocar no banco.

    Returns
    -------
    list[CaseResultado]
        Cases que casam. Ordem indefinida (a do banco). `motivo_match`
        fixo em "cliente=parceiro/concorrente do lead". Sem score.
    """
    # Sanitiza ANTES de validar: o caso "lista só com strings vazias" deve
    # cair no curto-circuito, não disparar query com IN vazio (que em
    # alguns dialects vira `IN ()`, sintaticamente inválido).
    nomes_limpos = [n.strip() for n in nomes if n and n.strip()]
    if not nomes_limpos:
        return []

    # `_norm` envolve a expressão em lower(unaccent(...)). Aplicado nos dois
    # lados (coluna e literal) garante simetria — "Hypera Pharma" casa com
    # "hypera pharma", "HYPERA PHARMA", "Hypéra Pharma", etc.
    stmt = select(CasePJ).where(
        _norm(CasePJ.cliente).in_([_norm(literal(n)) for n in nomes_limpos])
    )

    motivo = "cliente=parceiro/concorrente do lead"

    with Session(get_engine()) as session:
        cases = session.scalars(stmt).all()

    return [_case_to_resultado(c, motivo=motivo) for c in cases]


# -----------------------------------------------------------------------------
# Busca semântica
# -----------------------------------------------------------------------------
def buscar_semantico(query: str, top_k: int = 5) -> list[CaseResultado]:
    """
    Embedda a query e retorna os top_k cases mais parecidos por cosseno.

    Parameters
    ----------
    query : str
        Texto livre — descrição do problema do lead, por exemplo.
    top_k : int, optional
        Quantos resultados retornar (default 5).

    Returns
    -------
    list[CaseResultado]
        Cases ordenados do mais parecido para o menos parecido.
        `score_similaridade` é cosseno em [-1, 1] (perto de 1 = muito parecido).
    """
    if not query or not query.strip():
        raise ValueError("query não pode ser vazia em buscar_semantico.")
    if top_k <= 0:
        raise ValueError(f"top_k deve ser positivo, recebi {top_k}.")

    # Embedda como QUERY (não DOCUMENT) — o modelo ajusta o vetor para
    # casar com vetores indexados como retrieval_document.
    query_vector = embed(query, task_type="retrieval_query")
    model_ver = model_version_tag()

    # `cosine_distance` vem do tipo Vector do pgvector. Gera o operador
    # `<=>` no SQL. Ordenando por distância crescente, o mais parecido
    # vem primeiro.
    distance = CasePJEmbedding.embedding.cosine_distance(query_vector).label(
        "distance"
    )

    stmt = (
        select(CasePJ, distance)
        .join(CasePJEmbedding, CasePJ.id == CasePJEmbedding.case_id)
        .where(CasePJEmbedding.model_version == model_ver)
        .order_by(distance)
        .limit(top_k)
    )

    with Session(get_engine()) as session:
        rows = session.execute(stmt).all()

    return [
        _case_to_resultado(
            case,
            score=_distance_to_similarity(dist),
            motivo=f"similaridade: {_distance_to_similarity(dist):.2f}",
        )
        for case, dist in rows
    ]


# -----------------------------------------------------------------------------
# Busca híbrida
# -----------------------------------------------------------------------------
def buscar_hibrido(
    filtros: dict,
    query: str,
    top_k: int = 5,
) -> list[CaseResultado]:
    """
    Filtra por categoria E ordena por similaridade.

    Esta é a função que, na prática, vai ser mais usada pelo agente: dá
    pra dizer "cases do setor de Varejo, em ordem de quanto eles batem
    com 'previsão de demanda em sazonalidade'".

    Casos extremos:
    - filtros vazios e query não vazia    -> equivale a buscar_semantico
    - filtros não vazios e query vazia    -> equivale a buscar_deterministico
    - ambos vazios                         -> retorna todos (sem ordem)
    """
    _validar_filtros(filtros)
    if top_k <= 0:
        raise ValueError(f"top_k deve ser positivo, recebi {top_k}.")

    query_limpa = query.strip() if query else ""

    # Branch 1: sem query -> determinístico puro
    if not query_limpa:
        return buscar_deterministico(filtros)

    # Branch 2: sem filtros -> semântico puro
    if not filtros:
        return buscar_semantico(query_limpa, top_k=top_k)

    # Branch 3: ambos -> WHERE (filtros) + ORDER BY similaridade
    query_vector = embed(query_limpa, task_type="retrieval_query")
    model_ver = model_version_tag()

    distance = CasePJEmbedding.embedding.cosine_distance(query_vector).label(
        "distance"
    )

    stmt = (
        select(CasePJ, distance)
        .join(CasePJEmbedding, CasePJ.id == CasePJEmbedding.case_id)
        .where(CasePJEmbedding.model_version == model_ver)
        .order_by(distance)
        .limit(top_k)
    )
    stmt = _aplicar_filtros(stmt, filtros)

    motivo_filtro = _motivo_filtros(filtros)

    with Session(get_engine()) as session:
        rows = session.execute(stmt).all()

    return [
        _case_to_resultado(
            case,
            score=_distance_to_similarity(dist),
            motivo=(
                f"{motivo_filtro} | similaridade: {_distance_to_similarity(dist):.2f}"
            ),
        )
        for case, dist in rows
    ]


# -----------------------------------------------------------------------------
# Util
# -----------------------------------------------------------------------------
def _distance_to_similarity(distance: float) -> float:
    """
    Converte distância de cosseno (do pgvector `<=>`) em similaridade.

    Para vetores L2-normalizados, `cosine_distance = 1 - cos(θ)`, então
    `similarity = 1 - distance` recupera o cosseno em [-1, 1].
    """
    return float(1.0 - distance)
