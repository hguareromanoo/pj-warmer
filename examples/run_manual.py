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

from src.retrieval.deep_research import run_deep_research
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
        org_name="Movile",
        org_setor="Tecnologia / Mobile",
        person_name="João Silva",
        person_position="Diretor de Operações",
    )
    # ↑↑↑ Edite aqui para testar outros leads ↑↑↑

    print(f"\n🔍 Pesquisando lead: {lead.org_name}...\n")
    print("(isso pode levar de 30s a 2min, dependendo da quantidade de buscas)\n")

    # Geração do briefing — única chamada que custa API
    briefing = await run_deep_research(lead)

    # 1) Renderização Markdown no terminal (consumo humano)
    markdown = format_as_markdown(briefing, lead)
    print("\n" + "=" * 70)
    print(markdown)
    print("=" * 70 + "\n")

    # 2) Renderização JSON em arquivo (consumo por integrações)
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


if __name__ == "__main__":
    asyncio.run(main())