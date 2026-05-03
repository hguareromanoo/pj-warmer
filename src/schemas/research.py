"""
Schemas Pydantic do Warmer Briefing.

Estrutura inspirada na metodologia Challenger Sale (fase "Warmer"):
o objetivo é dar ao Hunter o contexto necessário pra demonstrar
domínio sobre a empresa, o lead e o setor logo na abertura da AT.

Os schemas ficam isolados em `src/schemas/` para que possam ser
reutilizados por outras camadas:
  - persistência no banco (cada sub-objeto pode virar tabela)
  - serialização para a API do Pipedrive (notas estruturadas)
  - exibição na interface de chat (formatação Markdown)
  - input para outros agentes (ex: gerador de pitch)
"""

from typing import Optional

from pydantic import BaseModel, Field

from src.schemas.case import CaseResultado


# ============================================================================
# Input
# ============================================================================
class LeadInput(BaseModel):
    """
    Dados de entrada da pesquisa.

    Mantemos enxuto: nome da empresa, setor, nome do contato e cargo.
    Quando integrarmos com Pipedrive, esse modelo será populado
    automaticamente a partir do retorno da API.
    """

    org_name: str = Field(description="Nome da empresa-lead.")
    org_setor: str = Field(description="Setor de atuação da empresa.")
    person_name: str = Field(description="Nome do contato dentro da empresa.")
    person_position: str = Field(description="Cargo do contato.")


# ============================================================================
# Sub-modelo: Empresa
# ============================================================================
class CompanyProfile(BaseModel):
    """Perfil factual da empresa-lead."""

    description: str = Field(
        description=(
            "2 a 3 frases descrevendo o que a empresa faz, modelo de negócio "
            "e segmento exato de atuação. Seja específico, evite genéricos "
            "como 'empresa inovadora' ou 'líder de mercado'."
        ),
        max_length=600,
    )
    founded_year: Optional[int] = Field(
        default=None,
        description="Ano de fundação da empresa. Use null se não encontrar com confiança.",
        ge=1800,
        le=2100,
    )
    size_indicator: Optional[str] = Field(
        default=None,
        description=(
            "Indicador de porte em texto livre. Exemplos: "
            "'startup, ~50 funcionários', 'média empresa, R$ 200M de receita anual', "
            "'multinacional, 10k+ colaboradores'. Use null se não encontrar."
        ),
        max_length=200,
    )
    notable_partners_or_clients: Optional[list[str]] = Field(
        default=None,
        description=(
            "Até 5 parceiros, clientes ou investidores notáveis. "
            "Cada item: apenas o nome (ex: 'iFood', 'Itaú', 'Sequoia Capital'). "
            "Use null se não encontrar nenhum."
        ),
        max_length=5,
    )
    recent_milestones: Optional[list[str]] = Field(
        default=None,
        description=(
            "Até 5 marcos importantes recentes (rodadas de investimento, "
            "aquisições, expansões, lançamento de produtos). "
            "Cada item: 1 frase com o ano entre parênteses no final. "
            "Ex: 'Captou Série C de US$ 80M (2024)'."
        ),
        max_length=5,
    )
    awards_recognitions: Optional[list[str]] = Field(
        default=None,
        description=(
            "Até 3 prêmios ou reconhecimentos públicos relevantes. "
            "Cada item: nome do prêmio + ano. "
            "Ex: 'Great Place to Work Brasil (2024)'."
        ),
        max_length=3,
    )


# ============================================================================
# Sub-modelo: Lead
# ============================================================================
class LeadProfile(BaseModel):
    """Perfil do contato da empresa."""

    current_role_summary: str = Field(
        description=(
            "2 a 3 frases sobre as responsabilidades típicas desse cargo "
            "NESSA empresa específica (não definição genérica do cargo). "
            "Foque no que essa pessoa provavelmente faz no dia a dia."
        ),
        max_length=600,
    )
    tenure_at_company: Optional[str] = Field(
        default=None,
        description=(
            "Tempo de casa e trajetória interna, se identificável. "
            "Ex: 'no cargo desde 2019', 'ingressou em 2022 como Coordenador, "
            "promovido a Gerente em 2024'. Use null se não encontrar."
        ),
        max_length=300,
    )
    professional_background: Optional[str] = Field(
        default=None,
        description=(
            "Trajetória profissional anterior em 2 a 3 frases: "
            "empresas anteriores relevantes, formação, especializações. "
            "Use null se não encontrar informação confiável."
        ),
        max_length=500,
    )
    notable_public_signals: Optional[list[str]] = Field(
        default=None,
        description=(
            "Até 3 sinais públicos da pessoa: posts de LinkedIn em destaque, "
            "palestras, entrevistas, artigos publicados. "
            "Cada item: tema + onde apareceu. "
            "Ex: 'Palestra sobre liderança remota no RD Summit 2024'. "
            "Use null se não encontrar."
        ),
        max_length=3,
    )


# ============================================================================
# Sub-modelo: Setor
# ============================================================================
class IndustryContext(BaseModel):
    """Contexto factual do setor de atuação da empresa."""

    current_trends: str = Field(
        description=(
            "3 a 5 frases sobre as forças que estão moldando o setor agora: "
            "regulamentação, tecnologia, mudança de comportamento do consumidor, "
            "movimentos competitivos. Seja específico ao setor, evite truísmos "
            "como 'transformação digital'."
        ),
        max_length=900,
    )
    common_challenges: Optional[list[str]] = Field(
        default=None,
        description=(
            "Até 5 dores recorrentes que empresas desse setor enfrentam. "
            "Cada item: 1 frase específica. NÃO mencione soluções, "
            "apenas o problema. Use null se não conseguir identificar."
        ),
        max_length=5,
    )
    direct_competitors: Optional[list[str]] = Field(
        default=None,
        description=(
            "Até 5 concorrentes diretos da empresa-lead no mesmo segmento. "
            "Apenas o nome de cada concorrente. Use null se não encontrar."
        ),
        max_length=5,
    )
    recent_sector_news: Optional[list[str]] = Field(
        default=None,
        description=(
            "Até 3 notícias dos últimos 6-12 meses que impactam o setor "
            "(não a empresa especificamente). Cada item: headline + 1 frase "
            "explicando por que importa. Use null se não encontrar."
        ),
        max_length=3,
    )


# ============================================================================
# Output principal: WarmerBriefing
# ============================================================================
class WarmerBriefing(BaseModel):
    """
    Briefing completo pré-AT no estilo Warmer da metodologia Challenger.

    Não inclui mais 'potential_angles' (mapeamento Poli Júnior) — essa
    identificação fica a cargo do Hunter durante a reunião, com base no
    contexto fornecido aqui.
    """

    tldr: str = Field(
        description=(
            "Resumo executivo de 2 a 3 frases, voltado para o Hunter ler "
            "em segundos antes da reunião. Deve conter o essencial: "
            "o que a empresa faz, o cargo do contato e UM gancho relevante "
            "(notícia recente, marco, ou contexto de setor). "
            "Esse é o primeiro campo que o Hunter vê."
        ),
        max_length=500,
    )
    company: CompanyProfile = Field(description="Perfil da empresa-lead.")
    lead: LeadProfile = Field(description="Perfil do contato.")
    industry: IndustryContext = Field(description="Contexto do setor de atuação.")
    recent_company_news: Optional[list[str]] = Field(
        default=None,
        description=(
            "Até 5 notícias dos últimos 6-12 meses ESPECIFICAMENTE sobre a empresa "
            "(distintas das notícias de setor). Cada item: data aproximada + "
            "headline + 1 frase de contexto. "
            "Ex: 'Mar/2025: Empresa anuncia parceria com X para expansão no Nordeste'. "
            "Use null se não encontrar."
        ),
        max_length=5,
    )


# ============================================================================
# Output enriquecido: WarmerBriefing + cases relacionados
# ============================================================================
# Esses dois schemas são a saída da integração Cases × Deep Research
# (ver PLANEJAMENTO_INTEGRACAO_SETOR.md, seção 3.4). Ficam neste arquivo —
# e não em src/schemas/case.py — porque o "container" final é centrado
# no briefing; o bloco de cases é um anexo desse briefing.
class CasesParaBriefing(BaseModel):
    """
    Bloco de cases anexado a um WarmerBriefing.

    Duas listas separadas (em vez de uma só) por decisão deliberada
    (planejamento 3.5): cases por setor e cases por relação (parceiro
    /concorrente) carregam significados distintos para o Hunter, e um
    mesmo case pode aparecer nas duas — explicitamente — quando, por
    exemplo, um parceiro do lead também atua no mesmo setor. Não dedupar
    entre as listas é proposital: o Hunter precisa ver o duplo sinal.
    """

    por_setor: list[CaseResultado] = Field(
        description=(
            "Cases recuperados pelo setor primário do lead OU por setores "
            "considerados semanticamente próximos (acima do threshold de "
            "similaridade). Já dedupados por id pelo orquestrador."
        ),
    )
    por_relacao: list[CaseResultado] = Field(
        description=(
            "Cases cujo cliente bate (case+accent insensitive) com algum "
            "nome listado em `briefing.company.notable_partners_or_clients` "
            "ou `briefing.industry.direct_competitors`."
        ),
    )
    setores_consultados: list[str] = Field(
        description=(
            "Lista de setores que passaram do threshold de similaridade e "
            "alimentaram a busca em `por_setor`. Inclui o setor primário do "
            "lead. Serve como trilha de auditoria para diagnosticar quando "
            "um case 'óbvio' não aparecer ou um case 'errado' aparecer."
        ),
    )


class BriefingComCases(BaseModel):
    """
    Saída final do `run_warmer(lead)`: o briefing original + cases anexos.

    Mantemos as duas peças desacopladas (briefing vs. cases) em vez de
    achatar tudo num único objeto. Isso preserva a possibilidade de
    serializar só o briefing (compatibilidade com consumidores que ainda
    não conhecem cases) e simplifica o raciocínio sobre o schema.
    """

    briefing: WarmerBriefing = Field(
        description="Briefing factual produzido pelo deep_research.",
    )
    cases: CasesParaBriefing = Field(
        description="Cases da Poli Júnior relacionados ao lead.",
    )