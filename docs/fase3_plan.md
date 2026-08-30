# Plano de execução — Fase 3 (universo histórico + backtesting)

Spec mestre: [`fase3.md`](../fase3.md). Decisões metodológicas/arquiteturais: **Handoff v2
(Opus)** — [`docs/fase3_handoff_v2.md`](fase3_handoff_v2.md), 16 seções, aprovado. Não se
rediscutem aqui. Detalhe do universo histórico e da regra bitemporal:
[`docs/historical_universe.md`](historical_universe.md). Este documento é o registro de
execução, milestone a milestone. Onde uma questão nova toca metodologia / survivorship /
point-in-time / lifecycle / arquitetura da Fase 2 e **não está coberta pelo Handoff v2 §12**,
a regra é **PARAR e escalar para o Opus** (gatilhos no Handoff §16), nunca decidir sozinho.

Branch de trabalho: `fase3-backtesting-engine` (a partir de `main` `f80c666`). **Sem merge em
`main` sem autorização explícita** (`fase3.md` §91).

Ao fim de cada bloco: `pytest -q` + `ruff check .` + `mypy src`, tudo verde antes de seguir.
Todo bug real contra dados reais vira teste de regressão.

---

## Decisões fixas do Handoff v2 (não rediscutir)

### Infra / banco — regra final (corrige Handoff §9)

**Nome de conector não é identidade de banco. `project_id` é.**

```
DDL (create/alter/drop):  SOMENTE mcp__claude_ai_Supabase__apply_migration
                          com project_id='bdppudbcjosznkfucekm' explícito.
                          exec_ddl NÃO tem permissão de DDL. Não tentar.

DML + leitura:            stock_research.db (REST + exec_sql/exec_ddl). Caminho normal.

Ledger:                   supabase_migrations.schema_migrations não aceita o RPC (403).
                          apply_migration mantém sozinho. execute_sql só para
                          reconciliar drift histórico.

ANTES DE QUALQUER DDL, obrigatório:
  1. list_projects → confirmar que bdppudbcjosznkfucekm aparece
  2. afirmar o project_id como string literal na chamada
  3. NUNCA inferir o projeto pelo nome do conector ou da ferramenta
```

- **`exec_ddl` NÃO faz DDL** — achado do M2 (2026-08-30): `stock_research.db.execute()` com
  `create table` retorna **`403 permission denied for schema public`**. O `service_role` faz
  DML (o `delete from instrument_lifecycle` do pipeline funciona) mas **não tem `CREATE` em
  `public`**. O Handoff §9 oferecia `exec_ddl` como alternativa para migration — **está
  errado, corrigido aqui**.
- **Conectores MCP nesta máquina** (dois, tokens de contas diferentes, o nome não distingue):
  - `mcp__claude_ai_Supabase__*` — exige `project_id`. Validado: `list_projects` devolve
    **um único** projeto, `bdppudbcjosznkfucekm` "B3 FOCUS", `sa-east-1`, PG 17. É o B3.
  - `mcp__supabase__*` — sem `project_id`, aponta para outro app. **Nunca usar para B3.**
- Migrations novas: **aditivas, compatíveis, reversíveis (`drop table`/`drop column`),
  testadas** (`fase3.md` §1, Handoff §9).

### `instruments.active` — escopo operacional, NÃO vigência histórica

`active = true` é o gate de escopo dos pipelines, usado em 6 lugares:
`prices.py` (`sync-prices --all`), `news.py` (`sync-news --all`), `fundamentals_ingest.py`,
`audit.py`, e os `--all` de `compute-multiples` / `compute-quality`.

**Não significa "negocia hoje"**: PETR3/ITUB3 estão `active=false` e são negociados agora —
ficam fora por decisão de escopo, não por delisting.

Consequências obrigatórias:

- Instrumentos históricos entram `active=false`. Inerte por construção — nenhum pipeline os
  puxa. **Nunca** ligar `active=true` para forçar ingestão.
- **Vigência é respondida por `instrument_lifecycle`**, fonte única.
- `instruments.valid_from` / `valid_to` (existem, NULL, nunca usados) **permanecem NULL** —
  não duplicar o lifecycle.

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

## M1 — universo histórico / survivorship bias (**concluído**, 2026-08-30)

Ordem: Handoff v2 §14, passos 6–17. Schemas reais validados e mapeamento completo em
[`docs/historical_universe.md`](historical_universe.md) §3. **Resultado completo com números
reais: [`docs/historical_universe.md`](historical_universe.md) §6.**

Resumo: `companies` 3→2530; `company_lifecycle` 2566 linhas (663 registered / 1895 canceled /
8 suspended); `instrument_lifecycle` 1448 (778 c/ ticker, 670 s/ ticker pré-2018);
FCA 2010–2026; 0 `NOT_ELIGIBLE_DATA` (171 resolvidos por fallback); VALE 2012 (ON+PNA/VALE5)
≠ 2020+ (VALE3); anti-survivorship, invariância à proveniência, look-ahead-regression e
idempotência todos verdes; 33 testes novos; `pytest` 496 / `ruff` / `mypy` limpos.
**Não mergeado em `main`.**

### Validação de schema (passo 6) — feita 2026-08-30

- `cad_cia_aberta.csv` (disco): 46 colunas, cp1252, `;`. 2677 linhas —
  ATIVO 757 / CANCELADA 1912 / SUSPENSO 8. 1912/1912 canceladas com `DT_CANCEL` +
  `MOTIVO_CANCEL`. Snapshot (sem histórico de transições; sem ticker/ISIN/classe).
- `fca_cia_aberta_valor_mobiliario_YYYY.csv` (ZIPs 2010/2015/2016/2017/2018/2019/2020/2023
  baixados): 18 colunas, cp1252, `;`, **header byte-idêntico 2010→2023**. Colunas confirmadas
  e mapeadas em `historical_universe.md` §3.2.
- **Limitação real descoberta**: `Codigo_Negociacao` (ticker) **vazio 2010–2017**, ~60%
  preenchido 2018+. Datas de negociação/listagem completas o tempo todo. **Não é gatilho de
  escalada** (Handoff §16 pede escalar só se as *datas* forem inutilizáveis) — tratado dentro
  do envelope Handoff §4.2/§8.5/§11: `ticker` nullable, `share_class` discrimina, gaps com
  `quality_flag='incomplete'`, universo de teste semeado. Detalhe em `historical_universe.md`
  §3.3.
- **VALE**: FCA não tem linha histórica de VALE5/PNA; a estrutura de classes é provável via
  `share_count_history` (PN 2,1 bi em 2010–2016 → 12 em 2017). VALE5 entra semeada em
  `instrument_lifecycle` (`source='seed_manual'`, `estimated`). Detalhe §3.4.

### Entregas M1 (passos 7–17)

2 tabelas novas aditivas (`company_lifecycle`, `instrument_lifecycle` — schema no Handoff §4);
`sources/fundamentals/cvm_fca.py` + extensão da leitura do cadastro; `pipelines/historical_universe.py`;
CLI `sync-cvm-lifecycle`; `analytics/universe.py` (`select_investable_universe` puro +
`get_investable_universe_as_of(D)`, predicado do Handoff §6, sem expor `valid_to`/`listing_end`);
`config/backtest_universe_v1.yaml`; `ticker_aliases` semeada; caso VALE validado; os 18 testes
do Handoff §15.

## M2 — universo investível (**concluído com escalada pendente**, 2026-08-30)

Ordem: "HANDOFF PARA SONNET — M2" (Opus), 5 pré-requisitos + 10 passos.
**Resultado completo:** [`docs/historical_universe.md`](historical_universe.md) §7-§9.

Entregas: `source_reference_year_first` (migração `20260830055948`); validação de formato de
ticker + `resolution_status(row, D)`; camada investível com as 5 etapas e contagem por motivo;
`liquidity_metrics` (migração `20260830060954`, 24.822 linhas); `analytics/liquidity.py` puro
+ `pipelines/liquidity.py`; `analytics/universe_coverage.py` + CLI `universe-coverage`;
688 instrumentos históricos em `instruments` (`active=false`); 36 testes novos.
`pytest` 548 / `ruff` / `mypy` limpos.

**Nenhum preço foi baixado** (Opus regra 4): `daily_prices` continua com 6 instrumentos.

**Limiares NÃO escolhidos** (Opus regra 7): `config/backtest_universe_v1.yaml` mantém
`awaiting_opus_thresholds` e `null` — tabela de sensibilidade em `historical_universe.md` §9.3.

**ESCALADA ABERTA** (Opus regra 9): 8 das 17 datas (2010–2017) na banda `severe`
(`unresolved_rate` 99,8%). Universo investível vazio antes de 2018.

### Decisões abertas (Handoff §13) — resolução V1 adotada, reversível

- **Deslistadas em `instruments`**: só as do universo de teste + as que ganharem preço.
  `instrument_lifecycle` é a representação estrutural completa (não exige linha em
  `instruments`). Confirma Handoff §13.1 no lado conservador.
- **Fallback de `listing_start` NULL**: `company.valid_from` → primeira cotação →
  `NOT_ELIGIBLE_DATA` contabilizado (Handoff §5.1).
- **Liquidez**: adiada para o M2 (não é entrega do M1). Quando vier, tabela dedicada
  `liquidity_metrics` — nunca em `fundamental_metrics` (`fase3.md` §16, Handoff §13.3).
- **"Investível" com presença em índice**: fora da V1 (Handoff §11).
- **Preço de saída em delisting sem cotação**: pertence ao milestone do backtest, não ao M1.
- **Split temporal** (`fase3.md` §46): após medir cobertura real do universo expandido.
