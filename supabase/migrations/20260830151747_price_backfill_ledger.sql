-- =============================================================================
-- Fase 3 -- M2.1 (Bloco 2): ledger do backfill historico de precos.
--
-- Ver "OPUS -- PLANO M2.1" + "HANDOFF PARA SONNET -- M2.1 (revisao 2)".
--
-- price_backfill_runs     : uma linha por execucao de sync-historical-prices
--                           (inclui --dry-run).
-- price_backfill_attempts : uma linha por (run, instrumento) -- checkpoint,
--                           resume, e a base do relatorio M2.1A. Nenhum
--                           instrumento some entre etapas: mesmo reprovado
--                           tem linha com motivo.
--
-- Aditivas, reversiveis (drop table). RLS sem policy.
-- =============================================================================

create table public.price_backfill_runs (
  backfill_run_id     bigint generated always as identity primary key,
  batch_label         text        not null,
  batch_file          text,
  batch_file_sha256   text,
  provider            text        not null default 'yfinance',
  provider_version    text,
  params              jsonb       not null default '{}'::jsonb,
  dry_run             boolean     not null default false,

  requested           int         not null default 0,
  attempted           int         not null default 0,
  succeeded           int         not null default 0,
  empty_series        int         not null default 0,
  symbol_not_found    int         not null default 0,
  failed              int         not null default 0,
  rows_written        int         not null default 0,
  rows_out_of_window  int         not null default 0,
  critical_findings   int         not null default 0,

  started_at          timestamptz not null default now(),
  finished_at         timestamptz,
  status              text        not null default 'running'
                        check (status in ('running','success','failed','aborted')),
  run_id             bigint      references public.ingestion_runs (run_id)
);

comment on table public.price_backfill_runs is
  'Uma linha por execucao de sync-historical-prices (Fase 3 M2.1). --dry-run '
  'grava com dry_run=true e nao baixa nada. batch_file_sha256 congela o lote '
  'para reprodutibilidade.';

create table public.price_backfill_attempts (
  attempt_id             bigint generated always as identity primary key,
  backfill_run_id        bigint      not null
                           references public.price_backfill_runs (backfill_run_id) on delete cascade,
  instrument_id          bigint      not null
                           references public.instruments (instrument_id) on delete cascade,
  ticker                 text        not null,
  company_id             bigint      references public.companies (company_id),
  provider               text        not null default 'yfinance',
  provider_symbol        text        not null,

  lifecycle_valid_from   date,
  lifecycle_valid_to     date,
  price_window_from      date,
  price_window_to        date,
  price_window_precision  text,      -- from_precision da instrument_price_window

  requested_start        date,
  requested_end          date,
  returned_first_date    date,
  returned_last_date     date,
  row_count              int         not null default 0,   -- linhas devolvidas pelo provedor
  rows_written           int         not null default 0,   -- linhas DENTRO da janela -> daily_prices
  rows_out_of_window     int         not null default 0,   -- descartadas (ticker_identity_not_proven / after)
  expected_trading_days  int,
  gap_count              int         not null default 0,
  max_gap_days           int         not null default 0,

  attempt_count          int         not null default 1,
  status                 text        not null
                           check (status in ('pending','resolved','empty_series',
                                             'symbol_not_found','failed',
                                             'skipped_out_of_scope','suspect_history','dry_run')),
  quality_flag           text        not null default 'ok'
                           check (quality_flag in ('ok','estimated','incomplete',
                                                   'provider_no_data_delisted','suspect','missing_input')),
  quality_reason         text,
  checks                 jsonb       not null default '{}'::jsonb,   -- resultado dos 8 checks
  error_message          text,
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now(),

  constraint price_backfill_attempts_key unique (backfill_run_id, instrument_id)
);

comment on table public.price_backfill_attempts is
  'Uma linha por (backfill_run, instrumento) -- Fase 3 M2.1. Checkpoint/resume '
  'e base do relatorio. rows_written = so o que caiu DENTRO da janela canonica '
  '(instrument_price_window); rows_out_of_window fica no bruto/ledger, nunca em '
  'daily_prices.';

create index price_backfill_attempts_run_idx
  on public.price_backfill_attempts (backfill_run_id, status);
create index price_backfill_attempts_instrument_idx
  on public.price_backfill_attempts (instrument_id);

alter table public.price_backfill_runs enable row level security;
alter table public.price_backfill_attempts enable row level security;
