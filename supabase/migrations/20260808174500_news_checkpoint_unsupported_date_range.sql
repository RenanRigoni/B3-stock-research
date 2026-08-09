-- Fase 1.1: o GDELT DOC API rejeita explicitamente janelas fora da cobertura
-- historica real dele (HTTP 200, corpo "Invalid query start date.") -- nao e
-- rate limit, e a fonte dizendo que aquela data nao existe pra ela. Retry
-- nunca resolve isso, entao precisa de um status proprio, terminal (nunca
-- reprocessado num resume), distinto das falhas retomaveis.
alter table public.news_backfill_checkpoints
  drop constraint news_backfill_checkpoints_status_check;

alter table public.news_backfill_checkpoints
  add constraint news_backfill_checkpoints_status_check
  check (status in (
    'success_with_results', 'success_empty', 'rate_limited',
    'timeout', 'http_error', 'parse_error', 'unsupported_date_range'
  ));
