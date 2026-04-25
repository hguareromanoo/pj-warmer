
# 🔥 PJ Warmer

Ferramenta de inteligência pré-AT para Hunters da Poli Júnior.

## 📌 Contexto

Atualmente, os Consultores de Negócios (CNs) entram nas reuniões de Diagnóstico (ATs) com pouco ou nenhum contexto sobre a empresa do lead. Isso prejudica a profundidade e o teor consultivo das vendas.

O **PJ Warmer** é o MVP de uma iniciativa experimental que visa tornar a preparação para ATs um processo — aplicando o conceito de **Warmer** da metodologia Challenger Sale: o momento em que o vendedor demonstra conhecimento sobre o lead, seus problemas e o mercado em que está inserido.

---

## 🎯 Objetivos

- Tornar as vendas da Poli Júnior mais consultivas
- Aproveitar melhor o tempo das ATs (menos Situação, mais Problema, Implicação e Necessidade)
- Ressignificar o papel do Hunter na reunião de vendas

---

## 🧪 Hipóteses do Experimento

1. Um briefing pré-AT melhora a conversão para Aprofundamento ou Proposta
2. IA generativa é viável para realizar essas pesquisas com qualidade
3. Hunters com contexto prévio levantam demandas de maior qualidade

---

## ✅ Definition of Done

**Básico (MVP):**
- Busca de cases da Poli Júnior do mesmo setor/área da empresa
- Notícias relevantes sobre a empresa do lead
- Briefing sobre a atuação da empresa
- Tendências no setor e na área de atuação do lead
- Uso registrado e acompanhado por pelo menos 1 Hunter

**Outlier:**
- Tudo acima + Teste A/B estruturado entre reuniões com e sem uso do Warmer

---

## 📊 Indicadores de Sucesso

| Indicador | Tipo |
|---|---|
| Feedback positivo dos CNs sobre qualidade dos briefings | Qualitativo |
| Aumento na conversão para Aprofundamento | Quantitativo |
| Aumento na qualidade das demandas levantadas | Qualitativo |
| Fidelização | Longo prazo |
| Qualidade de propostas geradas | Qualitativo |
| Número de demandas identificadas por AT | Quantitativo |

---

## 🛠️ Arquitetura

```
Usuário (Hunter)
     │
     ▼
Interface de Chat (slash commands)
     │
     ├── /briefing <empresa> → Deep research + contexto da empresa
     ├── /cases <setor> <área> → Busca de cases similares (determinístico + vetorial)
     └── <pergunta livre> → Chat com contexto do deal (Pipedrive)
     │
     ▼
Agente IA (LLM)
     │
     ├── Banco de Cases (SQLite / busca vetorial com embeddings)
     ├── API Pipedrive (dados do deal/lead)
     └── Deep Research (web)
     │
     ▼
Resposta estruturada ao Hunter
```

---

## 🔍 Busca de Cases

A busca de cases similares opera em duas camadas:

- **Determinística:** filtro por setor da empresa e área de atuação do lead
- **Semântica (vetorial):** embedding dos campos de dor e solução dos cases, com recuperação por similaridade de cosseno

> Base atual: ~24 cases estruturados extraídos do material de AT.
> Próximos passos: enriquecer com propostas do Notion e recuperar documentação de projetos concluídos.

---

## 📦 Stack

| Camada | Tecnologia |
|---|---|
| Backend | Python |
| ORM / Banco | SQLAlchemy + Alembic + Supabase |
| Embeddings | Gemini (free tier) / OpenAI (fallback) |
| Observabilidade | Logfire (token usage, latência, dashboard) |
| Integração CRM | Pipedrive API |
| Interface | Chat com suporte a slash commands |

---
## Estrutura de pastas
```text 
pj-warmer/ 
├── alembic/ 
├── src/ 
│ ├── agents/ 
│ ├── services/ 
│ ├── retrieval/
│ ├── db/ 
│ ├── schemas/
│ └── utils/
├── tests/ 
├── .env 
├── alembic.ini 
├── requirements.txt 
└── README.md
```

-   `agents`: orquestra
    
-   `services`: executa regras e integrações
    
-   `retrieval`: busca informação
    
-   `db`: persiste dados
    
-   `schemas`: padroniza dados
    
-   `utils`: apoia com funções auxiliares
## 🚀 Como rodar

```bash
# Clone o repositório
git clone <repo-url>
cd pj-warmer

# Instale as dependências
pip install -r requirements.txt

# Configure as variáveis de ambiente
cp .env.example .env
# Preencha: PIPEDRIVE_API_KEY, GEMINI_API_KEY, SUPABASE_URL, SUPABASE_KEY

# Rode as migrações do banco
alembic upgrade head

# Inicie a aplicação
python main.py
```

---

## 🗺️ Roadmap

- [x] Banco de cases estruturado (setor + área + serviço)
- [x] Busca determinística por setor
- [ ] Embeddings e busca vetorial dos cases
- [ ] Interface de chat com slash commands
- [ ] Integração com Pipedrive (pull de dados do deal)
- [ ] Deep research da empresa e do lead
- [ ] Dashboard de observabilidade (Logfire)
- [ ] Envio de briefing para nota no Pipedrive
- [ ] Registro de uso e coleta de feedback dos Hunters
- [ ] Teste A/B estruturado

---

## 👥 Time

| Papel | Responsável |
|---|---|
| Produto & Arquitetura | Henrique Romano |
| Implementação | Henrique Nunes |

---

> Este projeto é um experimento de inovação da Poli Júnior.
> Validação em curso — feedbacks dos usuários são parte essencial do processo.
