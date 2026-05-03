"""
Orquestrador da integração Cases × Deep Research (Lane A + Lane B).

Dado um `WarmerBriefing` (já produzido por `run_deep_research`) e o
`LeadInput` original, este módulo encontra os cases da Poli Júnior
relacionados ao lead via dois canais independentes — ver
PLANEJAMENTO_INTEGRACAO_SETOR.md, seção 3:

  - Lane A (por setor): setor primário do lead + setores semanticamente
    próximos (cosseno >= threshold).
  - Lane B (por relação): cliente do case == parceiro/concorrente do
    lead (match exato, case+accent insensitive).

A junção das duas lanes vira um `CasesParaBriefing`. Quem chama (em
geral `warmer_orchestrator.run_warmer`, A.6) decide o que fazer com
isso.

Esta função é SÍNCRONA. As chamadas internas (`embed`,
`buscar_deterministico`, `buscar_por_clientes`) também são síncronas;
não há proveito em torná-la `async` hoje.
"""

from __future__ import annotations

from src.retrieval.cases import buscar_deterministico, buscar_por_clientes
from src.retrieval.setores import setores_similares
from src.schemas.case import CaseResultado
from src.schemas.research import (
    CasesParaBriefing,
    LeadInput,
    WarmerBriefing,
)


def encontrar_cases_para_briefing(
    briefing: WarmerBriefing,
    lead: LeadInput,
    threshold_similaridade: float = 0.86,
) -> CasesParaBriefing:
    """
    Junta cases via Lane A (setor) e Lane B (relação) num
    `CasesParaBriefing`.

    Parameters
    ----------
    briefing : WarmerBriefing
        Briefing produzido por `run_deep_research`. Usado para extrair
        parceiros/concorrentes da Lane B.
    lead : LeadInput
        Lead original — usado pelo `org_setor` na Lane A.
    threshold_similaridade : float, optional
        Cosseno mínimo para um setor da base ser considerado "próximo"
        do `lead.org_setor`. Default 0.75 (chute inicial; calibrar
        contra cenários reais na A.7).

    Returns
    -------
    CasesParaBriefing
        Bloco anexado ao briefing, com `por_setor`, `por_relacao` e
        `setores_consultados` para auditoria.
    """
    # --- Lane A: por setor (primário + similares) ---------------------------
    # `setores_similares` já inclui o setor primário do lead se ele estiver
    # na base e tiver passado do threshold (cosseno entre vetores idênticos
    # ~= 1.0). Não precisamos forçar a inclusão.
    similares = setores_similares(lead.org_setor, threshold=threshold_similaridade)

    # Dedup por `case.id`. Iterando na ordem em que `setores_similares`
    # devolveu (decrescente por score), o primeiro a registrar um id "ganha"
    # — efetivamente: o case fica vinculado ao setor de maior score em que
    # apareceu. Decisão 3.5 do planejamento.
    por_setor: list[CaseResultado] = []
    ids_vistos: set[int] = set()
    for setor, _score in similares:
        for case in buscar_deterministico({"setor": setor}):
            if case.id in ids_vistos:
                continue
            ids_vistos.add(case.id)
            por_setor.append(case)

    # --- Lane B: por relação (parceiros + concorrentes) ---------------------
    # Os dois campos podem ser `None` no briefing (deep_research devolve
    # null quando não acha evidência). Convertemos para [] e somamos
    # antes de passar pra `buscar_por_clientes`, que já cuida de strings
    # vazias e do curto-circuito quando a lista total fica vazia.
    parceiros = briefing.company.notable_partners_or_clients or []
    concorrentes = briefing.industry.direct_competitors or []
    nomes_relacao = parceiros + concorrentes
    por_relacao = buscar_por_clientes(nomes_relacao)

    # --- Auditoria ----------------------------------------------------------
    # Lista os setores na MESMA ordem que entraram na busca (decrescente por
    # score). Permite ao Hunter inspecionar por que um case "óbvio" não
    # apareceu (setor primário ficou abaixo do threshold? só similares com
    # baixo score entraram?). Sem score aqui — manter a interface simples;
    # se virar útil, expor depois.
    setores_consultados = [setor for setor, _ in similares]

    return CasesParaBriefing(
        por_setor=por_setor,
        por_relacao=por_relacao,
        setores_consultados=setores_consultados,
    )
