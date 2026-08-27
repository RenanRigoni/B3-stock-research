-- =============================================================================
-- Fase 2 -- Seções 4 e 5: market cap por companhia (Σ preço_classe ×
-- shares_issued_classe) e múltiplos point-in-time.
--
-- V1 usa base FY (último exercício anual disponível, com available_from <=
-- as_of_date). base 'ttm' fica reservada para o incremento seguinte (§5 pede
-- TTM como padrão, mas exige EBITDA trimestral isolado -- subprojeto próprio).
--
-- Toda linha preserva os insumos (preços, quantidade de ações, lucro/EBITDA/
-- FCF/equity/net_debt usados) para o cálculo ser reproduzível e nunca virar
-- "R$ XX,XX" sem rastreio (fase2_plan.md 11-12).
-- =============================================================================

create table public.valuation_multiples (
  multiple_id           bigint generated always as identity primary key,
  company_id            bigint      not null references public.companies (company_id)
                                      on delete cascade,
  as_of_date            date        not null,
  basis                 text        not null default 'fy'
                          check (basis in ('fy', 'ttm')),

  market_cap            numeric(30, 2),
  enterprise_value      numeric(30, 2),

  price_earnings        numeric(24, 6),
  ev_ebitda             numeric(24, 6),
  fcf_yield             numeric(20, 8),
  earnings_yield        numeric(20, 8),
  price_book            numeric(24, 6),
  dividend_yield        numeric(20, 8),

  -- insumos preservados (rastreio / reprodutibilidade)
  net_income_ref        numeric(30, 2),
  ebitda_ref            numeric(30, 2),
  fcf_ref               numeric(30, 2),
  equity_ref            numeric(30, 2),
  net_debt_ref          numeric(30, 2),
  dividends_ttm         numeric(30, 2),
  fundamentals_ref_date date,
  price_inputs          jsonb,

  calculation_version   text        not null default 'valuation_multiples_v1',
  quality_flag          text        not null default 'ok'
                          check (quality_flag in ('ok', 'estimated', 'incomplete',
                                                  'missing_input', 'sector_inadequate')),
  quality_reason        text,
  run_id                bigint      references public.ingestion_runs (run_id),
  created_at            timestamptz not null default now(),

  constraint valuation_multiples_key
    unique (company_id, as_of_date, basis, calculation_version)
);

comment on table public.valuation_multiples is
  'Market cap agregado por companhia e múltiplos point-in-time. market_cap = '
  'Σ (preço da classe × shares_issued da classe), preços e quantidades com '
  'trade_date/available_from <= as_of_date. price_inputs preserva os insumos '
  'para reproduzir o cálculo (fase2_plan.md 4, 5, 11).';

create index valuation_multiples_company_idx
  on public.valuation_multiples (company_id, as_of_date desc);

alter table public.valuation_multiples enable row level security;
