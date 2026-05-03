"""
Agente conversacional do PJ Warmer.

Recebe input em linguagem natural do Hunter, identifica a intenção
(briefing ou busca de cases), coleta parâmetros faltantes de forma
conversacional e executa a ferramenta correspondente.

Ferramentas disponíveis:
  - get_briefing   : roda run_warmer completo (deep_research + cases)
  - search_cases   : busca cases por setor, área e/ou descrição livre

Ponto de entrada externo: `chat(user_message, history)`.
O run_chat.py mantém o loop e o histórico; este módulo cuida do LLM.
"""

from __future__ import annotations

from pydantic_ai import Agent, RunContext
from pydantic_ai.messages import ModelMessage
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.providers.google import GoogleProvider

from src.agents.warmer_orchestrator import run_warmer
from src.retrieval.cases import buscar_hibrido
from src.retrieval.formatters import format_as_markdown
from src.schemas.research import LeadInput
from src.utils.config import get_settings


# ============================================================================
# System prompt
# ============================================================================
SYSTEM_PROMPT = """\
Você é o assistente do PJ Warmer, uma ferramenta de inteligência \
pré-reunião para Hunters da Poli Júnior (empresa júnior de engenharia da USP).

# O que você consegue fazer
Você tem DUAS ferramentas:

1. `get_briefing` — gera um Warmer Briefing completo sobre uma empresa-lead.
   Requer exatamente 4 campos:
     - org_name       : nome da empresa
     - org_setor      : setor de atuação da empresa
     - person_name    : nome do contato
     - person_position: cargo do contato
   Se o Hunter não informar algum desses campos, pergunte antes de chamar \
a ferramenta. Não invente valores.

2. `search_cases` — busca cases da Poli Júnior.
   Aceita setor da empresa, área da PJ e/ou descrição livre do problema. \
Pelo menos um deve ser preenchido.

# Área da PJ vs. Setor da empresa
Quando o Hunter mencionar uma dessas expressões, trate como ÁREA da PJ \
(parâmetro `area_pj`):
  "Ciência de Dados", "Engenharia de Dados", "Inteligência de Negócios", \
"IA", "Inteligência Artificial"
Qualquer outra coisa (Saúde, Varejo, Logística, Tecnologia, Financeiro etc.) \
é SETOR da empresa (parâmetro `setor`).

# Pedidos fora do escopo
Se o pedido não for sobre briefing ou busca de cases, responda EXATAMENTE \
esta mensagem, sem alterar nem uma vírgula:
"Posso te ajudar com duas coisas: gerar um briefing pré-AT de uma empresa \
ou buscar cases da Poli Júnior. Tente algo como \
'briefing da Ambev, setor bebidas, contato João Silva, diretor comercial' \
ou 'cases de saúde' ou 'cases de ciência de dados sobre previsão de demanda'."

# Apresentação dos resultados
Quando uma ferramenta retornar um resultado, apresente o conteúdo diretamente. \
Não resuma nem reescreva o que a ferramenta devolveu. \
Uma frase introdutória curta é suficiente, se necessário.
"""


# ============================================================================
# Construção do agente
# ============================================================================
def _build_agent() -> Agent[None, str]:
    """
    Instancia o agente conversacional com as duas ferramentas registradas.

    Padrão lazy idêntico ao deep_research.py: construído na primeira chamada,
    reutilizado nas seguintes. Evita inicializar o cliente Gemini antes de
    o .env estar carregado.
    """
    settings = get_settings()
    provider = GoogleProvider(api_key=settings.gemini_api_key)
    model = GoogleModel(settings.gemini_model, provider=provider)

    agent: Agent[None, str] = Agent(
        model,
        output_type=str,
        system_prompt=SYSTEM_PROMPT,
    )

    # -------------------------------------------------------------------------
    # Ferramenta 1: briefing completo
    # -------------------------------------------------------------------------
    @agent.tool
    async def get_briefing(
        ctx: RunContext[None],
        org_name: str,
        org_setor: str,
        person_name: str,
        person_position: str,
    ) -> str:
        """
        Gera um Warmer Briefing completo para uma empresa-lead.
        Executa deep_research na web + busca de cases relacionados.
        Todos os 4 parâmetros são obrigatórios.
        """
        lead = LeadInput(
            org_name=org_name,
            org_setor=org_setor,
            person_name=person_name,
            person_position=person_position,
        )

        result = await run_warmer(lead)

        # Formata o briefing em Markdown
        briefing_md = format_as_markdown(result.briefing, lead)

        # Monta o bloco de cases de forma simples
        cases = result.cases
        cases_lines: list[str] = ["\n" + "=" * 70, "📂 Cases relacionados\n"]

        if cases.por_setor:
            cases_lines.append(f"Por setor ({len(cases.por_setor)}):")
            for c in cases.por_setor:
                cases_lines.append(
                    f"  [{c.id}] {c.cliente} | {c.setor_empresa} "
                    f"| {c.area_pj} / {c.servico_pj}"
                )
        else:
            cases_lines.append("Por setor: nenhum case encontrado.")

        if cases.por_relacao:
            cases_lines.append(f"\nPor relação ({len(cases.por_relacao)}):")
            for c in cases.por_relacao:
                cases_lines.append(
                    f"  [{c.id}] {c.cliente} | {c.setor_empresa}"
                )

        if cases.setores_consultados:
            cases_lines.append(
                f"\nSetores consultados: {', '.join(cases.setores_consultados)}"
            )

        return briefing_md + "\n".join(cases_lines)

    # -------------------------------------------------------------------------
    # Ferramenta 2: busca de cases
    # -------------------------------------------------------------------------
    @agent.tool
    def search_cases(
        ctx: RunContext[None],
        setor: str = "",
        area_pj: str = "",
        descricao: str = "",
    ) -> str:
        """
        Busca cases da Poli Júnior.
        Parâmetros opcionais, mas pelo menos um deve ser preenchido:
          - setor    : setor da empresa cliente (ex: 'Saúde', 'Varejo')
          - area_pj  : área da Poli Júnior (somente: 'Engenharia de Dados' ou 'ED', 'Ciência de Dados' ou 'CD', 'Inteligência de Negócios' ou 'IN', 'IA' ou 'Inteligência Artificial')
          - descricao: descrição livre do problema (ex: 'previsão de demanda')
        """
        # Guarda contra chamada completamente vazia
        if not any([setor, area_pj, descricao]):
            return (
                "Preciso de pelo menos um critério: setor da empresa, "
                "área da PJ ou uma descrição do problema."
            )

        filtros: dict = {}
        if setor:
            filtros["setor"] = setor
        if area_pj:
            filtros["area_pj"] = area_pj

        resultados = buscar_hibrido(filtros, descricao, top_k=5)

        if not resultados:
            # Monta mensagem específica com os critérios usados para o Hunter
            # entender por que não achou (e não achar que é bug).
            criterios = []
            if setor:
                criterios.append(f"setor={setor!r}")
            if area_pj:
                criterios.append(f"área={area_pj!r}")
            if descricao:
                criterios.append(f"descrição={descricao!r}")
            return f"Nenhum case encontrado para {', '.join(criterios)}."

        linhas: list[str] = [f"Encontrei {len(resultados)} case(s):\n"]
        for c in resultados:
            score_str = (
                f" (score={c.score_similaridade:.2f})"
                if c.score_similaridade is not None
                else ""
            )
            linhas.append(
                f"  [{c.id}] {c.cliente} | {c.setor_empresa} "
                f"| {c.area_pj} / {c.servico_pj}{score_str}"
            )
            # Trecho do problema para o Hunter avaliar a pertinência
            trecho = c.problema[:120].rstrip()
            if len(c.problema) > 120:
                trecho += "..."
            linhas.append(f"       {trecho}\n")

        return "\n".join(linhas)

    return agent


# ============================================================================
# Singleton + função pública
# ============================================================================
_agent: Agent[None, str] | None = None


def _get_agent() -> Agent[None, str]:
    """Retorna o agente, criando-o na primeira vez (lazy singleton)."""
    global _agent
    if _agent is None:
        _agent = _build_agent()
    return _agent


async def chat(
    user_message: str,
    history: list[ModelMessage] | None = None,
) -> tuple[str, list[ModelMessage]]:
    """
    Envia uma mensagem ao agente e devolve (resposta, histórico_atualizado).

    Parameters
    ----------
    user_message : str
        Input do Hunter em linguagem natural.
    history : list[ModelMessage] | None
        Histórico da conversa atual. Passar None na primeira mensagem
        é equivalente a passar uma lista vazia.

    Returns
    -------
    tuple[str, list[ModelMessage]]
        - resposta  : texto do agente pronto para exibir
        - histórico : histórico completo, incluindo este turno.
                      Guarde e passe de volta na próxima chamada.
    """
    agent = _get_agent()
    result = await agent.run(user_message, message_history=history or [])
    return result.output, result.all_messages()
