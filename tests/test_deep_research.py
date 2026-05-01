"""
Testes unitários do módulo Warmer.

Princípios seguidos:
  - **Sem chamadas reais de API**: tudo mockado. Testes não gastam crédito,
    não dependem de internet, e rodam em milissegundos.
  - **Cada teste isola uma única responsabilidade**: se um teste quebrar,
    o nome dele já indica onde está o problema.
  - **TestModel do pydantic-ai** substitui o Gemini real nos testes do agente.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from pydantic_ai import models
from pydantic_ai.models.test import TestModel

from src.retrieval import deep_research
from src.retrieval.deep_research import _search_tavily, run_deep_research
from src.retrieval.formatters import (
    SCHEMA_VERSION,
    format_as_markdown,
    to_json_payload,
)
from src.schemas.research import (
    CompanyProfile,
    IndustryContext,
    LeadInput,
    LeadProfile,
    WarmerBriefing,
)


# ============================================================================
# Fixtures: dados/objetos reutilizados nos testes
# ============================================================================
@pytest.fixture
def sample_lead() -> LeadInput:
    """Um lead de exemplo para usar nos testes."""
    return LeadInput(
        org_name="Acme Logística",
        org_setor="Transporte e Logística",
        person_name="Maria Souza",
        person_position="Gerente de Operações",
    )


@pytest.fixture
def full_briefing() -> WarmerBriefing:
    """WarmerBriefing com TODOS os campos preenchidos (cenário rico)."""
    return WarmerBriefing(
        tldr="Acme é uma transportadora rodoviária. Maria é Gerente de Operações há 3 anos. Recente expansão para o Nordeste é gancho relevante.",
        company=CompanyProfile(
            description="Transportadora rodoviária focada em cargas fracionadas no Sudeste.",
            founded_year=1998,
            size_indicator="média empresa, ~800 colaboradores",
            notable_partners_or_clients=["Magazine Luiza", "Mercado Livre"],
            recent_milestones=["Expansão para o Nordeste (2024)"],
            awards_recognitions=["Great Place to Work (2024)"],
        ),
        lead=LeadProfile(
            current_role_summary="Coordena operações regionais e otimização de rotas.",
            tenure_at_company="No cargo desde 2022",
            professional_background="Engenheira de Produção, MBA em Logística.",
            notable_public_signals=["Palestra no LogTech Summit 2024"],
        ),
        industry=IndustryContext(
            current_trends="Setor pressionado por custos de combustível e ESG. Crescimento do e-commerce demanda última milha.",
            common_challenges=["Margem apertada", "Falta de motoristas"],
            direct_competitors=["JSL", "Tegma"],
            recent_sector_news=["Novo marco regulatório do TAC entra em vigor (2024)"],
        ),
        recent_company_news=["Mar/2025: Anúncio de centro de distribuição em Recife"],
    )


@pytest.fixture
def minimal_briefing() -> WarmerBriefing:
    """WarmerBriefing com APENAS os campos obrigatórios (cenário pobre)."""
    return WarmerBriefing(
        tldr="Empresa pequena, pouca info pública.",
        company=CompanyProfile(
            description="Empresa de software B2B.",
        ),
        lead=LeadProfile(
            current_role_summary="Coordena equipe técnica.",
        ),
        industry=IndustryContext(
            current_trends="Setor de SaaS B2B em consolidação.",
        ),
    )


@pytest.fixture(autouse=True)
def reset_agent_singleton():
    """
    Garante que cada teste comece com o agente "limpo".

    O `_agent` é um singleton no módulo. Sem isso, um teste que monkey-patcha
    o agente afetaria os próximos testes.
    """
    deep_research._agent = None
    yield
    deep_research._agent = None


@pytest.fixture(autouse=True)
def fake_settings(monkeypatch):
    """
    Garante que get_settings() funcione sem precisar de .env real.

    monkeypatch.setenv é a forma oficial do pytest de mexer em env vars
    durante o teste, e desfaz a mudança automaticamente no final.
    """
    monkeypatch.setenv("TAVILY_API_KEY", "fake-tavily-key")
    monkeypatch.setenv("GEMINI_API_KEY", "fake-gemini-key")
    from src.utils.config import get_settings

    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ============================================================================
# Testes do wrapper Tavily (_search_tavily)
# ============================================================================
class TestSearchTavily:
    """Testa a função que chama o Tavily."""

    def test_returns_formatted_results_on_success(self):
        """Quando o Tavily retorna resultados, formatamos corretamente."""
        fake_response = {
            "results": [
                {"url": "https://example.com/a", "content": "Conteúdo A"},
                {"url": "https://example.com/b", "content": "Conteúdo B"},
            ]
        }

        with patch.object(deep_research, "TavilyClient") as mock_client_cls:
            mock_client_cls.return_value.search.return_value = fake_response
            output = _search_tavily("query teste")

        assert "Fonte: https://example.com/a" in output
        assert "Conteúdo: Conteúdo A" in output
        assert "Fonte: https://example.com/b" in output
        assert "---" in output  # separador entre resultados

    def test_returns_friendly_message_when_no_results(self):
        """Quando o Tavily devolve lista vazia, devolvemos mensagem amigável."""
        with patch.object(deep_research, "TavilyClient") as mock_client_cls:
            mock_client_cls.return_value.search.return_value = {"results": []}
            output = _search_tavily("nada vai dar match")

        assert "Nenhum resultado encontrado" in output

    def test_returns_error_message_when_tavily_raises(self):
        """Se o Tavily lança exceção, devolvemos string de erro (não estouramos)."""
        with patch.object(deep_research, "TavilyClient") as mock_client_cls:
            mock_client_cls.return_value.search.side_effect = RuntimeError("API down")
            output = _search_tavily("qualquer coisa")

        assert "Erro ao realizar a busca" in output
        assert "API down" in output

    def test_passes_search_depth_and_max_results(self):
        """Confirma que os parâmetros de configuração chegam no Tavily."""
        with patch.object(deep_research, "TavilyClient") as mock_client_cls:
            mock_search = mock_client_cls.return_value.search
            mock_search.return_value = {"results": []}
            _search_tavily("query teste")

        mock_search.assert_called_once()
        kwargs = mock_search.call_args.kwargs
        assert kwargs["query"] == "query teste"
        assert kwargs["search_depth"] == "advanced"
        assert kwargs["max_results"] == 3

    def test_handles_missing_url_or_content_gracefully(self):
        """Se o Tavily devolver resultado mal-formado, não quebramos."""
        fake_response = {
            "results": [
                {"url": "https://example.com/a"},  # sem content
                {"content": "só conteúdo"},  # sem url
            ]
        }
        with patch.object(deep_research, "TavilyClient") as mock_client_cls:
            mock_client_cls.return_value.search.return_value = fake_response
            output = _search_tavily("query")

        assert "https://example.com/a" in output
        assert "só conteúdo" in output
        assert "desconhecida" in output  # placeholder para url ausente


# ============================================================================
# Testes do agente (run_deep_research)
# ============================================================================
class TestRunDeepResearch:
    """Testa a função pública que orquestra o agente."""

    @pytest.mark.asyncio
    async def test_returns_warmer_briefing(self, sample_lead):
        """
        O agente devolve um WarmerBriefing válido.

        Usamos TestModel do pydantic-ai: ele NÃO chama LLM de verdade,
        apenas gera dados que satisfazem o output_type declarado.
        """
        with models.override_allow_model_requests(True):
            test_model = TestModel()
            agent = deep_research._get_agent()
            with agent.override(model=test_model):
                briefing = await run_deep_research(sample_lead)

        assert isinstance(briefing, WarmerBriefing)
        assert isinstance(briefing.tldr, str)
        assert isinstance(briefing.company, CompanyProfile)
        assert isinstance(briefing.lead, LeadProfile)
        assert isinstance(briefing.industry, IndustryContext)

    @pytest.mark.asyncio
    async def test_propagates_agent_errors(self, sample_lead):
        """Se o agente quebrar, a exceção sobe (não engolimos silenciosamente)."""
        agent = deep_research._get_agent()
        with patch.object(agent, "run", side_effect=RuntimeError("LLM offline")):
            with pytest.raises(RuntimeError, match="LLM offline"):
                await run_deep_research(sample_lead)


# ============================================================================
# Testes do renderizador Markdown (format_as_markdown)
# ============================================================================
class TestFormatAsMarkdown:
    """Testa a função de formatação Markdown para exibição no chat."""

    def test_includes_tldr_at_top(self, sample_lead, full_briefing):
        """O TL;DR aparece dentro de um blockquote logo após o título."""
        out = format_as_markdown(full_briefing, sample_lead)
        # O TL;DR vem antes da primeira seção H2
        tldr_pos = out.find("TL;DR")
        first_h2_pos = out.find("\n## ")
        assert tldr_pos > 0
        assert first_h2_pos > tldr_pos
        # E aparece em blockquote (linha começa com ">")
        assert "> **TL;DR**" in out

    def test_includes_lead_metadata_in_headers(self, sample_lead, full_briefing):
        """Nome da empresa, do contato, cargo e setor aparecem nos cabeçalhos."""
        out = format_as_markdown(full_briefing, sample_lead)
        assert "Acme Logística" in out
        assert "Maria Souza" in out
        assert "Gerente de Operações" in out
        assert "Transporte e Logística" in out

    def test_renders_all_three_main_sections(self, sample_lead, full_briefing):
        """As 3 seções principais (Empresa, Lead, Setor) aparecem na ordem certa."""
        out = format_as_markdown(full_briefing, sample_lead)
        empresa_pos = out.find("## 🏢 Empresa")
        lead_pos = out.find("## 👤 Lead")
        setor_pos = out.find("## 🏭 Setor")
        assert empresa_pos > 0
        assert lead_pos > empresa_pos
        assert setor_pos > lead_pos

    def test_omits_optional_fields_when_none(self, sample_lead, minimal_briefing):
        """Campos opcionais com valor None NÃO devem aparecer no Markdown."""
        out = format_as_markdown(minimal_briefing, sample_lead)

        # Esses campos estão None no minimal_briefing → não devem aparecer
        assert "Fundada em" not in out
        assert "Porte" not in out
        assert "Parceiros" not in out
        assert "Marcos recentes" not in out
        assert "Prêmios" not in out
        assert "Tempo de casa" not in out
        assert "Bagagem prévia" not in out
        assert "Sinais públicos" not in out
        assert "Dores comuns" not in out
        assert "Concorrentes diretos" not in out
        assert "Notícias relevantes do setor" not in out
        assert "Notícias recentes da empresa" not in out

    def test_renders_lists_as_bullet_points(self, sample_lead, full_briefing):
        """Listas viram bullet points em Markdown."""
        out = format_as_markdown(full_briefing, sample_lead)
        # Parceiros do full_briefing
        assert "- Magazine Luiza" in out
        assert "- Mercado Livre" in out
        # Concorrentes
        assert "- JSL" in out
        # Notícias da empresa
        assert "- Mar/2025: Anúncio de centro de distribuição em Recife" in out

    def test_minimal_briefing_still_renders_required_fields(
        self, sample_lead, minimal_briefing
    ):
        """Mesmo com tudo opcional vazio, os 4 campos obrigatórios aparecem."""
        out = format_as_markdown(minimal_briefing, sample_lead)
        assert "Empresa pequena" in out  # tldr
        assert "Empresa de software B2B" in out  # company.description
        assert "Coordena equipe técnica" in out  # lead.current_role_summary
        assert "SaaS B2B" in out  # industry.current_trends


# ============================================================================
# Testes do renderizador JSON (to_json_payload)
# ============================================================================
class TestToJsonPayload:
    """Testa a função de conversão para JSON-ready dict."""

    def test_payload_has_required_top_level_keys(self, sample_lead, full_briefing):
        """O payload tem schema_version, generated_at, lead e briefing."""
        payload = to_json_payload(full_briefing, sample_lead)
        assert "schema_version" in payload
        assert "generated_at" in payload
        assert "lead" in payload
        assert "briefing" in payload

    def test_schema_version_matches_module_constant(
        self, sample_lead, full_briefing
    ):
        """schema_version reflete a constante do módulo."""
        payload = to_json_payload(full_briefing, sample_lead)
        assert payload["schema_version"] == SCHEMA_VERSION

    def test_payload_is_json_serializable(self, sample_lead, full_briefing):
        """O dict pode ser passado direto para json.dumps sem erro."""
        payload = to_json_payload(full_briefing, sample_lead)
        # Se algo no payload não for serializável, isso vai estourar
        as_str = json.dumps(payload, ensure_ascii=False)
        assert isinstance(as_str, str)
        assert "Acme Logística" in as_str

    def test_optional_none_fields_preserved_as_null(
        self, sample_lead, minimal_briefing
    ):
        """
        No JSON, campos opcionais None aparecem como null (preservados).

        Diferente do Markdown — JSON precisa preservar a estrutura completa
        para quem consome saber a diferença entre 'não pesquisado' e
        'pesquisado e não encontrado'.
        """
        payload = to_json_payload(minimal_briefing, sample_lead)
        company = payload["briefing"]["company"]
        assert company["founded_year"] is None
        assert company["awards_recognitions"] is None
        assert payload["briefing"]["recent_company_news"] is None


# ============================================================================
# Testes dos schemas (validações Pydantic)
# ============================================================================
class TestSchemas:
    """Confere que os schemas Pydantic se comportam como esperado."""

    def test_lead_input_requires_all_fields(self):
        """Faltando qualquer campo, Pydantic deve recusar."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            LeadInput(org_name="x", org_setor="y", person_name="z")  # type: ignore

    def test_company_profile_rejects_invalid_founded_year(self):
        """founded_year tem range 1800-2100 — anos absurdos são recusados."""
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            CompanyProfile(description="...", founded_year=1500)
        with pytest.raises(ValidationError):
            CompanyProfile(description="...", founded_year=2500)

    def test_warmer_briefing_requires_only_4_essential_fields(self):
        """
        WarmerBriefing pode ser criado só com tldr + os 3 sub-modelos
        (cada sub-modelo só com seus campos obrigatórios).
        """
        briefing = WarmerBriefing(
            tldr="t",
            company=CompanyProfile(description="d"),
            lead=LeadProfile(current_role_summary="c"),
            industry=IndustryContext(current_trends="i"),
        )
        # Tudo opcional deve estar None
        assert briefing.recent_company_news is None
        assert briefing.company.founded_year is None
        assert briefing.lead.tenure_at_company is None
        assert briefing.industry.direct_competitors is None

    def test_list_fields_have_max_length(self):
        """Listas têm limites superiores (ex: max 5 parceiros)."""
        from pydantic import ValidationError

        # 6 itens em notable_partners_or_clients (max=5) deve falhar
        with pytest.raises(ValidationError):
            CompanyProfile(
                description="d",
                notable_partners_or_clients=["a", "b", "c", "d", "e", "f"],
            )