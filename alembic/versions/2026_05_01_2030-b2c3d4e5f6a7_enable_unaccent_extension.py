"""enable unaccent extension

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-05-01 20:30:00.000000

Habilita a extensão `unaccent` do Postgres. Ela vira a função
`unaccent(text)` disponível no SQL — usada na camada de retrieval
(`src/retrieval/cases.py`) para tornar os filtros determinísticos
case-insensitive E accent-insensitive ao mesmo tempo.

Depois desta migração, queries como:

    WHERE lower(unaccent(setor_empresa)) = lower(unaccent(:setor))

passam a casar "Serviços" com "servicos", "Saúde" com "saude", etc.
"""
from typing import Sequence, Union

from alembic import op

# Identificadores do encadeamento de migrações.
revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Habilita a extensão unaccent no banco."""
    # `IF NOT EXISTS` torna a operação idempotente — se já estiver
    # habilitada (manualmente ou por outra migração), não dá erro.
    op.execute("CREATE EXTENSION IF NOT EXISTS unaccent")


def downgrade() -> None:
    """
    Reverte: remove a extensão.

    Atenção: isso só funciona se nenhum índice/view/coluna estiver
    usando `unaccent()`. Se algo depender, o downgrade vai falhar
    com erro claro do Postgres — preferível a quebrar dados em silêncio.
    """
    op.execute("DROP EXTENSION IF EXISTS unaccent")
