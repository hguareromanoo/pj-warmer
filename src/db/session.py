"""
Helper de conexão com o banco.

Centraliza a criação do engine SQLAlchemy num lugar só. Sem isso, o
seed e o retrieval iriam (cada um) chamar `create_engine(...)` com a
mesma URL, o que é (a) repetição inútil e (b) duplica connection pools
no mesmo processo.

Uso:

    from src.db.session import get_engine
    from sqlmodel import Session

    with Session(get_engine()) as session:
        ...
"""

from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from src.utils.config import get_settings


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """
    Retorna uma instância única (cached) do Engine SQLAlchemy.

    O Engine carrega connection pool internamente, então criar mais de um
    no mesmo processo é desperdício. lru_cache(1) garante singleton.
    """
    settings = get_settings()
    return create_engine(settings.database_url)
