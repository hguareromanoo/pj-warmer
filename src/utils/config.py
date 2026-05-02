"""
Carregamento centralizado de configuração.

Em vez de chamar `load_dotenv()` e `os.getenv("X")` espalhados pelo código,
centralizamos tudo aqui. Vantagens:

1. Falha rápido: se faltar uma variável de ambiente, descobrimos na hora
   em que a app sobe — não no meio de uma chamada de produção.
2. Tipagem: as variáveis viram atributos com tipos garantidos pelo Pydantic.
3. Testabilidade: nos testes podemos sobrescrever facilmente.
"""

from datetime import date
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Variáveis de ambiente do projeto."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",  # ignora variáveis no .env que não sejam definidas aqui
    )

    # Chaves de API
    tavily_api_key: str = Field(description="Chave da API do Tavily (busca web).")
    gemini_api_key: str = Field(description="Chave da API do Google Gemini (LLM).")

    # Modelo padrão do LLM
    gemini_model: str = Field(
        default="gemini-2.5-flash",
        description="Modelo Gemini a ser usado pelo agente de pesquisa.",
    )

    # Banco de dados (Supabase Postgres). Mesma URL usada pelo Alembic.
    database_url: str = Field(description="String de conexão Postgres do Supabase.")

    # Embeddings — provedor + modelo
    # `embeddings_provider` é o ponto de extensão para o fallback OpenAI
    # mencionado na seção 3.2 do PLANEJAMENTO_BUSCA_CASES.md. Hoje só "gemini"
    # é suportado; trocar é uma única env var quando precisar.
    embeddings_provider: str = Field(
        default="gemini",
        description="Provedor de embeddings ('gemini' ou 'openai').",
    )
    gemini_embedding_model: str = Field(
        default="gemini-embedding-001",
        description=(
            "Modelo Gemini de embeddings. O 'gemini-embedding-001' é Matryoshka "
            "(saída padrão 3072 dims, suporta 768 via output_dimensionality)."
        ),
    )

    # Parâmetros da busca
    tavily_max_results: int = Field(
        default=3,
        description="Quantidade máxima de resultados retornados por busca no Tavily.",
    )
    tavily_search_depth: str = Field(
        default="advanced",
        description="Profundidade da busca no Tavily ('basic' ou 'advanced').",
    )


@lru_cache
def get_settings() -> Settings:
    """
    Retorna uma instância única (cached) de Settings.

    Usar lru_cache garante que o `.env` é lido uma vez só por processo,
    mesmo que `get_settings()` seja chamado dezenas de vezes.
    """
    return Settings()  # type: ignore[call-arg]


def get_current_date_str() -> str:
    """
    Retorna a data de hoje em formato legível para humanos.

    Usado para injetar a data atual em prompts de LLM, evitando que
    o modelo opere "no escuro temporal" (LLMs têm knowledge cutoff
    e tendem a achar que ainda é o ano em que foram treinados).

    Ex: '28 de abril de 2026'
    """
    today = date.today()
    meses_pt = [
        "janeiro", "fevereiro", "março", "abril", "maio", "junho",
        "julho", "agosto", "setembro", "outubro", "novembro", "dezembro",
    ]
    return f"{today.day} de {meses_pt[today.month - 1]} de {today.year}"