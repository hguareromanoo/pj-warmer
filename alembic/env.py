"""
Configuração de ambiente do Alembic.

Este arquivo é executado pelo Alembic toda vez que você roda um comando
(como `alembic upgrade head` ou `alembic revision`). A função dele é:

1. Carregar a URL do banco a partir do `.env` (em vez de deixar a senha
   exposta no `alembic.ini`).
2. Apontar para a `metadata` dos modelos SQLModel — isso permite que o
   Alembic compare o estado do banco com o que está declarado no código
   e (no futuro) gere migrações automáticas.
3. Rodar as migrações em modo "online" (com conexão real) ou "offline"
   (gerando SQL em arquivo).

Para um projeto pequeno como o nosso, o que importa é o modo online: ele
é o que `alembic upgrade head` usa.
"""

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

# Garante que `from src.db.models import ...` funcione quando o Alembic
# roda fora de um contexto de pacote instalado. Sem isso, `import src...`
# falha com ModuleNotFoundError porque o Alembic não herda o pythonpath
# do pytest.ini.
_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

# -----------------------------------------------------------------------------
# 1) Carregar variáveis do .env ANTES de qualquer outra coisa
# -----------------------------------------------------------------------------
# O .env fica na raiz do projeto. Como o Alembic é normalmente chamado a
# partir da raiz (`alembic upgrade head`), basta apontar para "./.env".
#
# Usamos python-dotenv direto aqui (em vez de importar pydantic-settings)
# porque o env.py precisa ser leve e não criar dependência circular com
# o módulo de configuração da aplicação.
from dotenv import load_dotenv
import os

load_dotenv(_PROJECT_ROOT / ".env")

# -----------------------------------------------------------------------------
# 2) Importar a metadata dos modelos
# -----------------------------------------------------------------------------
# `target_metadata` é o que o Alembic usa em modo --autogenerate para
# detectar diferenças entre o schema declarado em código e o schema real
# do banco. Mesmo nas migrações escritas à mão (como a primeira), passar
# isso aqui é boa prática: vira a "fonte da verdade" do schema.
#
# Como usamos SQLModel, a metadata vive em `SQLModel.metadata` e contém
# todas as tabelas que foram importadas no momento em que esta linha roda.
# Por isso importamos `src.db.models` — só de importar, as classes se
# registram na metadata global do SQLModel.
from sqlmodel import SQLModel

import src.db.models  # noqa: F401  (importação com efeito colateral: registra modelos)

target_metadata = SQLModel.metadata

# -----------------------------------------------------------------------------
# 3) Config do Alembic (lido do alembic.ini)
# -----------------------------------------------------------------------------
config = context.config

# Logging: usa a seção [loggers] do alembic.ini.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Sobrescreve o `sqlalchemy.url` placeholder do alembic.ini com a URL real
# vinda do .env. Se DATABASE_URL não estiver definida, o Alembic vai falhar
# com uma mensagem clara — preferível a usar um placeholder silenciosamente.
database_url = os.getenv("DATABASE_URL")
if not database_url:
    raise RuntimeError(
        "DATABASE_URL não está definida no .env. "
        "Pegue em: Supabase -> Project Settings -> Database -> Connection string -> URI."
    )
config.set_main_option("sqlalchemy.url", database_url)


# -----------------------------------------------------------------------------
# 4) Modos de execução: offline e online
# -----------------------------------------------------------------------------
def run_migrations_offline() -> None:
    """
    Modo offline: gera SQL bruto em vez de executar contra o banco.

    Útil para inspecionar o que uma migração vai fazer (`alembic upgrade head --sql`)
    ou para enviar pro DBA aprovar antes de rodar. Em produção a gente roda
    online mesmo, mas o Alembic exige que ambos os modos estejam definidos.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # `compare_type` faz o autogenerate detectar mudanças de tipo (ex: VARCHAR -> TEXT).
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """
    Modo online: cria uma conexão real com o banco e aplica as migrações.

    Esse é o caminho usado por `alembic upgrade head`.
    """
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        # NullPool: cada migração abre/fecha a conexão. Não é hot path, então
        # pool de conexões aqui é desperdício e atrapalha em testes.
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )

        with context.begin_transaction():
            context.run_migrations()


# Despacha para o modo certo dependendo de como o Alembic foi invocado.
if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
