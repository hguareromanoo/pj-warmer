"""create casepj and casepj_embedding tables

Revision ID: a1b2c3d4e5f6
Revises:
Create Date: 2026-04-30 17:00:00.000000

Primeira migração do projeto. Cria a infraestrutura do módulo de busca de
cases conforme a seção 3.4 do PLANEJAMENTO_BUSCA_CASES.md:

  1. Habilita a extensão pgvector no banco (necessária para o tipo `vector`).
  2. Cria a tabela `casepj` (dados de negócio dos cases da Poli Júnior).
  3. Cria a tabela `casepj_embedding` (vetores paralelos, em tabela separada).
  4. Cria um índice HNSW na coluna `embedding` para acelerar busca por
     similaridade de cosseno quando a base crescer.

A migração é escrita à mão (não via --autogenerate) porque autogenerate não
sabe gerar `CREATE EXTENSION` nem índices HNSW com `postgresql_ops`.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

# Identificadores do encadeamento de migrações.
# `revision` é a "versão" que esta migração estabelece.
# `down_revision = None` indica que esta é a primeira — não há nada antes dela.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Mantemos a dimensão do vetor em sincronia com `src/db/models.py`.
# Hardcoded aqui (em vez de `from src.db.models import EMBEDDING_DIM`) porque
# migrações devem ser auto-contidas: se o código de modelos mudar amanhã,
# uma migração antiga ainda precisa rodar exatamente como foi escrita hoje.
EMBEDDING_DIM = 768


def upgrade() -> None:
    """Aplica a migração: cria extensão, tabelas e índice."""

    # -------------------------------------------------------------------------
    # 1) Habilitar a extensão pgvector
    # -------------------------------------------------------------------------
    # Sem isso, o tipo `vector(N)` não existe e os comandos abaixo falham.
    # `IF NOT EXISTS` torna a operação idempotente — se a extensão já estiver
    # habilitada (manualmente, pelo painel do Supabase), não dá erro.
    #
    # No Supabase, pgvector já vem instalado mas não habilitado por padrão.
    # Esta linha é o que liga o interruptor.
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # -------------------------------------------------------------------------
    # 2) Tabela casepj — dados de negócio
    # -------------------------------------------------------------------------
    op.create_table(
        "casepj",
        # id auto-incrementado (SERIAL no Postgres).
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        # Identificação do cliente. Ex: "Hypera Pharma".
        sa.Column("cliente", sa.String(), nullable=False),
        # Setor primário. Ex: "Farmacêutica".
        sa.Column("setor_empresa", sa.String(), nullable=False),
        # Setores associados como ARRAY(TEXT). Esta é a mudança chave em
        # relação ao SQLite atual: lá era uma string concatenada por vírgula
        # ("Saúde, Varejo"), agora é um array nativo (['Saúde', 'Varejo'])
        # que permite filtros exatos com `'Saúde' = ANY(setores_associados)`.
        sa.Column(
            "setores_associados",
            postgresql.ARRAY(sa.Text()),
            nullable=True,
        ),
        # Área da PJ que executou o projeto. Ex: "Ciência de Dados".
        sa.Column("area_pj", sa.String(), nullable=False),
        # Serviço específico entregue. Ex: "Análise Preditiva".
        sa.Column("servico_pj", sa.String(), nullable=False),
        # Campos descritivos longos. TEXT (sem tamanho fixo) é o tipo certo
        # no Postgres para strings de tamanho variável e potencialmente longas.
        sa.Column("problema", sa.Text(), nullable=False),
        sa.Column("solucao", sa.Text(), nullable=False),
        sa.Column("impacto_roi", sa.Text(), nullable=False),
    )

    # -------------------------------------------------------------------------
    # 3) Tabela casepj_embedding — vetores em tabela separada
    # -------------------------------------------------------------------------
    op.create_table(
        "casepj_embedding",
        # FK para casepj.id. ON DELETE CASCADE: se um case for removido, seus
        # embeddings somem junto. Embeddings órfãos são lixo.
        sa.Column(
            "case_id",
            sa.Integer(),
            sa.ForeignKey("casepj.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Versão do modelo que gerou este embedding. Ex: "gemini-text-embedding-004".
        # Faz parte da PK composta — assim é possível ter, para o mesmo case,
        # um embedding de cada modelo coexistindo durante uma migração de modelo.
        sa.Column("model_version", sa.String(), nullable=False),
        # O vetor propriamente dito. `Vector(N)` mapeia para `vector(N)` do pgvector.
        sa.Column("embedding", Vector(EMBEDDING_DIM), nullable=False),
        # Texto exato que gerou o embedding. Guardado para debug e para
        # comparação rápida ("o source_text mudou? então re-embeda").
        sa.Column("source_text", sa.Text(), nullable=False),
        # Timestamp da geração. server_default=now() faz o Postgres preencher
        # automaticamente se o INSERT não passar o campo.
        sa.Column(
            "updated_at",
            postgresql.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # PK composta — uma linha por (case, modelo).
        sa.PrimaryKeyConstraint("case_id", "model_version", name="pk_casepj_embedding"),
    )

    # -------------------------------------------------------------------------
    # 4) Índice HNSW para busca por similaridade de cosseno
    # -------------------------------------------------------------------------
    # HNSW (Hierarchical Navigable Small World) é o algoritmo de indexação
    # vetorial recomendado pelo pgvector para busca por aproximação rápida.
    # `vector_cosine_ops` é a "operator class" que diz ao índice qual métrica
    # de distância usar — no nosso caso, distância por cosseno (operador <=>).
    #
    # Com 24 registros isso é overkill, mas o índice é barato de manter
    # quando a tabela é pequena, e estar pronto para crescer evita uma
    # migração futura.
    op.execute(
        "CREATE INDEX ix_casepj_embedding_hnsw "
        "ON casepj_embedding "
        "USING hnsw (embedding vector_cosine_ops)"
    )


def downgrade() -> None:
    """
    Reverte a migração: dropa índice, tabelas e (opcionalmente) a extensão.

    Ordem importa: índices e FKs precisam sair antes da tabela que referenciam.
    """
    # 1) Índice (sai antes da tabela; dropar a tabela já dropa o índice junto,
    #    mas explicitar deixa o downgrade claro).
    op.execute("DROP INDEX IF EXISTS ix_casepj_embedding_hnsw")

    # 2) Tabela de embeddings (filha — tem a FK).
    op.drop_table("casepj_embedding")

    # 3) Tabela de cases (pai).
    op.drop_table("casepj")

    # NÃO removemos a extensão `vector`: outras partes do banco (ou outros
    # projetos compartilhando o Supabase) podem estar usando-a. Remover
    # extensões compartilhadas em downgrade é uma forma de quebrar coisas
    # em que o downgrade não tinha culpa.
