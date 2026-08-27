-- =============================================================================
-- Fase 2 -- Seções 8 e 17: Quality Score por companhia (independente de preço/
-- valuation). V1 = perfil não-financeiro (quality_nonfinancial_v1).
--
-- QUALITY e VALUATION ficam independentes: nenhum componente do score usa
-- preço, P/L, EV/EBITDA ou margem de segurança (fase2_plan.md 8, 17.1).
--
-- Bancos: o score fica score_status='incomplete' por desenho enquanto NIM /
-- eficiência / Basileia / inadimplência não tiverem fonte (fase2_plan.md 9, 18)
-- -- nunca um número artificial sobre campos sector_inadequate.
-- =============================================================================

create table public.quality_scores (
  quality_score_id     bigint generated always as identity primary key,
  company_id           bigint      not null references public.companies (company_id)
                                     on delete cascade,
  as_of_date           date        not null,
  profile              text        not null default 'nonfinancial'
                         check (profile in ('nonfinancial', 'bank')),
  methodology_version  text        not null default 'quality_nonfinancial_v1',

  score                numeric(6, 2),               -- 0..100; NULL quando incomplete
  score_status         text        not null
                         check (score_status in ('ok', 'incomplete')),
  calibration_status   text        not null default 'provisional',

  window_years         integer,                     -- exercícios fiscais efetivamente usados
  weight_covered       numeric(6, 2),               -- peso total (de 100) com dado suficiente
  components           jsonb,                       -- {bloco: {score, weight, status, subitems:[...]}}
  config_version       text,
  quality_reason       text,

  run_id               bigint      references public.ingestion_runs (run_id),
  created_at           timestamptz not null default now(),

  constraint quality_scores_key
    unique (company_id, as_of_date, profile, methodology_version)
);

comment on table public.quality_scores is
  'Score de qualidade da empresa (0-100), independente de preço/valuation. '
  'calibration_status="provisional" sempre na V1 (bandas calibradas por '
  'referência de mercado, não por validação estatística -- universo de 3 '
  'empresas). Bancos -> score_status="incomplete" por desenho (fase2_plan.md 8, 9, 17).';

create index quality_scores_company_idx
  on public.quality_scores (company_id, as_of_date desc);

alter table public.quality_scores enable row level security;
