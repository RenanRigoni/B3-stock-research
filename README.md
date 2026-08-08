# stock-research — base histórica de ações da B3

Sistema pessoal de pesquisa que reconstrói, de forma auditável, o contexto histórico
de uma ação: **preços**, **notícias**, **fundamentos** e a **reação do mercado a eventos**.

O objetivo não é prever preço. É conseguir responder, com honestidade estatística:

> *O que o mercado sabia sobre esta empresa naquele momento, quais eventos estavam
> ocorrendo, como os fundamentos conhecidos se apresentavam, e como o preço reagiu
> depois — em termos absolutos e relativos ao Ibovespa?*

Esta é a **Fase 1** de cinco. Ela não emite recomendação, não calcula preço-alvo e
não faz valuation. Ela constrói a base sobre a qual as fases seguintes serão possíveis.

---

## Princípios que o código não negocia

| Princípio | O que significa na prática |
|---|---|
| **Point-in-time** | Todo fato tem `available_from`. O balanço de 2024 nunca é usado para analisar uma decisão de 2023. Há testes automáticos que falham se isso for violado. |
| **Rastreabilidade** | Toda linha aponta para o `run_id` e o arquivo bruto que a originou. Nenhum número aparece sem origem. |
| **Idempotência** | Rodar o mesmo pipeline duas vezes não duplica nada. Toda escrita é upsert por chave natural. |
| **Nada em silêncio** | Anomalia vira registro em `quality_findings`, não `except: pass`. Dado ausente é preferível a dado incorreto. |
| **Sem caixa preta** | Todo score guarda o método, o modelo e a versão que o produziram. Resultados de versões diferentes nunca se misturam. |

Ordem de prioridade quando houver conflito:
`correção > rastreabilidade > reprodutibilidade > simplicidade > performance > estética`.

---

## Arquitetura em uma frase

Pipelines **Python locais** baixam dados das fontes, preservam o bruto **em disco**,
normalizam e gravam a camada curada no **Supabase Postgres**.

```
fontes externas          disco local              Supabase Postgres
──────────────────       ─────────────────        ──────────────────────
yfinance  (preços)  ──▶  data/raw/prices/   ──▶   daily_prices
CVM       (DFP/ITR) ──▶  data/raw/cvm/      ──▶   financial_statement_facts
GDELT     (notícias)──▶  data/raw/news/     ──▶   news_articles
brapi     (validação)                              price_validations
                                                   │
                                                   ▼
                                            events → event_studies
```

O bruto fica local de propósito: é grande, é reprodutível, e serve para reprocessar
sem consumir a API de novo. O curado vai para o Supabase porque as fases 2–5 terão
uma interface web (Vercel) lendo exatamente esses dados.

Detalhes e a decisão de trocar DuckDB por Postgres: [docs/architecture.md](docs/architecture.md).

---

## Instalação

Requer **Python 3.12** e [uv](https://docs.astral.sh/uv/).

```bash
git clone <repo>
cd B3

uv venv --python 3.12
uv pip install -e ".[dev]"

cp .env.example .env    # e preencha (ver abaixo)
```

### Configurar o `.env`

O único valor sem o qual nada funciona é o `DATABASE_URL`:

1. Supabase Dashboard → **Project Settings** → **Database** → **Connection string** → **URI**
2. Use o **Session pooler** (porta 5432)
3. A senha é a que você definiu ao criar o projeto — **as API keys do Supabase não servem aqui**

`BRAPI_TOKEN` é opcional: sem ele, a validação cruzada de preços é pulada com aviso,
e o resto do pipeline segue normalmente.

### Verificar

```bash
stock-research doctor    # diagnostica config, segredos e conexão
stock-research init      # cria data/ e carrega o universo de instrumentos
stock-research status    # cobertura de dados por instrumento
```

---

## Comandos

Todos os comandos abaixo estão implementados e validados contra dados reais.

| Comando | O que faz |
|---|---|
| `doctor` | Diagnostica configuração e conexão |
| `init` | Cria `data/` e carrega `companies.yaml` no banco |
| `status [TICKER]` | Cobertura de dados por instrumento |
| `sync-prices --ticker PETR4 --start 2010-01-01` / `--all` | Backfill de preços |
| `update-prices` | Atualização incremental de preços |
| `validate-prices --ticker PETR4 --days 60` | Comparação yfinance × brapi |
| `sync-cvm --registry` | Resolve CNPJ/código CVM das empresas do universo |
| `sync-cvm --year 2024` / `--from-year 2010` | DFP/ITR da CVM |
| `sync-news --ticker PETR4 --start 2020-01-01 --end 2020-12-31` / `--all` | Notícias via GDELT |
| `analyze-news --ticker PETR4` | Dedup por similaridade, relevância e classificação heurística |
| `build-events --ticker PETR4` | Agrupa notícias relevantes em eventos, calcula `effective_trade_date` |
| `run-event-study --ticker PETR4` | Retornos, excesso, market model, CAR por evento |
| `audit` | Relatório de qualidade de dados (`data/exports/data_quality_*.md`) |
| `report PETR4` | Relatório da empresa (`data/exports/PETR4_phase1_report.md`) |
| `backup` | Copia `data/raw/` e `config/` para `backups/<timestamp>/` |
| `pipeline --ticker PETR4 --start 2018-01-01` | Tudo, ponta a ponta |

Roadmap completo (com os bugs reais encontrados e corrigidos em cada milestone):
[docs/roadmap.md](docs/roadmap.md).

---

## Adicionar uma empresa

Edite [config/companies.yaml](config/companies.yaml) e rode `stock-research init` de novo.

```yaml
- ticker: WEGE3
  yahoo_symbol: WEGE3.SA
  company_name: WEG
  aliases:
    strong: [WEG, WEGE3]
    weak: [Weg S.A.]
```

Aliases `weak` são termos ambíguos que geram falso positivo se usados sozinhos na busca
de notícias — `"Vale"` também é "vale a pena". A distinção importa para o score de
relevância.

---

## Configuração versionada

| Arquivo | Papel |
|---|---|
| [config/settings.yaml](config/settings.yaml) | Janelas, thresholds, provedores, parâmetros do event study |
| [config/companies.yaml](config/companies.yaml) | Universo de instrumentos e aliases de busca |
| [config/news_taxonomy.yaml](config/news_taxonomy.yaml) | Categorias de evento e léxicos do classificador |
| [config/company_mapping.yaml](config/company_mapping.yaml) | Ticker → CNPJ/código CVM (preenchido por `sync-cvm --registry`, com conferência humana) |

Esses arquivos vão para o git de propósito: mudá-los muda o resultado, e essa mudança
precisa aparecer no diff.

---

## Banco de dados

Migrations em [supabase/migrations/](supabase/migrations/). Dicionário completo em
[docs/data_dictionary.md](docs/data_dictionary.md).

**RLS está habilitado em todas as tabelas, sem nenhuma policy.** É intencional: o
pipeline usa a `service_role` (que ignora RLS) e qualquer chave pública que vaze não
lê absolutamente nada. Quando a Fase 4 expuser um app no Vercel, as policies de
leitura serão adicionadas conscientemente, uma a uma.

### Backup

Itens a preservar:
- o banco Supabase (`pg_dump`, ou os backups automáticos do próprio Supabase)
- `data/raw/` — permite reprocessar tudo sem consumir API de novo
- `config/` — define o que o pipeline produz

---

## Testes

```bash
pytest                          # suíte offline, sem rede
pytest -m integration           # bate nas fontes reais (yfinance, GDELT, CVM)
ruff check .
mypy src
```

Testes de integração são marcados e **não rodam por padrão**. A suíte offline usa
fixtures em `tests/fixtures/`.

---

## Fontes de dados

| Fonte | Uso | Documentação |
|---|---|---|
| **yfinance** | Preços diários (fonte primária) | [docs/sources.md](docs/sources.md) |
| **CVM Dados Abertos** | Fundamentos oficiais (DFP/ITR) | [dados.cvm.gov.br](https://dados.cvm.gov.br/) |
| **GDELT DOC API** | Notícias históricas | [gdeltproject.org](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/) |
| **brapi** | Validação cruzada de preços (opcional) | [brapi.dev/docs](https://brapi.dev/docs) |

`yfinance` é um projeto open source **não afiliado ao Yahoo**. Os dados do Yahoo
Finance têm termos próprios voltados a uso pessoal — compatível com o escopo deste
projeto, que é pesquisa pessoal. Ver [docs/sources.md](docs/sources.md) e
[docs/limitations.md](docs/limitations.md).

---

## Limitações conhecidas

Leia [docs/limitations.md](docs/limitations.md) antes de confiar em qualquer número.
Resumo do que mais importa:

- `yfinance` não é feed oficial da B3 e pode corrigir séries retroativamente
- GDELT não captura toda notícia existente; ausência de resultado ≠ ausência de notícia
- timestamps de notícia podem ser imprecisos — daí o campo `time_precision`
- correlação temporal entre notícia e preço **não prova causalidade**
- métricas contábeis dependem do setor: `net_debt/EBITDA` não se aplica a banco
- delisted securities ainda exigem tratamento — survivorship bias não está resolvido

---

## Aviso

```
Este sistema é uma ferramenta pessoal de pesquisa.
Os resultados não constituem recomendação de investimento.
Dados podem conter erros, atrasos ou lacunas.
Análises históricas não garantem retornos futuros.
```
