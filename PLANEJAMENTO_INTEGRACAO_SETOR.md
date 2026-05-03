# 📋 Planejamento — Integração Cases × Deep Research (versão setor)

> Versão simplificada do planejamento de integração. Substitui o
> `PLANEJAMENTO_INTEGRACAO_CASES_RESEARCH.md` para esta entrega.
>
> Diferença principal: aqui o sinal de match é só **setor** (incluindo
> setores semanticamente próximos via embedding) e **nomes de
> parceiros/concorrentes**. Não usa `common_challenges` nem `tldr`.

---

## 1. Contexto

Depois que `run_deep_research(lead)` produz um `WarmerBriefing`, queremos
enriquecer o output com cases da Poli Júnior relacionados ao lead.

> **Antes de qualquer decisão, ler o `README.md`.** Ele é o guia mestre
> do projeto: define a arquitetura geral, o roadmap, e os consumidores
> futuros desta integração — o agente de chat com slash commands
> (`/briefing <empresa>`, `/cases <setor> <area>`) e a futura nota
> estruturada no Pipedrive. Esta integração precisa **não atrapalhar**
> nenhum dos dois, mas **não precisa implementá-los**.

Três sinais a explorar (todos já presentes no briefing/lead):

| Sinal | Origem | Tipo de match |
|---|---|---|
| Setor primário do lead | `LeadInput.org_setor` | Determinístico (já case+accent insensitive depois da migração unaccent) |
| Setores **semanticamente próximos** | `LeadInput.org_setor` vs setores únicos da base | Cosseno entre embeddings, threshold |
| Parceiros e concorrentes | `briefing.company.notable_partners_or_clients` + `briefing.industry.direct_competitors` | Match exato (case+accent insensitive) em `casepj.cliente` |

Sem challenges, sem tldr, sem RRF. Só setor + nome.

---

## 2. Estado atual

| Peça | Status |
|---|---|
| `run_deep_research` | ✅ implementado |
| `buscar_deterministico` (filtro setor já insensitive) | ✅ implementado |
| `buscar_semantico`, `buscar_hibrido` | ✅ implementado (não usados aqui) |
| `embed(text, task_type)` | ✅ implementado |
| Helper "lista de setores únicos da base" | ⬜ não existe |
| Helper "setores similares por embedding" | ⬜ não existe |
| Helper "busca de cases por nome de cliente" | ⬜ não existe |
| Orquestrador da integração | ⬜ não existe |
| Schema `BriefingComCases` | ⬜ não existe |

---

## 3. Decisões arquiteturais (mínimas)

### 3.1. Onde mora

- `src/retrieval/cases.py` ganha **uma função nova**: `buscar_por_clientes(nomes)`.
- `src/retrieval/setores.py` (NOVO): helpers de setor (lista + similaridade).
- `src/agents/case_matcher.py` (NOVO): orquestração da integração.
- `src/agents/warmer_orchestrator.py` (NOVO): junta deep_research + matcher.
- `src/schemas/research.py` ganha `BriefingComCases`.

**Restrição de contrato (não implementar agora, só não atrapalhar):**
- `run_warmer(lead)` deve devolver um `BriefingComCases` que seja:
  - chamável de qualquer lugar (CLI manual hoje, slash command `/briefing`
    futuramente, agente de chat com pergunta livre);
  - serializável para JSON limpo via Pydantic (futuro: nota no Pipedrive).
- Em prática: assinatura simples (`async def run_warmer(lead) -> BriefingComCases`),
  sem efeitos colaterais (não imprime, não escreve arquivo). Quem chama
  decide o que fazer com o resultado.

### 3.2. Setores semanticamente próximos

- "Setor único da base" = `SELECT DISTINCT setor_empresa FROM casepj
  UNION SELECT DISTINCT unnest(setores_associados) FROM casepj`. Hoje
  são ~19 strings.
- Para o lead, `embed(lead.org_setor, task_type="semantic_similarity")`.
- Para cada setor único, embedda também (com `lru_cache` — 19 chamadas
  na primeira execução, 0 daí em diante no mesmo processo).
- Retorna setores com `cosseno >= 0.75`. Threshold é o chute inicial;
  calibrar com os cenários reais.

Por que não criar tabela nova de embedding de setores? **19 strings, 1
processo, cache em memória basta**. Persistir em tabela vira complexidade
sem retorno claro nesse volume.

### 3.3. Match de parceiros/concorrentes

- Match exato em `casepj.cliente`, **case+accent insensitive** (já que
  o unaccent já existe no banco).
- Sem fuzzy/trigram no MVP. Decisão consciente: o risco de falso positivo
  com nomes curtos compensa só se os usuários do Hunter sentirem falta.
- Os dois campos do briefing são unidos numa lista única antes do match.

### 3.4. Schema de saída

```python
class CasesParaBriefing(BaseModel):
    por_setor: list[CaseResultado]           # cases do setor + similares
    por_relacao: list[CaseResultado]         # parceiros + concorrentes
    setores_consultados: list[str]           # auditoria: quais setores entraram (similaridade >= threshold)

class BriefingComCases(BaseModel):
    briefing: WarmerBriefing
    cases: CasesParaBriefing
```

`setores_consultados` permite ao Hunter (e a quem calibra) ver quais
setores foram considerados similares — fundamental para diagnóstico
quando algum case "óbvio" não aparecer ou um case "errado" aparecer.

### 3.5. Dedup e ordem

- Dentro de `por_setor`: dedupe por `case.id`. Se um case aparece pelo
  setor primário **e** por um setor similar, fica uma vez. Ordem: pelo
  setor com maior score de similaridade.
- `por_setor` e `por_relacao` são listas separadas — não dedupar entre
  elas. Um case pode aparecer nas duas (ex: parceiro do lead que também
  é do mesmo setor) e o Hunter deve ver isso explicitamente.

---

## 4. Plano faseado (sub-fases curtas)

### A.1 — Schemas
- Criar `CasesParaBriefing` e `BriefingComCases` em `src/schemas/research.py`.
- Validação: importar no Python sem erro.

### A.2 — Lista de setores únicos da base
- Adicionar em `src/retrieval/setores.py` a função `listar_setores_unicos() -> list[str]` (via SQL union).
- Validação: rodar e devolver ~19 strings.

### A.3 — Similaridade entre setores
- Em `src/retrieval/setores.py`: `setores_similares(setor_lead, threshold=0.75) -> list[tuple[str, float]]`.
- Embedda `setor_lead`, cacheia (`lru_cache`) embeddings dos setores únicos, retorna os acima do threshold ordenados decrescente.
- Validação: chamar com `"Bens de Consumo"` e ver se retorna `Varejo`, `Alimentos e Bebidas` etc. com score razoável.

### A.4 — Busca por nomes de cliente (Lane B)
- Em `src/retrieval/cases.py`: `buscar_por_clientes(nomes: list[str]) -> list[CaseResultado]`.
- SQL: `WHERE lower(unaccent(cliente)) IN (lower(unaccent(:n)), ...)`.
- `motivo_match = "cliente=parceiro/concorrente do lead"`.
- Validação: passar `["Hypera Pharma"]` e ver o case 1 retornar.

### A.5 — Orquestrador da integração
- Em `src/agents/case_matcher.py`: `encontrar_cases_para_briefing(briefing, lead) -> CasesParaBriefing`.
- Faz: A.3 → para cada setor similar, `buscar_deterministico(setor=...)` → dedupe → soma com Lane B.
- Validação: chamar com o JSON da Nestlé e inspecionar saída.

### A.6 — Orquestrador final
- Em `src/agents/warmer_orchestrator.py`: `run_warmer(lead) -> BriefingComCases`.
- Chama `run_deep_research` + `encontrar_cases_para_briefing`.

### A.7 — Atualizar `examples/run_manual.py`
- Trocar `run_deep_research` por `run_warmer`.
- Imprimir cases no terminal e salvar no JSON.
- Validação: rodar contra Afip e Nestlé, conferir cases.

---

## 5. Riscos

| Risco | Mitigação |
|---|---|
| Threshold 0.75 alto demais (não acha similares) | Inspecionar `setores_consultados` na A.7. Baixar para 0.65 se necessário. |
| Threshold baixo demais (acha qualquer coisa) | Mesma inspeção. Subir para 0.85. |
| Embedding de strings curtas é ruidoso | Usar `task_type="semantic_similarity"` (não retrieval). E aceitar que é MVP. |
| Match exato em parceiros/concorrentes nunca acerta | Aceitar. Se ficar pobre, adicionar trigram (`pg_trgm`) numa fase futura. |
| 19 chamadas Gemini no primeiro briefing | Aceitável (free tier). lru_cache resolve no segundo briefing em diante. |

---

## 6. Decisões adiadas

Vale repetir o princípio: **prioridade é funcionar e ser simples**. Tudo
abaixo é planejado mas fora do escopo desta entrega.

- Estender `format_as_markdown` e `to_json_payload` em `src/retrieval/formatters.py`
  para incluir cases. (Hoje o `run_manual.py` pode imprimir cases de
  forma crua. A formatação bonita vem quando o slash command
  `/briefing` for implementado.)
- Implementação dos slash commands `/briefing`, `/cases`, etc.
- Integração com Pipedrive (envio do `BriefingComCases` como nota
  estruturada).
- Trigram (`pg_trgm`) para fuzzy match em parceiros/concorrentes.
- Persistir embeddings de setores em tabela.
- Re-rank com LLM dos cases finais.
- Voltar a usar challenges/tldr como sinal adicional.

---

## 7. DoD

- [ ] Schemas criados.
- [ ] `setores_similares` retorna resultado plausível em testes manuais.
- [ ] `buscar_por_clientes` funciona.
- [ ] `run_warmer` roda end-to-end contra Afip e Nestlé sem erro.
- [ ] Threshold de similaridade calibrado (≥ 0.65 e ≤ 0.85, idealmente 0.75).

---

## 8. Próximo passo

Começar pela A.1 (schemas).
