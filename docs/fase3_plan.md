# Plano de execução — Fase 3 (universo histórico + backtesting)

Spec mestre: [`fase3.md`](../fase3.md). Este documento é o registro de execução, milestone a
milestone. Decisões metodológicas/arquiteturais vêm do **Handoff v2 (Opus)** — aprovadas,
não se rediscutem aqui. Onde uma questão nova toca metodologia / survivorship / point-in-time
/ lifecycle / arquitetura da Fase 2, a regra é **PARAR e escalar para o Opus**, nunca decidir
sozinho.

Branch de trabalho: `fase3-backtesting-engine` (a partir de `main` `f80c666`). **Sem merge em
`main` sem autorização explícita** (`fase3.md` §91).

Ao fim de cada bloco: `pytest -q` + `ruff check .` + `mypy src`, tudo verde antes de seguir.
Todo bug real contra dados reais vira teste de regressão.

---

## Decisões fixas do Handoff v2 (não rediscutir)

### Infra / banco

- Toda operação no banco B3 usa **exclusivamente** o caminho `stock_research.db` (REST /
  PostgREST + RPC `exec_ddl` para DDL). Projeto correto: `bdppudbcjosznkfucekm` "B3 FOCUS"
  (`doctor` confirma).
- Exceção única, pontual: o ledger `supabase_migrations.schema_migrations` não aceita
  `exec_sql`/`exec_ddl` (sem grant no schema) — reconciliado uma vez no M0 via MCP
  `execute_sql`, apontando explicitamente para `bdppudbcjosznkfucekm`.
- Migrations novas: **aditivas, compatíveis, reversíveis quando possível, testadas**
  (`fase3.md` §1).

### Regra bitemporal (obrigatória, `fase3.md` §3 + correção do Renan no Handoff)

O universo histórico se reconstrói por **tempo efetivo de existência/negociação**
(`valid_from` / `valid_to` / `listing_start` / `listing_end`), **nunca** por tempo de
transação/ingestão. Exigir `available_from <= D` no lifecycle esvaziaria o universo: um
snapshot da CVM baixado em 2026 não pode ser gate de elegibilidade para 2013 — a existência
de uma companhia negociando em 2013 era fato **público** em 2013.

**A exceção é ENUMERADA** a exatamente duas tabelas:

- `company_lifecycle`
- `instrument_lifecycle`

Todo o resto continua em **point-in-time estrito** (`available_from <= as_of`):
`financial_statement_facts`, `cvm_documents`, `fundamental_metrics`, `share_count_history`,
`risk_free_assumptions`, `equity_risk_premium_assumptions`, `wacc_assumptions`,
`valuation_snapshots`, `quality_scores`, notícias, eventos. **Estender essa lista exige
escalar para o Opus.**

Colunas de proveniência nas tabelas de lifecycle são nomeadas de propósito para **não**
colidir com `available_from` (que significa "o gate" no resto do codebase):
`source_available_from` / `source_observed_at` / `ingested_at`. Elas existem para
reconstrução/auditoria, **não** para filtrar elegibilidade.

### API pública do universo — sem look-ahead de lifecycle

`get_investable_universe_as_of(D)` entrega só o necessário para decidir em `D`. `valid_to` /
`listing_end` respondem internamente "estava vivo em D?" mas **não são expostos** à camada de
estratégia. Nunca expor à estratégia: `future delisting date`, `future cancellation reason`,
`future successor`, `future ticker change` — mesmo estando armazenados internamente. O engine
reage no fim **efetivo** (não no anúncio) — viés conservador.

### Instrumentos históricos / deslistados

- Mantêm representação histórica; cadastrados em `instruments` com `active = false` quando
  necessário.
- Ausência de preço **não** autoriza remover o instrumento do universo histórico estrutural.
- Na camada investível (M2+), falta de dados vira `NOT_ELIGIBLE_DATA` — nunca preenchida com
  dado futuro.

### Universo V1 (`fase3.md` §14-16)

- Elegível = **negociada na data** + filtros de liquidez + disponibilidade mínima de dados.
- **Não** exigir participação histórica no IBOV nesta versão.
- **Não** materializar `universe_snapshots` agora — `get_investable_universe_as_of` é
  **função pura** sobre os lifecycles, espelhando `analytics/fundamentals.select_point_in_time`.
  Só reavaliar materialização com evidência concreta de problema de performance.
- Config versionada: `config/backtest_universe_v1.yaml`.
- Granularidade anual em V1 (data do FCA).

### Liquidez

Se calculada, vai para estrutura própria (`liquidity_metrics` ou equivalente do Handoff) —
**nunca** misturar dado de mercado silenciosamente em `fundamental_metrics`.

### VALE — caso histórico obrigatório (`fase3.md` §10)

Demonstrar que a estrutura acionária vigente em 2012 (VALE3 ON + VALE5 PNA) difere da
vigente pós-2017 (unificação de classes; VALE5 some ~2017). Market cap histórico calculado
pela estrutura vigente na data.

### Fora de escopo agora

M2+, backtest engine, estratégias (quality/value), carteira, retorno futuro, notícias como
sinal, ML. Nenhuma metodologia da Fase 2 muda.

---

## M0 — checkpoint (**concluído**, 2026-08-30)

- Branch `fase3-backtesting-engine` criada a partir de `main` (`f80c666`).
- Baseline de `main`: **463 testes verdes**, `ruff check .` limpo, `mypy src` limpo
  (74 arquivos).
- Housekeeping (`fase3.md` §2): ledger `supabase_migrations.schema_migrations` reconciliado.
  Faltavam **duas** linhas (DDL de ambas já aplicada no banco, só o registro ausente):
  - `20260826000001` — `exec_sql_higher_statement_timeout` (funções `exec_sql`/`exec_ddl`
    já com `statement_timeout = 120s`, confirmado).
  - `20260827000006` — `dcf_and_macro` (as 4 tabelas existem e estão populadas:
    `risk_free_assumptions` 1, `equity_risk_premium_assumptions` 1, `wacc_assumptions` 2,
    `valuation_snapshots` 12).
  Feito via MCP `execute_sql` no projeto `bdppudbcjosznkfucekm` — única operação fora do
  caminho `stock_research.db` (o RPC não tem grant no schema `supabase_migrations`).
  Drift histórico que permanece (fora de escopo): os arquivos
  `20260808170000_news_backfill_checkpoints.sql` /
  `20260808174500_news_checkpoint_unsupported_date_range.sql` têm timestamp de nome
  diferente das versões no ledger (`20260808165922` / `20260809230457`) — reconstrução da
  Fase 1.1, não afeta `db push`.
- Drift de doc corrigido: `docs/data_dictionary.md` não diz mais "(migration ..06, não
  aplicada)".
- Nenhuma DDL nova rodada no M0.

## M1 — universo histórico / survivorship bias (em andamento)

Fontes (do Handoff, a validar contra arquivo real antes de qualquer parser):

- **Company lifecycle**: CVM cadastro `cad_cia_aberta.csv` — já em disco
  (`data/raw/cvm/registry/`). Traz `DT_REG`, `DT_CANCEL`, `MOTIVO_CANCEL`, `SIT`,
  `SIT_EMISSOR`, `CATEG_REG`, `TP_MERC`. É **snapshot** (sem histórico de nome/situação, sem
  ticker/ISIN/classe). Parser atual valida só `{CNPJ_CIA, DENOM_SOCIAL, CD_CVM, SIT}` e
  descarta canceladas — estender para reter canceladas/incorporadas.
- **Instrument / ticker lifecycle**: CVM FCA
  `fca_cia_aberta_valor_mobiliario_YYYY.csv` (dataset `cia_aberta-doc-fca`, ZIPs anuais
  2010-2026). **Não ingerido hoje.** Traz código de negociação / classe / mercado / datas de
  negociação por ano. **Schema exato A VALIDAR baixando 1 ZIP real antes de escrever o
  parser** — não assumir nomes de coluna.
- Cobertura: piso real 2010 (preço + CVM + FCA). Notícias/eventos só 2017+.
- Preço da cauda de deslistadas: yfinance para após o delisting — buraco conhecido, aceitar
  cobertura parcial + `quality_flag`.

Entregas M1: 2 tabelas novas aditivas (`company_lifecycle`, `instrument_lifecycle`),
ingestão do cadastro estendido + FCA, `ticker_aliases` populada com trocas reais detectadas,
caso VALE validado, testes anti-survivorship + invariância à proveniência + look-ahead de
lifecycle não vaza para sinais/valuation.

### Abertas para o Opus (não decidir sozinho)

- Deslistadas: todas em `instruments` (`active=false`) ou só as com preço?
- Liquidez: em `fundamental_metrics` (proibido misturar) vs. tabela nova — Handoff aponta
  tabela própria; confirmar nome.
- "Investível" exigir presença em índice — **fora da V1** por decisão do Handoff; carteira
  histórica da B3 não validada.
- Preço de saída em delisting sem cotação — política financeira pertence ao milestone do
  backtest, não ao M1.
- Split temporal (`fase3.md` §46) — após medir cobertura real.
