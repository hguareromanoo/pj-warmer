"""
Script para rodar o Warmer Briefing MANUALMENTE e ver o resultado.

Como usar (a partir da raiz do projeto pj-warmer):
    1. Garanta que o `.env` está preenchido (copie de `.env.example`).
    2. Instale as dependências: `pip install -r requirements.txt`
    3. Execute: `python -m examples.run_manual`

O script:
  - Imprime o briefing formatado em Markdown no terminal (visualização)
  - Salva o payload JSON em `examples/output/<empresa>_<timestamp>.json`
    para você inspecionar a estrutura que vai pra integração com
    Pipedrive/banco

Você pode editar os dados do lead na função main() para testar com
casos diferentes.
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path

from src.agents.warmer_orchestrator import run_warmer
from src.retrieval.formatters import format_as_markdown, to_json_payload
from src.schemas.research import LeadInput

# Liga os logs para você ver o que está acontecendo durante a busca.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)


def _slugify(text: str) -> str:
    """Converte 'Acme Logística' em 'acme-logistica' para nome de arquivo."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


async def main() -> None:
    # ↓↓↓ Edite aqui para testar outros leads ↓↓↓
    lead = LeadInput(
        org_name="Nestle",
        org_setor="Alimentos e bebidas",
        person_name="Brunno Ragonha",
        person_position="Diretor de Data Science & Analytics",
    )
    # ↑↑↑ Edite aqui para testar outros leads ↑↑↑

    print(f"\n🔍 Pesquisando lead: {lead.org_name}...\n")
    print("(isso pode levar de 30s a 2min, dependendo da quantidade de buscas)\n")

    # Geração do briefing + cases relacionados — única chamada que custa API.
    # `run_warmer` orquestra `run_deep_research` (web + LLM) e o
    # `case_matcher` (DB + cosseno em memória), devolvendo um BriefingComCases.
    result = await run_warmer(lead)
    briefing = result.briefing
    cases = result.cases

    # 1) Renderização Markdown no terminal (consumo humano).
    #    Por decisão (PLANEJAMENTO_INTEGRACAO_SETOR.md, seção 6), o markdown
    #    ainda NÃO inclui cases — esse layout fica para quando o slash command
    #    `/briefing` for implementado. Aqui imprimimos os cases de forma crua
    #    em um bloco separado mais abaixo.
    markdown = format_as_markdown(briefing, lead)
    print("\n" + "=" * 70)
    print(markdown)
    print("=" * 70 + "\n")

    # 2) Renderização JSON em arquivo (consumo por integrações).
    #    Idem: `to_json_payload` não recebe cases nesta sessão (decisão 6).
    #    O JSON salvo permanece compatível com consumidores que ainda só
    #    conhecem o briefing.
    payload = to_json_payload(briefing, lead)
    output_dir = Path(__file__).parent / "output"
    output_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    filename = f"{_slugify(lead.org_name)}_{timestamp}.json"
    output_path = output_dir / filename
    output_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"📦 Payload JSON salvo em: {output_path}")
    print(
        "   (esse é o formato que vai pra banco / Pipedrive / outras integrações)\n"
    )

    # 3) Bloco cru de cases — só para inspeção manual desta fase.
    #    NÃO vai para arquivo nem para o markdown; o objetivo é validar
    #    visualmente a Lane A (setor) e a Lane B (relação) e calibrar o
    #    threshold de similaridade contra cenários reais.
    print("📂 Cases relacionados (não persistidos ainda)\n")

    print(f"   Setores consultados ({len(cases.setores_consultados)}):")
    for setor in cases.setores_consultados:
        print(f"     - {setor}")

    print(f"\n   por_setor ({len(cases.por_setor)}):")
    for c in cases.por_setor:
        print(
            f"     - [{c.id}] {c.cliente} | {c.setor_empresa} "
            f"| {c.area_pj} / {c.servico_pj}"
        )

    print(f"\n   por_relacao ({len(cases.por_relacao)}):")
    for c in cases.por_relacao:
        print(f"     - [{c.id}] {c.cliente} | {c.setor_empresa}")

    print()


if __name__ == "__main__":
    asyncio.run(main())