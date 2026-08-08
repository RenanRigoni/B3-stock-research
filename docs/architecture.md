# Arquitetura

## Decisão de storage: por que Supabase Postgres e não DuckDB

O documento de especificação (`fase1.md`) pede DuckDB local, com a justificativa de que
o projeto é pessoal e não precisa de nuvem. Essa recomendação foi escrita **sem saber que
o produto final vai rodar em Supabase + Vercel**, acessado pelo navegador.

Manter DuckDB nesta fase criaria uma migração dolorosa depois: schema, chaves naturais,
estratégia de upsert e todo o código de idempotência teriam que ser reescritos quando as
Fases 2–4 precisassem de um app web lendo os mesmos dados.

**Decisão:** a camada curada nasce direto em Postgres.

Tudo o mais do `fase1.md` permanece válido sem alteração — point-in-time, preservação do
bruto, rastreabilidade, idempotência, versionamento das transformações.

### Tradução DuckDB → Postgres

| `fase1.md` pedia | Aqui é |
|---|---|
| `data/market_history.duckdb` | Supabase Postgres, região `sa-east-1` |
| `db/schema.sql` + migrations manuais | `supabase/migrations/*.sql` |
| Tipos DuckDB | `bigint identity`, `timestamptz`, `numeric`, `jsonb`, `text[]` |
| Views DuckDB | Postgres views com `security_invoker = true` |
| Conexão via arquivo | `DATABASE_URL` (psycopg) ou PostgREST |

## Camadas

```
    FONTES                  DISCO LOCAL              SUPABASE POSTGRES
 ─────────────           ─────────────────         ─────────────────────
  yfinance      ──┐
  CVM           ──┼──▶   data/raw/<fonte>/   ──▶    staging → curated
  GDELT         ──┤      + SHA256 em             (tabelas + views)
  brapi         ──┘        raw_files
```

### Por que o bruto fica local

- é grande e cresce rápido (ZIPs anuais da CVM, JSONs do GDELT);
- é **reprodutível** — perdê-lo custa tempo de download, não informação;
- permite reprocessar sem consumir API de novo;
- storage em nuvem cobraria por algo que ninguém consulta interativamente.

Cada arquivo bruto é registrado em `raw_files` com SHA256. Se a CVM reprocessar um ZIP,
a mudança é **detectada** em vez de sobrescrever a história silenciosamente.

### Por que o curado vai para o Postgres

As Fases 2–5 terão uma interface web na Vercel. Se os dados já estiverem no Supabase
desde a Fase 1, o app do futuro só precisa **ler** — nenhuma migração.

## Dois backends de banco, uma interface

`stock_research.db` escolhe o backend automaticamente:

| Backend | Quando | Características |
|---|---|---|
| `psycopg` | `DATABASE_URL` configurada | Rápido, transações reais, distingue insert de update |
| `rest` | Só as API keys disponíveis | PostgREST + RPC `exec_sql`, mais lento, sem transação multi-tabela |

O fallback REST existe por um motivo prático: **as API keys do Supabase não servem como
senha do Postgres**. Sem ele, o projeto ficaria parado esperando alguém copiar a
connection string do dashboard.

Nenhum chamador precisa saber qual está ativo. `stock-research doctor` mostra.

Assim que `DATABASE_URL` for preenchida, o backend `psycopg` assume sozinho e as RPCs
`exec_sql` / `exec_ddl` podem ser removidas.

## Segurança

- **RLS habilitado em todas as tabelas, sem nenhuma policy.** O pipeline usa a
  `service_role` (que ignora RLS). Qualquer chave pública que vaze não lê nada.
- Views usam `security_invoker = true` — respeitam o RLS das tabelas base em vez de rodar
  com privilégios do dono.
- As RPCs `exec_sql` / `exec_ddl` têm `EXECUTE` revogado de `public`, `anon` e
  `authenticated`; só `service_role` executa. Como a `service_role` já tem acesso total ao
  banco, isso não amplia a superfície de ataque.
- Logs passam por um filtro que mascara chaves, JWTs e senhas antes de qualquer handler
  (`src/stock_research/logging.py`), com testes cobrindo cada formato.

## Contrato dos pipelines

Todo pipeline segue a mesma forma:

```python
run_id = start_run("prices", provider="yfinance", ticker="PETR4")
try:
    ...                          # baixa → salva bruto → normaliza → valida
    upsert_many(...)             # idempotente por chave natural
    finish_run(run_id, status="success", records_inserted=n)
except Exception as exc:
    finish_run(run_id, status="failed", error_message=str(exc))
    raise
```

Consequências garantidas pelo schema, não pela disciplina de quem escreve:

- reexecutar não duplica (toda escrita tem `UNIQUE` de chave natural);
- toda linha derivada aponta para o `run_id` que a produziu;
- falha no meio não corrompe — o bruto já está em disco antes de qualquer transformação;
- anomalia vira registro em `quality_findings`, nunca `except: pass`.

## Estrutura de código

```
src/stock_research/
├── cli.py             comandos typer
├── config.py          YAML versionado + .env
├── logging.py         logs estruturados + redação de segredos
├── db/                fachada, backend psycopg, backend REST
├── sources/           adapters por fonte (yfinance, gdelt, cvm, brapi)
├── pipelines/         orquestração: baixar → normalizar → gravar
├── transforms/        normalização pura, sem I/O
├── analytics/         retornos, retorno anormal, event study
├── quality/           checks e relatórios
└── utils/
```

`sources/` isola cada API atrás de uma interface. Trocar o provedor de preços não deve
tocar em `pipelines/` — e chamadas HTTP a uma fonte nunca aparecem espalhadas pelo código.

`transforms/` não faz I/O: é onde ficam as funções puras e testáveis sem rede.
