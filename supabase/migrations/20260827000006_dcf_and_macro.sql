-- =============================================================================
-- Fase 2 -- Seções 10, 11, 12, 21: DCF FCFF (só não-financeiras) e as premissas
-- macro/mercado que ele consome.
--
-- Toda premissa é uma LINHA rastreável (value, source, as_of_date, quality_flag,
-- calculation_version) -- nunca um número solto no código (§10). O DCF V1 é
-- NOMINAL em BRL (§21.1).
-- =============================================================================

-- Risk-free nominal em BRL (Tesouro Prefixado, regra determinística ~10a; §21.2)
create table public.risk_free_assumptions (
  risk_free_id        bigint generated always as identity primary key,
  as_of_date          date        not null,
  government_yield     numeric(12, 8),      -- ponto médio bid/ask do título escolhido
  default_spread      numeric(12, 8),       -- brazil_default_spread subtraído (§21.6 passo 1)
  risk_free_rate      numeric(12, 8),       -- government_yield - default_spread
  bond_maturity       date,
  bond_base_date      date,                  -- Data Base da cotação usada
  bond_type           text,
  source              text        not null default 'tesouro_transparente',
  methodology         text        not null default 'prefixado_10y_v1',
  calculation_version text        not null default 'risk_free_v1',
  quality_flag        text        not null default 'ok'
                        check (quality_flag in ('ok','estimated','incomplete','missing_input','not_applicable')),
  quality_reason      text,
  run_id              bigint      references public.ingestion_runs (run_id),
  created_at          timestamptz not null default now(),
  constraint risk_free_assumptions_key unique (as_of_date, calculation_version)
);

-- Equity Risk Premium -- snapshots curados da Damodaran (§21.4). available_from
-- é o campo que os testes de look-ahead de valuation checam.
create table public.equity_risk_premium_assumptions (
  erp_id                     bigint generated always as identity primary key,
  snapshot_date              date        not null,
  available_from             timestamptz not null,
  country                    text        not null default 'Brazil',
  mature_market_erp          numeric(12, 8),
  country_default_spread     numeric(12, 8),
  country_risk_premium       numeric(12, 8),
  total_equity_risk_premium  numeric(12, 8),
  source                     text        not null default 'damodaran_ctryprem',
  methodology                text        not null default 'erp_snapshots_v1',
  calculation_version        text        not null default 'erp_v1',
  quality_flag               text        not null default 'ok',
  quality_reason             text,
  run_id                     bigint      references public.ingestion_runs (run_id),
  created_at                 timestamptz not null default now(),
  constraint erp_assumptions_key unique (snapshot_date, country, calculation_version)
);

-- WACC por empresa e as_of_date -- decomposição completa do §21.6.
create table public.wacc_assumptions (
  wacc_id                bigint generated always as identity primary key,
  company_id             bigint      not null references public.companies (company_id) on delete cascade,
  as_of_date             date        not null,
  risk_free_nominal_brl   numeric(12, 8),
  beta                    numeric(12, 8),
  beta_observations       integer,
  mature_market_erp       numeric(12, 8),
  country_risk_premium    numeric(12, 8),
  cost_of_equity          numeric(12, 8),
  pretax_cost_of_debt     numeric(12, 8),
  company_credit_spread   numeric(12, 8),
  tax_rate                numeric(12, 8),
  cost_of_debt            numeric(12, 8),
  equity_weight           numeric(12, 8),
  debt_weight             numeric(12, 8),
  wacc                    numeric(12, 8),
  inputs                  jsonb,
  calculation_version     text        not null default 'wacc_v1',
  quality_flag            text        not null default 'ok'
                            check (quality_flag in ('ok','estimated','incomplete','missing_input','sector_inadequate','not_applicable')),
  quality_reason          text,
  run_id                  bigint      references public.ingestion_runs (run_id),
  created_at              timestamptz not null default now(),
  constraint wacc_assumptions_key unique (company_id, as_of_date, calculation_version)
);

-- valuation_snapshots -- resultado de cada método de valor justo, reproduzível
-- e nunca sobrescrevendo silenciosamente (§12). Um snapshot por (empresa,
-- as_of, método, cenário).
create table public.valuation_snapshots (
  valuation_snapshot_id  bigint generated always as identity primary key,
  company_id             bigint      not null references public.companies (company_id) on delete cascade,
  as_of_date             date        not null,
  valuation_method       text        not null
                           check (valuation_method in ('fcff','residual_income','ddm','multiples_peers')),
  scenario               text        not null default 'base'
                           check (scenario in ('pessimista','base','otimista')),
  fair_value_per_share    numeric(20, 6),
  market_price_per_share  numeric(20, 6),
  margin_of_safety        numeric(12, 8),
  enterprise_value        numeric(30, 2),
  equity_value            numeric(30, 2),
  terminal_value_share    numeric(12, 8),   -- fração do EV que é valor terminal
  wacc                    numeric(12, 8),
  assumptions             jsonb,             -- premissas e intermediários do cálculo
  calculation_version     text        not null default 'fcff_v1',
  quality_flag            text        not null default 'ok'
                            check (quality_flag in ('ok','estimated','incomplete','missing_input','sector_inadequate','not_applicable')),
  quality_reason          text,
  run_id                  bigint      references public.ingestion_runs (run_id),
  created_at              timestamptz not null default now(),
  constraint valuation_snapshots_key
    unique (company_id, as_of_date, valuation_method, scenario, calculation_version)
);

create index risk_free_assumptions_asof_idx on public.risk_free_assumptions (as_of_date desc);
create index erp_assumptions_available_idx on public.equity_risk_premium_assumptions (available_from);
create index wacc_assumptions_company_idx on public.wacc_assumptions (company_id, as_of_date desc);
create index valuation_snapshots_company_idx
  on public.valuation_snapshots (company_id, as_of_date desc, valuation_method);

alter table public.risk_free_assumptions enable row level security;
alter table public.equity_risk_premium_assumptions enable row level security;
alter table public.wacc_assumptions enable row level security;
alter table public.valuation_snapshots enable row level security;
