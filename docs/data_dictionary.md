# Dicionário de dados

Schema completo (com comentários por tabela/coluna) está em
[`supabase/migrations/20260808000001_initial_schema.sql`](../supabase/migrations/20260808000001_initial_schema.sql) —
esse arquivo é a fonte da verdade. Este documento é um mapa de navegação, não uma cópia.

## Linhagem e auditoria

| Tabela | Papel |
|---|---|
| `ingestion_runs` | Uma linha por execução de pipeline. Toda tabela derivada aponta para o `run_id` que a produziu. |
| `raw_files` | Inventário de arquivos brutos em disco, com SHA256, para detectar mudança silenciosa na fonte. |
| `data_changes` | Histórico de correções retroativas feitas pelas fontes (ex.: Yahoo corrigindo série ajustada). |
| `manual_overrides` | Correções manuais, sem editar a camada bruta. |
| `quality_findings` | Anomalias detectadas pelos checks de qualidade (INFO/WARNING/ERROR). Base do `stock-research audit`. |

## Cadastro

| Tabela | Papel |
|---|---|
| `instruments` | Cadastro mestre. `ticker` **não** é identificador eterno — toda FK usa `instrument_id`. `company_id` (FK nullable) liga o ticker à companhia emissora. |
| `companies` | Entidade emissora (empresa legal, chave natural = CNPJ). Uma companhia tem 1+ instrumentos (PETR3 + PETR4). Fundamentos e valuation da Fase 2 agregam por `company_id`, nunca por `instrument_id` (fase2_plan.md §4/§19). Fase 3 M1 populou com **todas as 2530 companhias** do cadastro CVM (não só as 3 curadas). |
| `company_lifecycle` | Ciclo de vida da companhia (Fase 3 M1). `valid_from`/`valid_to` = **tempo efetivo** (`DT_REG`→`DT_CANCEL` da CVM) e são o **único gate** de elegibilidade do universo histórico. `registration_status` (registered/canceled/suspended), `reason`/`reason_category` (de `MOTIVO_CANCEL`), `issuer_status`. `source_available_from`/`source_observed_at`/`ingested_at` = **tempo de transação**, NUNCA gate (rename deliberado vs. `available_from`). Ver `docs/historical_universe.md` + `fase3_handoff_v2.md` §4.1. *(migração `20260830044424`)* |
| `instrument_lifecycle` | Ciclo de vida do instrumento/classe (Fase 3 M1). `valid_from`/`valid_to`/`listing_start`/`listing_end` = tempo efetivo (negociação real, da CVM FCA). `ticker` (`Codigo_Negociacao`) **nullable — NULL antes de 2018** (a FCA não informa; `quality_flag='incomplete'`); `share_class` + `company_id` discriminam. `source_*` = tempo de transação, nunca gate. Fonte `cvm_fca` + `seed_manual` (classes históricas que a FCA não lista, ex.: VALE5). Ver `docs/historical_universe.md` + `fase3_handoff_v2.md` §4.2. *(migração `20260830044442`)* |
| `ticker_aliases` | Tickers históricos (evita survivorship bias quando um código muda de dono). Fase 3 M1 semeou VALE5. |
| `company_aliases` | Termos de busca de notícias por empresa. `is_strong` distingue alias inequívoco ("Petrobras") de ambíguo ("Vale" isolado). |
| `trading_calendar` | Calendário de pregões derivado do benchmark. `trading_day_index` é a base de toda aritmética D+N. |

## Preços

| Tabela | Papel |
|---|---|
| `daily_prices` | OHLCV por instrumento e fonte. `close` e `adj_close` sempre separados. |
| `corporate_actions` | Dividendos, JCP, splits — como a fonte realmente classifica, nunca inferido. |
| `daily_returns` | Retornos derivados (preço e ajustado), volume normalizado, excesso vs. benchmark. |
| `price_validations` | Comparação cruzada entre provedores (yfinance × brapi). Divergência nunca corrigida automaticamente. |

## Notícias

| Tabela | Papel |
|---|---|
| `news_articles` | Artigos normalizados. URL canonicalizada, título normalizado + hash (camadas 1 e 2 de dedup). |
| `news_clusters` | Grupos de republicação (camada 3 de dedup, por similaridade de título). |
| `news_company_links` | Relação N:N artigo↔empresa, com `relevance_score` e `review_status`. |
| `news_analysis` | Categoria, sentimento, novelty score — por `(article_id, instrument_id, analysis_method, analysis_version)`. |

## Fundamentos

| Tabela | Papel |
|---|---|
| `cvm_documents` | Cabeçalho de cada documento CVM (DFP/ITR), com `available_from` — a base do point-in-time. `company_id` (FK nullable) em paralelo ao `instrument_id`. |
| `financial_statement_facts` | Fatos contábeis como reportados, `account_code` preservado, todas as versões/reapresentações. `company_id` (FK nullable) em paralelo ao `instrument_id`. |
| `fundamental_metrics` | Métricas derivadas, com `quality_flag` e `calculation_version`. `fundamental_metrics_v1` = base (Fase 1: revenue, net_income, ROE, ebit, capex, free_cash_flow, ...). `valuation_metrics_v1` = Fase 2 (`da`, `ebitda`, `pretax_income`, `income_tax`, `effective_tax_rate`, `nopat`, `invested_capital`, `roic`, `working_capital`; + `period_type='ttm'` para os fluxos) — bancos recebem `sector_inadequate` em ebitda/nopat/invested_capital/roic. `company_id` (FK nullable) para agregação. |
| `share_count_history` | Quantidade de ações por companhia/classe (ON/PN/TOTAL), point-in-time, da CVM FRE. `shares_issued` (Capital Integralizado) é o denominador de market cap; `free_float_shares` é free float (≠ emitidas−tesouraria); `treasury_shares`/`shares_outstanding` tipicamente NULL (FRE não traz consistente). `version` na chave → reapresentações preservadas. Ver `fase2_plan.md` §3/§24. |
| `valuation_multiples` | Market cap agregado por companhia (`Σ close_classe × shares_issued_classe`, point-in-time) + múltiplos (P/L, EV/EBITDA, FCF yield, earnings yield, P/VP, dividend yield). `basis='fy'` na V1 (`ttm` reservado). `price_inputs` (jsonb) preserva preço/qtd de cada classe. Bancos: `ev_ebitda`/`fcf_yield` NULL. Ver `fase2_plan.md` §4-5/§30. |
| `quality_scores` | Score de qualidade 0-100 por companhia (`quality_nonfinancial_v1`), **independente de preço**. `score_status` (`ok`/`incomplete`), `calibration_status='provisional'` sempre na V1, `components` (jsonb) com o detalhamento por bloco/subitem. Bancos → `incomplete` por desenho. Bandas em `config/quality_nonfinancial_v1.yaml`. Ver `fase2_plan.md` §8/§17. |
| `risk_free_assumptions` | Risk-free nominal em BRL por `as_of_date` — Tesouro Prefixado (regra determinística ~10a), `risk_free_rate = government_yield − brazil_default_spread`. Ver `fase2_plan.md` §21.2/§34. *(migração `20260827000006`, aplicada; ledger reconciliado no M0 da Fase 3)* |
| `equity_risk_premium_assumptions` | Snapshots de ERP Brasil (Damodaran, curados) — `mature_market_erp`, `country_default_spread`, `country_risk_premium`, `total_equity_risk_premium`. `available_from` é o que os testes de look-ahead checam. Ver `fase2_plan.md` §21.4/§34. *(migração `20260827000006`)* |
| `wacc_assumptions` | WACC por companhia/`as_of_date` — decomposição completa do §21.6 (risk-free, beta, cost of equity, cost of debt, pesos). `inputs` (jsonb). `quality_flag='estimated'` quando o custo de dívida usado não é o observado (fallback do nível 2 da DRE ou piso do risk-free) -- ver `fase2_plan.md` §36. *(migração `20260827000006`)* |
| `valuation_snapshots` | Resultado de cada método de valor justo por `(companhia, as_of, método, cenário)`. V1: `valuation_method='fcff'`, cenários pessimista/base/otimista, `fair_value_per_share`, `margin_of_safety`, `assumptions` (jsonb). Não sobrescreve silenciosamente. `quality_flag` herda o PIOR flag dos insumos (FCFF e WACC): premissa -- ΔWC assumido 0, capex de linha combinada, custo de dívida no piso, payout default -- nunca sai `ok`, e o `quality_reason` nomeia a premissa. Ver `fase2_plan.md` §10/§11/§12/§36. *(migração `20260827000006`)* |

**Consulta point-in-time correta:** sempre via `get_fundamentals_as_of(instrument_id, date)`
(`analytics/fundamentals.py`), nunca filtrando `reference_date <= date` diretamente — ver
[`docs/limitations.md`](limitations.md) e o motivo em `fase1.md §47/62`.

## Eventos e event study

| Tabela | Papel |
|---|---|
| `events` | Um evento por fato (não por artigo). `effective_trade_date` é o campo crítico. |
| `event_articles` | Ligação evento ↔ artigos que o reportam. |
| `event_studies` | Cabeçalho do estudo: alpha/beta, janela de estimação, `data_quality`. |
| `event_study_returns` | Retorno por horizonte (pregões), incluindo janela pré-evento e CAR. |

## Views

| View | Uso |
|---|---|
| `v_latest_prices` | Último preço por instrumento/fonte. |
| `v_price_returns` | Retornos com metadados do instrumento. |
| `v_canonical_news` | Um artigo por cluster (o canônico). |
| `v_news_with_company` | Notícias já ligadas à empresa, com relevância. |
| `v_events` | Eventos com contagem de artigos. |
| `v_fundamentals_as_reported` | Todos os fatos, todas as versões — use com filtro `available_from <= data`. |
| `v_fundamentals_latest_restated` | Última versão conhecida de cada conta. **Não usar em backtest** (contém reapresentações futuras). |
| `v_event_study_summary` | Formato largo do event study, para leitura humana e export. |
| `v_data_coverage` | Cobertura por instrumento — base do `stock-research status` e `audit`. |
| `v_manual_review_queue` | Candidatos a revisão humana (movimento sem evento, relevância ambígua, timestamp incerto). |

## Segurança

RLS habilitado em **todas** as tabelas, sem nenhuma policy — o pipeline usa `service_role`
(que ignora RLS); qualquer chave pública que vaze não lê nada. Ver
[`docs/architecture.md`](architecture.md#segurança).
