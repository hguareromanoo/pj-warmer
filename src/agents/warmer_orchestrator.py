"""
Orquestrador final do Warmer: deep_research + cases relacionados.

`run_warmer(lead)` é o ponto de entrada único da integração. Tudo que
um consumidor (CLI manual hoje; slash commands `/briefing` no futuro;
agente de chat com pergunta livre) precisa fazer é chamar essa função
e receber um `BriefingComCases`.

Princípios fixados em PLANEJAMENTO_INTEGRACAO_SETOR.md (seção 3.1):

  - Assinatura mínima: `async def run_warmer(lead) -> BriefingComCases`.
  - SEM efeitos colaterais: não imprime, não escreve arquivo, não logga
    nada ruidoso. Quem chama decide o que fazer com o resultado
    (formatar em Markdown, salvar JSON, mandar pro Pipedrive, etc.).
  - Resultado serializável via Pydantic, pronto para virar nota
    estruturada no Pipedrive ou payload de API quando o tempo chegar.
"""

from __future__ import annotations

from src.agents.case_matcher import encontrar_cases_para_briefing
from src.retrieval.deep_research import run_deep_research
from src.schemas.research import BriefingComCases, LeadInput


async def run_warmer(lead: LeadInput) -> BriefingComCases:
    """
    Roda o Warmer completo para um lead: deep_research + matcher de cases.

    Parameters
    ----------
    lead : LeadInput
        Empresa e contato do lead.

    Returns
    -------
    BriefingComCases
        Briefing factual produzido pelo agente de pesquisa, junto do
        bloco de cases relacionados (por setor e por relação).
    """
    # `run_deep_research` é a parte pesada (web + LLM). Awaiting é
    # essencial — sem isso, ficaríamos com a coroutine, não com o briefing.
    briefing = await run_deep_research(lead)

    # `encontrar_cases_para_briefing` é síncrona (só DB + cálculo de
    # cosseno em memória). Não precisa de `await`. Mantida síncrona
    # propositalmente — torná-la async sem motivo só polui a interface.
    cases = encontrar_cases_para_briefing(briefing, lead)

    return BriefingComCases(briefing=briefing, cases=cases)
