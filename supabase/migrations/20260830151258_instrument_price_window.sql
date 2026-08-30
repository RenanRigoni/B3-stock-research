-- =============================================================================
-- Fase 3 -- M2.1 (Bloco 1): janela canonica de preco por instrumento.
--
-- Ver "OPUS -- PLANO M2.1" + "HANDOFF PARA SONNET -- M2.1 (revisao 2)",
-- correcao normativa 1, e docs/historical_universe.md.
--
-- REGRA CANONICA:
--   price_valid_from = max(
--       company effective start,
--       class effective start (listing_start),
--       date(source_reference_year_first, 1, 1))
--   salvo excecao de continuidade INDEPENDENTE (config/price_continuity_exceptions.yaml).
--
--   Ser variante unica no FCA NAO prova que o simbolo era o mesmo antes do
--   primeiro ano observado -- o Yahoo retroprojeta serie de predecessor sob o
--   simbolo atual. Linhas anteriores a price_valid_from nao entram em
--   daily_prices canonico (ticker_identity_not_proven).
--
--   CASO B (SSBR3 -> ALSO3 -> ALOS3): variante truncada em
--   date(year_first_do_sucessor - 1, 12, 31).
--
-- Esta tabela e DERIVADA e recomputavel -- nunca fonte de verdade. A vigencia
-- efetiva continua sendo do instrument_lifecycle.
--
-- Aditiva, reversivel (drop table). RLS sem policy.
-- =============================================================================

create table public.instrument_price_window (
  instrument_id       bigint      primary key
                        references public.instruments (instrument_id) on delete cascade,

  price_valid_from    date,             -- limite canonico inferior (inclusivo)
  price_valid_to      date,             -- limite superior (inclusivo)

  from_precision      text        not null default 'unknown'
                        check (from_precision in ('day','year','unknown')),
  to_precision        text        not null default 'open'
                        check (to_precision in ('day','year','open')),

  basis               jsonb       not null default '{}'::jsonb,  -- candidatos + vinculo
  calculation_version text        not null default 'price_window_v1',
  computed_at         timestamptz not null default now(),
  run_id             bigint      references public.ingestion_runs (run_id)
);

comment on table public.instrument_price_window is
  'Janela canonica de preco por instrumento (Fase 3 M2.1 Bloco 1). DERIVADA e '
  'recomputavel: price_valid_from = max(company start, class listing_start, '
  '01/01/source_reference_year_first), salvo excecao de continuidade '
  'independente. Linhas do provedor fora desta janela NAO entram em '
  'daily_prices canonico. Vigencia efetiva permanece no instrument_lifecycle.';

comment on column public.instrument_price_window.from_precision is
  '"year" quando 01/01/source_reference_year_first e o limite vinculante '
  '(precisao anual -- nao inventar dia); "day" quando data efetiva de '
  'companhia/classe ou excecao de continuidade vincula; "unknown" sem evidencia.';

comment on column public.instrument_price_window.basis is
  'Candidatos considerados e qual vinculou cada extremo -- auditoria da regra.';

alter table public.instrument_price_window enable row level security;
