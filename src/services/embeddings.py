"""
Wrapper do provedor de embeddings.

Esta é a única porta de entrada para "transformar texto em vetor" no
projeto. O resto do código (seed de cases, retrieval, etc.) chama
`embed(...)` daqui e não precisa saber qual provedor está atrás.

Por que isolar assim?
- Se o tier gratuito do Gemini ficar limitante, troca-se o provedor com
  uma única variável de ambiente (`EMBEDDINGS_PROVIDER=openai`) e nada
  no resto do código quebra.
- Centraliza tratamento de erro, retries e logs num lugar só.

Implementação atual: Gemini via SDK `google-genai` (o novo, oficial),
modelo `gemini-embedding-001`. Esse modelo é Matryoshka — produz por
padrão um vetor de 3072 dimensões, mas suporta dimensões menores
(ex: 768) via `output_dimensionality` sem perda significativa de
qualidade. Pedimos 768 para casar com o schema do banco.
"""

import math
from typing import Literal

from google import genai
from google.genai import types

from src.db.models import EMBEDDING_DIM
from src.utils.config import get_settings

# Tipos de tarefa suportados pelo Gemini para embeddings.
# A diferença é prática: o modelo "ajusta" o vetor de saída pensando em
# como ele vai ser usado.
#
# - "retrieval_document": para textos que vão FICAR INDEXADOS (cases).
# - "retrieval_query":    para a pergunta de quem está BUSCANDO no índice.
#
# Usar o tipo errado não quebra nada, mas degrada um pouco a qualidade
# do retrieval. Por isso a função pede o task_type explicitamente.
TaskType = Literal[
    "retrieval_document",
    "retrieval_query",
    "semantic_similarity",
    "classification",
    "clustering",
]


# Cache do client Gemini (singleton). O Client mantém connection pool
# interno; criar um novo a cada chamada é desperdício de TLS handshake.
_gemini_client: genai.Client | None = None


def _get_gemini_client() -> genai.Client:
    global _gemini_client
    if _gemini_client is None:
        settings = get_settings()
        _gemini_client = genai.Client(api_key=settings.gemini_api_key)
    return _gemini_client


def embed(
    text: str,
    *,
    task_type: TaskType = "retrieval_document",
) -> list[float]:
    """
    Gera o embedding de um texto.

    Parameters
    ----------
    text : str
        O texto a ser embedado. Não pode ser vazio.
    task_type : TaskType, optional
        Pista para o modelo sobre como o vetor vai ser usado. Use
        "retrieval_document" ao indexar (seed) e "retrieval_query" ao
        buscar (retrieval). Default: "retrieval_document".

    Returns
    -------
    list[float]
        Vetor já L2-normalizado (norma 1), com tamanho EMBEDDING_DIM.

    Raises
    ------
    ValueError
        Se `text` for vazio.
    NotImplementedError
        Se EMBEDDINGS_PROVIDER for um provedor ainda não implementado.
    """
    if not text or not text.strip():
        raise ValueError("Texto vazio não pode ser embedado.")

    settings = get_settings()
    provider = settings.embeddings_provider.lower()

    if provider == "gemini":
        return _embed_with_gemini(text, task_type=task_type)

    if provider == "openai":
        # ---------------------------------------------------------------
        # PONTO DE EXTENSÃO — fallback OpenAI
        # ---------------------------------------------------------------
        # Quando precisar:
        # 1. Adicionar `openai` em requirements.txt
        # 2. Adicionar `openai_api_key` em src/utils/config.py
        # 3. Implementar _embed_with_openai com text-embedding-3-small
        # 4. Atenção: 1536 dimensões -> exige migração nova de schema
        #    (Vector(768) -> Vector(1536)) e re-seed de todos os embeddings.
        # ---------------------------------------------------------------
        raise NotImplementedError(
            "Provedor 'openai' ainda não implementado. "
            "Ver comentário no ponto de extensão em src/services/embeddings.py."
        )

    raise NotImplementedError(
        f"Provedor de embeddings desconhecido: '{provider}'. "
        "Valores suportados: 'gemini', 'openai'."
    )


def _embed_with_gemini(text: str, *, task_type: TaskType) -> list[float]:
    """Implementação Gemini de `embed`. Não chame direto — use `embed`."""
    client = _get_gemini_client()
    settings = get_settings()

    # O novo SDK aceita o nome do modelo limpo, sem prefixo "models/".
    # Se o usuário configurou com prefixo, removemos por segurança.
    model_name = settings.gemini_embedding_model.removeprefix("models/")

    response = client.models.embed_content(
        model=model_name,
        contents=text,
        config=types.EmbedContentConfig(
            # O SDK aceita o task_type como string uppercase OU como enum.
            # String é mais portável.
            task_type=task_type.upper(),
            # Pede explicitamente a dimensão que casa com o schema do banco.
            # Sem isso, o modelo Matryoshka retorna 3072 dims e o INSERT
            # quebra com erro do pgvector.
            output_dimensionality=EMBEDDING_DIM,
        ),
    )

    # response.embeddings é uma lista (uma entrada por item de `contents`).
    # Como passamos um único texto, pegamos a primeira.
    raw_vector = response.embeddings[0].values

    # ATENÇÃO: para `gemini-embedding-001`, quando se pede
    # `output_dimensionality` < 3072, a Google recomenda explicitamente
    # L2-normalizar o vetor antes de usar para similaridade de cosseno.
    # A dimensão padrão (3072) já vem normalizada; as menores não.
    # Sem essa normalização, o `<=>` (cosine distance) do pgvector
    # devolve distâncias erradas porque os vetores não estão na esfera unitária.
    return _l2_normalize([float(x) for x in raw_vector])


def _l2_normalize(vector: list[float]) -> list[float]:
    """
    Normaliza o vetor para norma 1 (L2). Necessário para o
    `gemini-embedding-001` quando se usa output_dimensionality < 3072.
    """
    norm = math.sqrt(sum(x * x for x in vector))
    if norm == 0.0:
        # Vetor zero — caso raríssimo, mas evita divisão por zero.
        return vector
    return [x / norm for x in vector]


def model_version_tag() -> str:
    """
    Identificador estável da configuração atual de embedding.

    Vai para a coluna `model_version` em casepj_embedding. Permite manter
    múltiplas versões de embedding por case durante uma transição de modelo.

    Ex: "gemini-gemini-embedding-001-d768"
    """
    settings = get_settings()
    provider = settings.embeddings_provider.lower()
    if provider == "gemini":
        model = settings.gemini_embedding_model.removeprefix("models/")
        # Inclui a dimensionalidade na tag — embeddings com a mesma família
        # de modelo mas dimensões diferentes não são intercambiáveis.
        return f"gemini-{model}-d{EMBEDDING_DIM}"
    if provider == "openai":
        return "openai-text-embedding-3-small"
    return f"{provider}-unknown"
