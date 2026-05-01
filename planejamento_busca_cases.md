# 📋 Planejamento — Módulo de Busca de Cases

> Documento de arquitetura e decisões do módulo de retrieval do PJ Warmer.
> Escopo: busca determinística + busca semântica (RAG) sobre a base de cases da Poli Júnior.

---

## 1. Contexto

O PJ Warmer precisa, dado um lead novo, recuperar cases anteriores da Poli Júnior que sejam relevantes para o Hunter mencionar na AT. "Relevante" tem dois sentidos diferentes:

- **Mesmo setor / área de atuação** → resposta exata, filtrada por categoria.
- **Situação parecida** → resposta aproximada, baseada na semelhança de problema/solução.

Esses dois sentidos pedem mecanismos diferentes (SQL vs. busca vetorial), mas o sistema precisa entregar uma experiência única ao usuário final no chatbot. O módulo aqui descrito resolve isso com uma camada de retrieval híbrida.

---

## 2. Estado atual da base

Arquivo `cases_pj.db` (SQLite, 24 linhas, tabela `casepj`):

| Campo | Tipo | Observação |
|---|---|---|
| `id` | INTEGER PK | |
| `cliente` | VARCHAR | Ex: "Hypera Pharma" |
| `setor_empresa` | VARCHAR | Setor primário, ex: "Farmacêutica" |
| `setores_associados` | VARCHAR | **Lista em string**, ex: "Saúde, Varejo" — problemático |
| `area_pj` | VARCHAR | Ex: "Ciência de Dados" (4 valores únicos) |
| `servico_pj` | VARCHAR | Ex: "Análise Preditiva" |
| `problema` | VARCHAR | Texto longo (~500 chars) |
| `solucao` | VARCHAR | Texto longo (~500 chars) |
| `impacto_roi` | VARCHAR | Texto longo (~300 chars) |

**Distribuição observada:** 19 setores únicos, 4 áreas PJ (Ciência de Dados, Inteligência de Negócios, Engenharia de Dados, IA). Os campos longos são ricos e descritivos — bons candidatos a embedding.

**Limitação:** `setores_associados` como string concatenada (`"Saúde, Varejo"`) impede busca exata. Buscar `LIKE '%Saúde%'` traria também "Saúde Animal".

---

## 3. Decisões arquiteturais

### 3.1. Banco e infraestrutura

- **Postgres no Supabase** com extensão **pgvector** habilitada.
- **SQLAlchemy + Alembic** para modelar e versionar o schema (já na stack).
- O `cases_pj.db` (SQLite) é tratado como **fonte de seed**, não como produção. Migra-se uma vez para o Supabase e descarta-se.

**Por quê:** Supabase já está na stack do projeto. Pgvector permite filtrar por categoria e ordenar por similaridade vetorial **na mesma query SQL**, que é o que viabiliza a busca híbrida sem complicação.

### 3.2. Embeddings

- **Provedor primário:** Google Gemini, modelo `text-embedding-004` (768 dimensões, free tier generoso).
- **Fallback:** OpenAI `text-embedding-3-small` (1536 dimensões), caso o tier do Gemini se torne limitante.
- Acesso ao provedor é **abstraído** em `services/embeddings.py` (interface única, troca de provedor é uma variável de ambiente).

**Por quê:** Gemini é gratuito o suficiente para o estágio experimental. A abstração protege o resto do sistema da escolha — se um dia trocar, ninguém mais precisa saber.

### 3.3. O que vira embedding

Um embedding **por case** (não há necessidade de chunking com 24 cases curtos). O texto a ser embedado é uma concatenação rotulada dos campos relevantes:

```
Cliente: {cliente}
Setor: {setor_empresa} (associados: {setores_associados})
Área: {area_pj} — Serviço: {servico_pj}
Problema: {problema}
Solução: {solucao}
Impacto: {impacto_roi}
```

**Por quê rotulada:** os rótulos ("Problema:", "Solução:") ajudam o modelo de embedding a entender a função semântica de cada bloco. Modelos modernos lidam bem com isso e o ganho em retrieval é mensurável.

**Por quê só um embedding por case:** simplicidade. Embedding por campo (problema, solução, impacto separados) seria útil se quiséssemos perguntas tipo "me dá só o impacto do case X". Não é o caso. Se virar requisito, refatora-se.

### 3.4. Schema novo no Supabase

Duas tabelas, **desacopladas**:

**`casepj`** (mantém os dados de negócio)
- Mesmos campos da tabela atual
- **Mudança:** `setores_associados` vira `TEXT[]` (array nativo do Postgres) em vez de string. Permite filtros exatos: `WHERE 'Saúde' = ANY(setores_associados)`.

**`casepj_embedding`** (novo, paralelo)
- `case_id` — FK para `casepj.id`, com `ON DELETE CASCADE`
- `embedding` — `vector(768)` (depende do modelo escolhido)
- `source_text` — o texto exato que gerou o embedding (para debug e regeneração)
- `model_version` — string identificadora, ex: `"gemini-text-embedding-004"`
- `updated_at` — timestamp

**Índice vetorial:** `CREATE INDEX ON casepj_embedding USING hnsw (embedding vector_cosine_ops);`. Com 24 registros é dispensável, mas já entra como padrão.

**Por quê separado:** o README sinaliza que `casepj` pode ganhar colunas. Embeddings em tabela separada permitem (a) regenerar embeddings sem mexer nos dados de negócio, (b) ter múltiplas versões de embedding convivendo durante uma transição de modelo, (c) deletar e repopular sem perder o case.

### 3.5. Camada de retrieval — três funções

Em `src/retrieval/cases.py`, três funções públicas com propósitos distintos:

| Função | Entrada | Comportamento |
|---|---|---|
| `buscar_deterministico(filtros)` | dict de filtros (setor, área, etc.) | SQL puro com `WHERE`. Retorna **todos** os matches. |
| `buscar_semantico(query, top_k=5)` | texto livre + k | Embedda a query, busca por similaridade de cosseno, retorna top-K. |
| `buscar_hibrido(filtros, query, top_k=5)` | filtros + texto livre | Aplica `WHERE` (filtros) **e** `ORDER BY similaridade`. Filtra primeiro, ranqueia depois. |

A função híbrida é a que vai ser mais usada na prática. Casos extremos:
- Filtros vazios + query → equivale a `buscar_semantico`
- Filtros + query vazia → equivale a `buscar_deterministico`

**Por quê três funções e não uma só:** a função única ficaria com muitos parâmetros opcionais e comportamento ambíguo. Três funções deixam a intenção explícita no call site (`agents/`).

### 3.6. Formato de saída

Schema Pydantic em `src/schemas/case.py`:

```python
class CaseResultado:
    id: int
    cliente: str
    setor_empresa: str
    setores_associados: list[str]
    area_pj: str
    servico_pj: str
    problema: str
    solucao: str
    impacto_roi: str
    score_similaridade: float | None  # None na busca determinística
    motivo_match: str  # "filtro: setor=Varejo" ou "similaridade: 0.87"
```

O campo `motivo_match` ajuda o agente do chatbot a explicar pro Hunter **por que** aquele case apareceu. Ex: "Esse case da Hypera apareceu porque o problema descrito no seu lead é parecido com o problema que eles tinham (sazonalidade na previsão de demanda)."

---

## 4. Mapeamento para a estrutura do projeto

Encaixe nas pastas já planejadas no README:

```
src/
├── retrieval/
│   └── cases.py              # buscar_deterministico, buscar_semantico, buscar_hibrido
├── services/
│   └── embeddings.py         # wrapper do provedor (Gemini/OpenAI)
├── db/
│   ├── models.py             # CasePJ, CasePJEmbedding (SQLAlchemy)
│   └── seed_cases.py         # script: lê SQLite, popula Supabase, gera embeddings
├── schemas/
│   └── case.py               # CaseResultado (Pydantic)
└── agents/
    └── ...                   # consome retrieval/ — fora deste escopo
```

**Princípio:** cada camada faz uma coisa. `retrieval` não sabe de IA generativa, só de busca. `services/embeddings` não sabe de cases, só de "transformar texto em vetor". `agents` orquestra — recebe a pergunta do Hunter e decide qual função de retrieval chamar.

---

## 5. Plano de implementação faseado

### Fase 1 — Infraestrutura (sem código de negócio)
1. Habilitar extensão `pgvector` no Supabase
2. Criar migração Alembic com as duas tabelas (`casepj` + `casepj_embedding`)
3. Configurar variáveis de ambiente (`GEMINI_API_KEY`, `SUPABASE_*`)

### Fase 2 — Pipeline de embeddings
4. Implementar `services/embeddings.py` (função `embed(text: str) -> list[float]`)
5. Implementar `db/seed_cases.py` — lê o SQLite local, transforma `setores_associados` em array, gera embeddings, insere nas duas tabelas
6. Rodar o seed e validar manualmente que os 24 cases estão no Supabase com seus embeddings

### Fase 3 — Camada de retrieval
7. Implementar `buscar_deterministico` (SQL puro)
8. Implementar `buscar_semantico` (embedding da query + ORDER BY similaridade)
9. Implementar `buscar_hibrido` (combinação)
10. Criar testes manuais com queries realistas

### Fase 4 — Validação qualitativa
11. Listar 10 cenários de lead reais (com Henrique Romano) e rodar contra a base
12. Avaliar: o case "certo" aparece no top-3? O `motivo_match` faz sentido?
13. Ajustar (texto que vai pro embedding, threshold de similaridade, top_k)

### Fase 5 — Integração com agentes (fora deste escopo)
14. `agents/` consome as três funções e decide quando usar cada uma a partir do contexto do chat

---

## 6. Riscos e pontos de atenção

| Risco | Mitigação |
|---|---|
| 24 cases podem ser pouco para busca semântica funcionar bem | Validação qualitativa na Fase 4. Se o recall for ruim, considerar reescrever os textos de problema/solução com mais consistência. |
| Tier gratuito do Gemini pode mudar/expirar | Abstração em `services/embeddings.py` permite trocar para OpenAI em uma linha. |
| Cases podem ter overlap (mesmo cliente, áreas diferentes) | Hoje não acontece, mas o schema permite. A busca naturalmente lida com isso retornando ambos. |
| Threshold de similaridade arbitrário | Não fixar threshold no início. Retornar top-K sempre e deixar o agente filtrar com base em score se precisar. |
| Embeddings desatualizados quando o texto do case muda | `seed_cases.py` deve ser idempotente — checar `updated_at` e regerar quando necessário. |

---

## 7. Decisões adiadas (não fazer agora)

Lista do que **conscientemente** está fora do MVP, para evitar scope creep:

- **Re-ranking com LLM** após o retrieval vetorial — ganho marginal com 24 cases.
- **Embedding por campo** (problema separado de solução) — só se o agente pedir.
- **Cache de embeddings de queries** — só faz sentido com volume.
- **Múltiplos modelos de embedding em paralelo** — o schema já suporta, mas não implementar antes de precisar.
- **Busca por palavras-chave (BM25/full-text) combinada com vetorial** — adicionar só se a busca semântica estiver retornando ruído.

---

## 8. Estratégia de testes

Dois níveis, com propósitos distintos.

### 8.1. Unit tests (automatizados, `pytest`)

Testam **mecânica**, não qualidade semântica. Rodam em segundos, sem chamadas externas.

- **`tests/test_retrieval_deterministico.py`** — mocka o banco e valida: filtros aplicados corretamente, filtros vazios retornam todos os registros, filtros sem match retornam lista vazia, ordenação preservada.
- **`tests/test_retrieval_semantico.py`** — mocka o serviço de embedding e o banco. Valida que: a query é embedada antes da busca, o vetor é passado corretamente para a query SQL, o `top_k` é respeitado, o formato do retorno bate com o schema `CaseResultado`.
- **`tests/test_retrieval_hibrido.py`** — valida a combinação: filtros + query, casos extremos (filtros vazios, query vazia) caem nos branches certos.
- **`tests/test_services_embeddings.py`** — mocka a chamada HTTP pro Gemini. Valida: input texto → output vetor de N dimensões, tratamento de erro de API, troca de provedor via env var.

**Stack:** `pytest` + `pytest-mock`. Sem chamadas reais a APIs externas nos testes — sempre mockadas. Banco testado contra um SQLite in-memory ou contra um schema isolado no Supabase de teste (decidir na implementação).

### 8.2. Validação qualitativa (manual, Fase 4)

Testa **qualidade da resposta**, não a mecânica. Não é automatizável de forma confiável com 24-100 cases.

Procedimento: lista de 10 cenários de lead reais com expectativa de qual case "deveria" aparecer no top-3. Roda e avalia. Resultado vai pra um arquivo `tests/cenarios_qualitativos.md` que serve de referência viva — quando algo regredir, dá pra rodar a mesma lista e comparar.

### 8.3. CI (opcional, futuro)

Se o projeto evoluir, vale plugar `pytest` num workflow de GitHub Actions que roda a cada push. Fora do escopo agora.

---

## 9. Definition of Done deste módulo

- [ ] Migrações Alembic criadas e aplicadas no Supabase
- [ ] 24 cases migrados com `setores_associados` em formato array
- [ ] 24 embeddings gerados e armazenados
- [ ] Três funções de busca implementadas com tipos e docstrings
- [ ] Unit tests cobrindo as três funções de busca e o serviço de embeddings
- [ ] 10 cenários qualitativos rodados e documentados em `tests/cenarios_qualitativos.md`
- [ ] README atualizado com instruções de seed e exemplos de uso
- [ ] `.env.example` atualizado com todas as variáveis necessárias

---

## 10. Próximo passo concreto

Começar pela **Fase 1**: escrever a migração Alembic com as duas tabelas. É o desbloqueio de tudo o resto e tem zero dependência de IA — dá pra fazer e validar antes de qualquer chamada de API.

Quando você estiver pronto pra avançar, me avisa que eu te entrego o esqueleto da migração e do `services/embeddings.py` comentados linha a linha — assim você consegue acompanhar mesmo sem fundo forte em código.