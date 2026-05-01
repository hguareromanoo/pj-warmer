"""
Deep Research: agente de pesquisa pré-AT (Warmer Briefing).

Dado um lead (empresa + contato), este módulo:
  1. Consulta a web (Tavily) para coletar contexto sobre empresa, lead e setor.
  2. Sintetiza tudo num briefing estruturado (WarmerBriefing) seguindo a
     metodologia Challenger Sale (fase Warmer).

A identificação de "potential angles" para o portfólio da Poli Júnior é
deliberadamente NÃO incluída — essa identificação é responsabilidade do
Hunter durante a reunião, com base no contexto factual fornecido aqui.

Uso típico:
    >>> from src.retrieval.deep_research import run_deep_research
    >>> from src.schemas.research import LeadInput
    >>>
    >>> lead = LeadInput(
    ...     org_name="Acme Logística",
    ...     org_setor="Transporte e Logística",
    ...     person_name="Maria Souza",
    ...     person_position="Gerente de Operações",
    ... )
    >>> briefing = await run_deep_research(lead)
    >>> print(briefing.tldr)
"""

from __future__ import annotations

import logging

from pydantic_ai import Agent, RunContext
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider
from tavily import TavilyClient

from src.schemas.research import LeadInput, WarmerBriefing
from src.utils.config import get_current_date_str, get_settings

logger = logging.getLogger(__name__)


# ============================================================================
# 1. Wrapper isolado do Tavily (facilita mockar nos testes)
# ============================================================================
def _search_tavily(query: str) -> str:
    """
    Faz uma busca na web usando Tavily e devolve os resultados como texto.

    Está isolado em uma função separada (em vez de inline na ferramenta do
    agente) por dois motivos:
      1. Permite mockar em testes sem precisar instanciar o Agent.
      2. Centraliza tratamento de erro num lugar só.
    """
    settings = get_settings()
    client = TavilyClient(api_key=settings.tavily_api_key)

    try:
        response = client.search(
            query=query,
            search_depth=settings.tavily_search_depth,
            max_results=settings.tavily_max_results,
        )
    except Exception as exc:
        logger.exception("Falha na busca Tavily para query=%r", query)
        return f"Erro ao realizar a busca: {exc}"

    results = response.get("results", [])
    if not results:
        return "Nenhum resultado encontrado para esta busca."

    formatted = [
        f"Fonte: {res.get('url', 'desconhecida')}\nConteúdo: {res.get('content', '')}"
        for res in results
    ]
    return "\n\n---\n\n".join(formatted)


# ============================================================================
# 2. System prompt do agente (separado para legibilidade)
# ============================================================================
SYSTEM_PROMPT = """\
Você é um pesquisador investigativo sênior especializado em preparar \
material de Warmer (metodologia Challenger Sale) para reuniões consultivas \
de vendas. Trabalha para a Poli Júnior, empresa júnior de engenharia da USP.

# Sua missão
Construir um briefing FACTUAL e VERIFICÁVEL sobre uma empresa-lead, \
seu contato e o setor de atuação. O Hunter (vendedor) usará esse briefing \
para demonstrar conhecimento na abertura da reunião — o que cria credibilidade \
e abre espaço para a conversa consultiva.

Você NÃO precisa identificar oportunidades de venda nem mapear soluções. \
Essa é responsabilidade do Hunter durante a conversa. Sua entrega é o \
CONTEXTO; a interpretação comercial é deles.

# Princípios INEGOCIÁVEIS

## 1. Fatos > especulação
Toda informação que você incluir deve vir de uma fonte que você consultou \
via `search_web`. Se não encontrar evidência confiável para um campo \
opcional, use `null`. NUNCA invente dados para preencher o briefing.

## 2. Específico > genérico
Evite frases vazias como "empresa inovadora", "líder de mercado", \
"transformação digital", "soluções de ponta". Se você se pegar escrevendo \
algo que poderia descrever qualquer empresa, refaça com base em fatos \
específicos da empresa que você pesquisou.

## 3. Recente > antigo
Priorize informações dos últimos 12 meses para notícias e marcos. \
Para perfil da empresa e do contato, busque o estado atual.

## 4. Português do Brasil, tom executivo
O Hunter vai ler isso em segundos antes da reunião. Frases curtas, \
diretas, sem jargão acadêmico ou marketês.

# Estratégia de busca recomendada

Você tem orçamento para fazer múltiplas buscas. Sugestão de cobertura \
(adapte conforme o que for encontrando):

- 1-2 buscas sobre a EMPRESA: "{empresa} o que faz", \
"{empresa} fundação investidores", "{empresa} aquisições parcerias"
- 1-2 buscas sobre o LEAD: "{nome} {empresa} LinkedIn", \
"{nome} {empresa} entrevista palestra"
- 1 busca sobre NOTÍCIAS recentes: "{empresa} notícias 2024 2025"
- 1 busca sobre o SETOR: "tendências setor {setor} Brasil 2024 2025"
- 1 busca sobre CONCORRENTES: "{empresa} concorrentes principais"

Total esperado: 6-8 buscas. Faça menos se uma busca já cobrir múltiplos \
campos. Faça mais se uma busca trouxer pistas para investigar mais a fundo.

# Sobre o campo `tldr`
Esse é o RESUMO no topo do briefing. O Hunter pode ler só ele em situações \
de pressa. Deve conter: (1) o que a empresa faz em uma frase, \
(2) o cargo do contato, (3) UM gancho concreto para abrir a conversa \
(notícia, marco, parceria, ou tendência específica). Máximo 3 frases.

# Sobre uso de `null` em campos opcionais
É melhor ter um campo `null` do que um campo cheio de generalidade. \
Se você buscou e não encontrou evidência sobre prêmios, parceiros, \
trajetória etc., devolva `null`. O Hunter prefere a verdade ("não \
encontramos info pública sobre X") a uma invenção plausível.
"""


# ============================================================================
# 3. Construção do agente (lazy)
# ============================================================================
def _build_agent() -> Agent[None, WarmerBriefing]:
    """
    Constrói o agente de pesquisa.

    Está numa função (em vez de variável global) para que possamos:
      1. Adiar a inicialização até que `get_settings()` consiga ler o .env.
      2. Recriar facilmente em testes com configurações diferentes.
    """
    settings = get_settings()

    # Injetamos a chave do Gemini explicitamente via GoogleProvider, em vez
    # de depender da env var GOOGLE_API_KEY (que é o que o pydantic-ai
    # buscaria por padrão). Isso permite manter a env como GEMINI_API_KEY,
    # que é mais descritivo e bate com o que o Google AI Studio usa.
    provider = GoogleProvider(api_key=settings.gemini_api_key)
    model = GoogleModel(settings.gemini_model, provider=provider)

    agent = Agent(
        model,
        output_type=WarmerBriefing,
        system_prompt=SYSTEM_PROMPT,
    )

    @agent.tool
    def search_web(ctx: RunContext[None], query: str) -> str:
        """
        Pesquisa na internet por informações atualizadas sobre empresas,
        mercados, tendências e cargos.
        """
        logger.info("Tool search_web chamada com query=%r", query)
        return _search_tavily(query)

    return agent


# Agente compartilhado a nível de módulo. Inicializado na primeira chamada.
_agent: Agent[None, WarmerBriefing] | None = None


def _get_agent() -> Agent[None, WarmerBriefing]:
    """Retorna o agente, criando-o na primeira vez."""
    global _agent
    if _agent is None:
        _agent = _build_agent()
    return _agent


# ============================================================================
# 4. Função pública: ponto de entrada do módulo
# ============================================================================
async def run_deep_research(lead: LeadInput) -> WarmerBriefing:
    """
    Executa a pesquisa profunda sobre um lead e devolve o Warmer Briefing.

    Args:
        lead: Dados do lead a ser pesquisado (empresa + contato).

    Returns:
        WarmerBriefing estruturado com perfil da empresa, do lead, do setor,
        notícias recentes e um TL;DR executivo.

    Raises:
        Exception: re-lança erros do agente (configuração, rede, validação)
        para que o chamador decida o que fazer.
    """
    logger.info(
        "Iniciando warmer research para empresa=%r setor=%r contato=%r cargo=%r",
        lead.org_name,
        lead.org_setor,
        lead.person_name,
        lead.person_position,
    )

    prompt = (
        f"Hoje é {get_current_date_str()}. Use essa data como referência "
        f"para calibrar 'recente' (últimos 6-12 meses retroativos a hoje) "
        f"e para nunca registrar datas no futuro como se já tivessem ocorrido.\n\n"
        f"Construa um Warmer Briefing completo para a seguinte AT.\n\n"
        f"Empresa-lead: {lead.org_name}\n"
        f"Setor de atuação: {lead.org_setor}\n"
        f"Contato: {lead.person_name}\n"
        f"Cargo: {lead.person_position}\n\n"
        "Pesquise na web seguindo a estratégia recomendada e preencha o "
        "schema de saída com fatos verificáveis. Use null em campos "
        "opcionais quando não encontrar evidência confiável."
    )

    agent = _get_agent()
    result = await agent.run(prompt)
    return result.output