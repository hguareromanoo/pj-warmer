"""
Modelos SQLModel da camada de persistência.

Este módulo define o schema do banco em código Python. SQLModel é uma
biblioteca que casa SQLAlchemy (ORM) com Pydantic (validação): cada
classe aqui é, ao mesmo tempo, uma tabela do Postgres e um schema de
validação.

Por que ter o schema em código?
- O Alembic compara o que está aqui com o estado real do banco para
  gerar/conferir migrações.
- O resto da aplicação (retrieval, agentes, scripts de seed) importa
  estas classes para ler/escrever de forma tipada — sem SQL espalhado.

Tabelas definidas:
- CasePJ              -> dados de negócio dos cases da Poli Júnior
- CasePJEmbedding     -> embeddings vetoriais paralelos a CasePJ (RAG)

A separação em duas tabelas é proposital. Ver seção 3.4 do
PLANEJAMENTO_BUSCA_CASES.md para o racional.
"""

from datetime import datetime
from typing import List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import Column, ForeignKey, Index, Text
from sqlalchemy.dialects.postgresql import ARRAY, TIMESTAMP
from sqlmodel import Field, SQLModel


# Dimensionalidade do vetor de embedding. Mantida aqui como constante porque
# aparece em três lugares: (1) declaração da coluna `vector(N)` abaixo,
# (2) migração Alembic, (3) wrapper de embeddings em services/. Centralizar
# evita que esses três lugares saiam de sincronia.
#
# 768 = output do `text-embedding-004` do Gemini.
# Se um dia trocarmos para o `text-embedding-3-small` da OpenAI (1536),
# muda-se aqui e gera-se uma migração nova.
EMBEDDING_DIM = 768


class CasePJ(SQLModel, table=True):
    """
    Case da Poli Júnior — uma linha por projeto entregue.

    Mesma estrutura semântica da tabela `casepj` no SQLite atual, com uma
    diferença chave: `setores_associados` agora é um array nativo do Postgres
    (TEXT[]), não mais uma string concatenada por vírgula. Isso permite
    filtros exatos do tipo `WHERE 'Saúde' = ANY(setores_associados)` sem
    falsos positivos como "Saúde Animal" matchando "Saúde".
    """

    # Nome explícito da tabela. Sem isso, o SQLModel deriva de `CasePJ` -> `casepj`,
    # que coincidentemente é o que queremos — mas deixar explícito documenta
    # a intenção e protege contra refatorações de nome de classe.
    __tablename__ = "casepj"

    # Chave primária. `default=None` + `primary_key=True` é o padrão do SQLModel
    # para um id auto-gerado pelo Postgres (SERIAL).
    id: Optional[int] = Field(default=None, primary_key=True)

    # Campos de identificação do case.
    cliente: str = Field(nullable=False, description="Nome do cliente. Ex: 'Hypera Pharma'.")
    setor_empresa: str = Field(
        nullable=False,
        description="Setor primário da empresa cliente. Ex: 'Farmacêutica'.",
    )

    # `setores_associados` é uma lista de strings — outros setores onde a
    # solução do case também é relevante. Modelado como ARRAY(TEXT) do Postgres.
    #
    # Por que `sa_column=Column(...)` em vez de só `Field(...)`? Porque o
    # SQLModel não tem suporte de primeira classe para ARRAY do Postgres;
    # então descemos um nível e definimos a coluna direto via SQLAlchemy.
    setores_associados: Optional[List[str]] = Field(
        default=None,
        sa_column=Column(ARRAY(Text), nullable=True),
        description="Outros setores onde o case é aplicável. Ex: ['Saúde', 'Varejo'].",
    )

    # Área da Poli Júnior responsável pelo projeto.
    # No dataset atual há 4 valores: 'Ciência de Dados', 'Inteligência de Negócios',
    # 'Engenharia de Dados', 'IA'. Não viramos enum porque a lista pode crescer.
    area_pj: str = Field(nullable=False, description="Área da PJ que executou o projeto.")
    servico_pj: str = Field(
        nullable=False,
        description="Serviço específico entregue. Ex: 'Análise Preditiva'.",
    )

    # Campos descritivos longos (~300-500 chars). São os "ricos" — o que
    # vai virar embedding. Tipados como Text porque VARCHAR sem tamanho é
    # equivalente no Postgres mas Text deixa a intenção mais explícita.
    problema: str = Field(
        sa_column=Column(Text, nullable=False),
        description="Descrição do problema do cliente que motivou o projeto.",
    )
    solucao: str = Field(
        sa_column=Column(Text, nullable=False),
        description="Descrição da solução entregue pela PJ.",
    )
    impacto_roi: str = Field(
        sa_column=Column(Text, nullable=False),
        description="Resultados quantitativos/qualitativos do projeto.",
    )


class CasePJEmbedding(SQLModel, table=True):
    """
    Embedding vetorial de um CasePJ.

    Tabela separada de `casepj` por três motivos (ver planejamento sec. 3.4):
    1. Permite regenerar embeddings sem mexer nos dados de negócio.
    2. Permite manter múltiplas versões de embedding em paralelo durante
       uma transição de modelo (ex: migrar de Gemini para OpenAI).
    3. Permite deletar e repopular o índice vetorial sem perder os cases.

    A chave primária é composta por (case_id, model_version) — isso enforça
    no banco a regra "no máximo um embedding por (case, modelo)" sem
    impedir que coexistam, por exemplo, um embedding do Gemini e um da
    OpenAI para o mesmo case durante uma transição.
    """

    __tablename__ = "casepj_embedding"

    # FK para casepj.id. ON DELETE CASCADE: se um case for deletado, seus
    # embeddings somem junto. Isso é o comportamento que queremos — um
    # embedding orfão é lixo.
    case_id: int = Field(
        sa_column=Column(
            ForeignKey("casepj.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        description="Referência ao case correspondente.",
    )

    # Identificador da versão do modelo de embedding usado. Faz parte da PK
    # para suportar coexistência de modelos diferentes.
    # Ex: "gemini-text-embedding-004", "openai-text-embedding-3-small".
    model_version: str = Field(
        primary_key=True,
        nullable=False,
        description="Identificador do modelo de embedding usado.",
    )

    # O vetor propriamente dito. `Vector(768)` vem de `pgvector.sqlalchemy`
    # e mapeia para o tipo `vector(768)` do pgvector no Postgres.
    embedding: List[float] = Field(
        sa_column=Column(Vector(EMBEDDING_DIM), nullable=False),
        description=f"Embedding com {EMBEDDING_DIM} dimensões.",
    )

    # Texto exato que foi passado para o modelo de embedding. Guardar isso
    # nos dá: (a) debugging — dá pra ver "o que foi indexado mesmo?",
    # (b) regeneração idempotente — comparando o source_text atual com
    # o que está no banco, dá pra decidir se precisa re-embedar.
    source_text: str = Field(
        sa_column=Column(Text, nullable=False),
        description="Texto que gerou o embedding (para debug/regeneração).",
    )

    # Quando o embedding foi gerado/atualizado pela última vez. Útil para
    # decidir se um embedding está desatualizado (ex: o problema do case
    # mudou depois disso).
    updated_at: datetime = Field(
        sa_column=Column(
            TIMESTAMP(timezone=True),
            nullable=False,
            server_default=None,  # default vem na migração via `now()`
        ),
        description="Timestamp da última geração/atualização do embedding.",
    )


# -----------------------------------------------------------------------------
# Índice HNSW para busca vetorial por similaridade de cosseno
# -----------------------------------------------------------------------------
# Com 24 cases o índice é dispensável — uma varredura completa é rápida.
# Mas declaramos desde já porque:
# 1. O custo é zero quando a tabela é pequena.
# 2. Quando a base crescer, não vamos precisar pensar nisso de novo.
# 3. O vector_cosine_ops é o que casa com a métrica de similaridade que
#    vamos usar nas queries (`<=>` no pgvector).
#
# `postgresql_using="hnsw"` instrui o Postgres a criar índice HNSW (em vez
# de B-tree padrão). `postgresql_ops` mapeia coluna -> operator class.
Index(
    "ix_casepj_embedding_hnsw",
    CasePJEmbedding.__table__.c.embedding,
    postgresql_using="hnsw",
    postgresql_ops={"embedding": "vector_cosine_ops"},
)
