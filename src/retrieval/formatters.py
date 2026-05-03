"""
Renderizadores do Warmer Briefing.

Funções puras que convertem um WarmerBriefing em formatos consumíveis
por diferentes canais:

  - `format_as_markdown(briefing)` → string Markdown para exibição no chat,
    com TL;DR em destaque no topo e seções hierárquicas.

  - `to_json_payload(briefing)` → dict serializável (JSON-ready), pronto
    para ser salvo no banco, enviado para a API do Pipedrive como nota
    estruturada, ou consumido por outros agentes/serviços.

Princípio de design: separação de "geração" (em `deep_research.py`) e
"apresentação" (aqui). A função `run_deep_research()` devolve o objeto
estruturado; quem decide COMO mostrar é a camada de UI/integração.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.schemas.research import LeadInput, WarmerBriefing

# Versão do schema. Incrementar quando mudar a estrutura do WarmerBriefing
# de forma incompatível (campos removidos, renomeados, ou tipo alterado).
# Adicionar campos novos opcionais NÃO precisa bump de versão.
SCHEMA_VERSION = "1.0.0"


# ============================================================================
# Renderizador Markdown (para exibição no chat)
# ============================================================================
def format_as_markdown(briefing: WarmerBriefing, lead: LeadInput) -> str:
    """
    Converte o WarmerBriefing em Markdown bonito para exibir no chat.

    Layout:
      1. Cabeçalho com nome da empresa
      2. TL;DR em blockquote (destaque visual)
      3. Seção "Empresa" (perfil + marcos + parceiros)
      4. Seção "Lead" (cargo + trajetória + sinais públicos)
      5. Seção "Setor" (tendências + dores + concorrentes + notícias do setor)
      6. Seção "Notícias da empresa" (se houver)

    Campos opcionais com valor None são silenciosamente omitidos.
    """
    parts: list[str] = []

    # --- Cabeçalho + TL;DR ---
    parts.append(f"# 🔥 Warmer Briefing — {lead.org_name}\n")
    parts.append("> **TL;DR**\n>")
    # Blockquote multi-linha: cada linha precisa começar com `> `
    tldr_lines = briefing.tldr.strip().split("\n")
    for line in tldr_lines:
        parts.append(f"> {line}")
    parts.append("")  # linha em branco depois do blockquote

    # --- Seção Empresa ---
    parts.append("## 🏢 Empresa")
    c = briefing.company
    parts.append(f"**O que faz:** {c.description}\n")

    facts: list[str] = []
    if c.founded_year is not None:
        facts.append(f"**Fundada em:** {c.founded_year}")
    if c.size_indicator:
        facts.append(f"**Porte:** {c.size_indicator}")
    if facts:
        parts.append("  \n".join(facts))  # 2 espaços + \n = quebra de linha em MD
        parts.append("")

    if c.notable_partners_or_clients:
        parts.append("**Parceiros / clientes notáveis:**")
        for item in c.notable_partners_or_clients:
            parts.append(f"- {item}")
        parts.append("")

    if c.recent_milestones:
        parts.append("**Marcos recentes:**")
        for item in c.recent_milestones:
            parts.append(f"- {item}")
        parts.append("")

    if c.awards_recognitions:
        parts.append("**Prêmios e reconhecimentos:**")
        for item in c.awards_recognitions:
            parts.append(f"- {item}")
        parts.append("")

    # --- Seção Lead ---
    parts.append(f"## 👤 Lead — {lead.person_name} ({lead.person_position})")
    p = briefing.lead
    parts.append(f"**No cargo, provavelmente:** {p.current_role_summary}\n")

    if p.tenure_at_company:
        parts.append(f"**Tempo de casa / trajetória interna:** {p.tenure_at_company}\n")

    if p.professional_background:
        parts.append(f"**Bagagem prévia:** {p.professional_background}\n")

    if p.notable_public_signals:
        parts.append("**Sinais públicos:**")
        for item in p.notable_public_signals:
            parts.append(f"- {item}")
        parts.append("")

    # --- Seção Setor ---
    parts.append(f"## 🏭 Setor — {lead.org_setor}")
    i = briefing.industry
    parts.append(f"**Tendências atuais:** {i.current_trends}\n")

    if i.common_challenges:
        parts.append("**Dores comuns no setor:**")
        for item in i.common_challenges:
            parts.append(f"- {item}")
        parts.append("")

    if i.direct_competitors:
        parts.append("**Concorrentes diretos:**")
        for item in i.direct_competitors:
            parts.append(f"- {item}")
        parts.append("")

    if i.recent_sector_news:
        parts.append("**Notícias relevantes do setor:**")
        for item in i.recent_sector_news:
            parts.append(f"- {item}")
        parts.append("")

    # --- Notícias da empresa ---
    if briefing.recent_company_news:
        parts.append("## 📰 Notícias recentes da empresa")
        for item in briefing.recent_company_news:
            parts.append(f"- {item}")
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


# ============================================================================
# Renderizador JSON (para integrações: banco, Pipedrive, outras APIs)
# ============================================================================
def to_json_payload(briefing: WarmerBriefing, lead: LeadInput) -> dict[str, Any]:
    """
    Converte o WarmerBriefing em dict JSON-serializável, com metadata.

    Estrutura do payload:
        {
            "schema_version": "1.0.0",
            "generated_at": "2026-04-29T15:23:00+00:00",
            "lead": { ... dados do input ... },
            "briefing": { ... estrutura completa do WarmerBriefing ... }
        }

    Campos opcionais com valor None aparecem como `null` no JSON
    (preservados, ao contrário do Markdown que os omite). Isso é proposital:
    quem consome JSON precisa saber a diferença entre "não pesquisado"
    e "pesquisado e não encontrado".

    O dict retornado pode ser passado direto para `json.dumps()`,
    para ORMs (SQLAlchemy/SQLModel aceitam dict em colunas JSON),
    ou para clientes HTTP (httpx, requests) como `json=payload`.
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lead": lead.model_dump(),
        "briefing": briefing.model_dump(),
    }