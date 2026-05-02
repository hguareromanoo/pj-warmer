"""
Seed dos cases da Poli Júnior do SQLite legado para o Supabase.

Como rodar (a partir da raiz do projeto, com a venv ativada):

    python -m src.db.seed_cases                       # usa cases_pj.db na raiz
    python -m src.db.seed_cases --sqlite path/x.db    # path customizado
    python -m src.db.seed_cases --dry-run             # mostra o que faria, sem mexer no banco

Idempotência:
- Cases são upsertados pelo `id` original do SQLite (preserva PK).
- Embeddings são upsertados por (case_id, model_version).
- Se o `source_text` calculado for igual ao que já está no banco, a
  chamada à API do Gemini é PULADA. Isso economiza tokens em re-runs.

Por que está em src/db/ e não em scripts/?
- O planejamento (sec. 4) coloca aqui. `scripts/seed_cases.py` é um stub
  antigo vazio — pode ser deletado quando você quiser.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

# -----------------------------------------------------------------------------
# Permite rodar `python src/db/seed_cases.py` direto (sem -m). Adiciona
# a raiz do projeto ao sys.path antes dos imports `from src...`.
# -----------------------------------------------------------------------------
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlmodel import Session

from src.db.models import EMBEDDING_DIM, CasePJ, CasePJEmbedding
from src.services.embeddings import embed, model_version_tag
from src.utils.config import get_settings


# Default: cases_pj.db na raiz do projeto.
DEFAULT_SQLITE_PATH = _PROJECT_ROOT / "cases_pj.db"


# -----------------------------------------------------------------------------
# Helpers de transformação
# -----------------------------------------------------------------------------
def parse_setores_associados(raw: str | None) -> list[str] | None:
    """
    Converte a string concatenada do SQLite em lista para o Postgres.

    Ex: 'Saúde, Varejo' -> ['Saúde', 'Varejo']
        ''              -> None
        None            -> None
    """
    if raw is None:
        return None
    items = [s.strip() for s in raw.split(",") if s.strip()]
    return items if items else None


def build_source_text(case: dict) -> str:
    """
    Monta o texto rotulado que vai para o modelo de embedding.

    Formato definido na seção 3.3 do PLANEJAMENTO_BUSCA_CASES.md.
    Os rótulos ("Cliente:", "Setor:", etc.) ajudam o modelo a entender
    a função semântica de cada bloco de texto.
    """
    setores_assoc = case["setores_associados"] or []
    setores_str = ", ".join(setores_assoc) if setores_assoc else "(nenhum)"
    return (
        f"Cliente: {case['cliente']}\n"
        f"Setor: {case['setor_empresa']} (associados: {setores_str})\n"
        f"Área: {case['area_pj']} — Serviço: {case['servico_pj']}\n"
        f"Problema: {case['problema']}\n"
        f"Solução: {case['solucao']}\n"
        f"Impacto: {case['impacto_roi']}"
    )


# -----------------------------------------------------------------------------
# Pipeline principal
# -----------------------------------------------------------------------------
def main(sqlite_path: Path, dry_run: bool = False) -> int:
    """
    Roda o seed end-to-end.

    Returns
    -------
    int
        Exit code (0 = sucesso, !=0 = erro).
    """
    settings = get_settings()

    # 1) Ler do SQLite ---------------------------------------------------------
    if not sqlite_path.exists():
        print(f"❌ SQLite não encontrado: {sqlite_path}", file=sys.stderr)
        print(
            "   Coloque o cases_pj.db na raiz do projeto ou passe --sqlite <path>.",
            file=sys.stderr,
        )
        return 1

    conn = sqlite3.connect(str(sqlite_path))
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute("SELECT * FROM casepj").fetchall()]
    conn.close()

    print(f"📥 Lidos {len(rows)} cases de {sqlite_path}")

    if dry_run:
        print("🔍 Modo --dry-run: nada será gravado no Supabase.")

    # 2) Conectar ao Postgres -------------------------------------------------
    engine = create_engine(settings.database_url)
    current_model_version = model_version_tag()
    print(f"🔖 model_version atual: {current_model_version}")

    # Contadores para o relatório final.
    n_cases_inseridos = 0
    n_embeddings_gerados = 0
    n_embeddings_pulados = 0

    with Session(engine) as session:
        for raw in rows:
            # Normaliza setores_associados de string -> array.
            raw["setores_associados"] = parse_setores_associados(
                raw.get("setores_associados")
            )

            # 3) Upsert em casepj ---------------------------------------------
            # Preservamos o `id` do SQLite. ON CONFLICT (id) DO UPDATE garante
            # que reexecutar o seed depois de editar um case no SQLite reflete
            # no Supabase sem duplicar.
            stmt = pg_insert(CasePJ.__table__).values(**raw)
            stmt = stmt.on_conflict_do_update(
                index_elements=["id"],
                set_={
                    col.name: stmt.excluded[col.name]
                    for col in CasePJ.__table__.columns
                    if col.name != "id"
                },
            )
            if not dry_run:
                session.execute(stmt)
            n_cases_inseridos += 1

            # 4) Decidir se precisa gerar embedding novo ----------------------
            source_text = build_source_text(raw)

            existing = session.execute(
                select(CasePJEmbedding).where(
                    CasePJEmbedding.case_id == raw["id"],
                    CasePJEmbedding.model_version == current_model_version,
                )
            ).scalar_one_or_none()

            if existing is not None and existing.source_text == source_text:
                # Idempotência: o texto-fonte não mudou, então o embedding
                # ainda é válido. Pular evita gastar quota da API à toa.
                n_embeddings_pulados += 1
                print(
                    f"  ⏭  Case {raw['id']:>2} ({raw['cliente']}): "
                    f"embedding em dia, pulando."
                )
                continue

            # 5) Gerar embedding ----------------------------------------------
            print(
                f"  🧠 Case {raw['id']:>2} ({raw['cliente']}): "
                f"gerando embedding..."
            )
            if dry_run:
                n_embeddings_gerados += 1
                continue

            vector = embed(source_text, task_type="retrieval_document")

            # Sanidade: o vetor PRECISA ter o tamanho que o schema espera,
            # senão o INSERT falha com erro feio do pgvector.
            if len(vector) != EMBEDDING_DIM:
                print(
                    f"❌ Dimensão inesperada: recebi {len(vector)}, "
                    f"esperava {EMBEDDING_DIM}. Abortando.",
                    file=sys.stderr,
                )
                return 2

            # 6) Upsert em casepj_embedding -----------------------------------
            emb_stmt = pg_insert(CasePJEmbedding.__table__).values(
                case_id=raw["id"],
                model_version=current_model_version,
                embedding=vector,
                source_text=source_text,
                updated_at=datetime.now(timezone.utc),
            )
            emb_stmt = emb_stmt.on_conflict_do_update(
                index_elements=["case_id", "model_version"],
                set_={
                    "embedding": emb_stmt.excluded.embedding,
                    "source_text": emb_stmt.excluded.source_text,
                    "updated_at": emb_stmt.excluded.updated_at,
                },
            )
            session.execute(emb_stmt)
            n_embeddings_gerados += 1

        # 7) Sincroniza a sequence de id em casepj ----------------------------
        # Como inserimos com `id` explícito (vindo do SQLite), o SERIAL do
        # Postgres não avança. Sem isso, o próximo INSERT ORM (sem id)
        # tentaria usar `id=1` e quebraria com PK violation.
        if not dry_run:
            session.execute(
                text(
                    "SELECT setval("
                    "  pg_get_serial_sequence('casepj', 'id'),"
                    "  COALESCE((SELECT MAX(id) FROM casepj), 1),"
                    "  true"
                    ")"
                )
            )
            session.commit()

    # 8) Relatório final ------------------------------------------------------
    print()
    print("=" * 60)
    print("✅ Seed concluído.")
    print(f"   Cases processados:    {n_cases_inseridos}")
    print(f"   Embeddings gerados:   {n_embeddings_gerados}")
    print(f"   Embeddings pulados:   {n_embeddings_pulados} (já em dia)")
    if dry_run:
        print("   (dry-run: nada foi gravado)")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Popula casepj e casepj_embedding no Supabase a partir do SQLite legado."
    )
    parser.add_argument(
        "--sqlite",
        type=Path,
        default=DEFAULT_SQLITE_PATH,
        help=f"Caminho do cases_pj.db (default: {DEFAULT_SQLITE_PATH}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra o que seria feito sem gravar no banco nem chamar a API.",
    )
    args = parser.parse_args()
    sys.exit(main(args.sqlite, args.dry_run))
