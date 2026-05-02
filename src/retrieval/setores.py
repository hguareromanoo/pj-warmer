"""
Helpers de retrieval ligados a SETORES (não a cases).

Públicos:
  - `listar_setores_unicos()` — setor primário ∪ setores associados.
  - `setores_similares(setor_lead, threshold)` — cosseno entre embeddings.

Por que isso vive em arquivo separado e não dentro de `cases.py`?
Porque a unidade de busca aqui é o SETOR (a string), não o case. As
duas camadas se encontram em `agents/case_matcher.py` (A.5).
"""

from functools import lru_cache

from sqlalchemy import text
from sqlmodel import Session

from src.db.session import get_engine
from src.services.embeddings import embed


def listar_setores_unicos() -> list[str]:
    """
    Retorna a lista única (ordenada) de setores presentes na base de cases.

    Combina, num UNION, o setor primário (`casepj.setor_empresa`) com cada
    elemento do array `casepj.setores_associados`. O `unnest` é necessário
    porque `setores_associados` é um array Postgres — sem ele, viria a
    string serializada do array como um único valor.

    Filtra `NULL` e strings vazias diretamente no SQL para não vazar lixo
    para a camada de embedding (A.3).

    Returns
    -------
    list[str]
        Setores distintos, em ordem alfabética. Hoje a base tem ~19.
    """
    # SQL único, simples, idempotente. Mantido inline (em vez de ORM) porque
    # `unnest` não tem expressão equivalente direta em SQLAlchemy/SQLModel
    # sem ginástica desnecessária para uma query desse tamanho.
    stmt = text(
        """
        SELECT setor FROM (
            SELECT DISTINCT setor_empresa AS setor
            FROM casepj
            WHERE setor_empresa IS NOT NULL AND setor_empresa <> ''
            UNION
            SELECT DISTINCT unnest(setores_associados) AS setor
            FROM casepj
        ) sub
        WHERE setor IS NOT NULL AND setor <> ''
        ORDER BY setor
        """
    )

    with Session(get_engine()) as session:
        rows = session.execute(stmt).all()

    # `rows` é uma lista de tuplas de 1 elemento; achatamos.
    return [row[0] for row in rows]


# -----------------------------------------------------------------------------
# Similaridade entre setores
# -----------------------------------------------------------------------------
@lru_cache(maxsize=None)
def _embed_setor(setor: str) -> tuple[float, ...]:
    """
    Embedda uma string de setor e cacheia o resultado por toda a vida do
    processo.

    Cache estratégico: a base tem ~19 setores únicos; sem cache, cada
    chamada de `setores_similares` faria 19 round-trips na API do Gemini.
    Com `lru_cache`, isso vira ~19 chamadas no PRIMEIRO briefing do
    processo e 0 chamadas a partir do segundo (mais 1 chamada para o
    `setor_lead`, que também é cacheado aqui se vier repetido).

    `task_type="semantic_similarity"` é a recomendação do Gemini quando
    o objetivo é comparar duas strings entre si (não query × documento
    indexado). Decisão fixada no planejamento 3.2.

    Retorno como `tuple` (e não `list`) só por consistência semântica:
    o vetor cacheado nunca deve ser mutado por quem chama. O custo de
    converter é desprezível.
    """
    return tuple(embed(setor, task_type="semantic_similarity"))


def _cosine_similarity(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    """
    Cosseno entre dois vetores JÁ L2-normalizados.

    Como `embed()` (em `services/embeddings.py`) garante norma 1 nos
    vetores devolvidos, o cosseno colapsa em produto interno simples —
    sem precisar dividir por normas. Se um dia a normalização sair de lá,
    esse atalho quebra silenciosamente; aceito o risco em troca da
    simplicidade.
    """
    return sum(x * y for x, y in zip(a, b))


def setores_similares(
    setor_lead: str,
    threshold: float = 0.86,
) -> list[tuple[str, float]]:
    """
    Setores da base com similaridade de cosseno >= `threshold` em relação
    ao `setor_lead`.

    Não exclui o próprio `setor_lead` se ele estiver na base (vai aparecer
    com score ~1.0). Quem orquestra (A.5) decide o que fazer com isso —
    deixar entrar simplifica o diagnóstico do `setores_consultados`.

    Parameters
    ----------
    setor_lead : str
        O setor declarado do lead (ex: `LeadInput.org_setor`).
    threshold : float, optional
        Cosseno mínimo para considerar um setor "próximo". Default 0.75
        (chute inicial; calibrar na A.7 contra cenários reais).

    Returns
    -------
    list[tuple[str, float]]
        Pares `(setor, score)` ordenados decrescente por score. Lista
        vazia se nada passar do threshold.
    """
    if not setor_lead or not setor_lead.strip():
        raise ValueError("setor_lead não pode ser vazio em setores_similares.")

    vetor_lead = _embed_setor(setor_lead.strip())

    resultados: list[tuple[str, float]] = []
    for setor in listar_setores_unicos():
        vetor_setor = _embed_setor(setor)
        score = _cosine_similarity(vetor_lead, vetor_setor)
        if score >= threshold:
            resultados.append((setor, score))

    # Ordem decrescente por score; em caso de empate, mantém ordem alfabética
    # natural vinda de listar_setores_unicos (estável).
    resultados.sort(key=lambda par: par[1], reverse=True)
    return resultados
