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

### Infra / banco (Handoff §7.2, §9, §12.12)

- Projeto B3 = `bdppudbcjosznkfucekm` "B3 FOCUS". **Dois conectores MCP nesta sessão:**
  - `mcp__claude_ai_Supabase__*` (conta do Renan, exige `project_id`) → enxerga o projeto
    **certo**. É o caminho sancionado para DDL / `apply_migration` / ledger.
  - `mcp__supabase__*` (sem `project_id`) → app de saúde/CRM. **Nunca usar para B3.**
- Leitura/escrita de dados: `stock_research.db` (REST/PostgREST + RPC `exec_sql`/`exec_ddl`).
- DDL de migration: `mcp__claude_ai_Supabase__apply_migration` com `project_id` explícito
  (mantém o ledger correto automaticamente) — ou `exec_ddl` + insert manual no ledger.
- Ledger `supabase_migrations.schema_migrations` não aceita `exec_sql`/`exec_ddl` (403, sem
  grant) — reconciliado no M0 via `mcp__claude_ai_Supabase__execute_sql`.
- Migrations novas: **aditivas, compatíveis, reversíveis (`drop table`), testadas**
  (`fase3.md` §1, Handoff §9).

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

Ordem: Handoff v2 §14, passos 6–17. Schemas reais validados e mapeamento completo em
[`docs/historical_universe.md`](historical_universe.md) §3.

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
