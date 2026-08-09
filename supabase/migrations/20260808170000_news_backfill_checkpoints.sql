-- Fase 1.1: checkpoint persistente do backfill historico de noticias (GDELT).
--
-- O backfill fatia o periodo pedido em janelas curtas (semana) por idioma e
-- pode ser interrompido a qualquer momento por rate limit real do provedor
-- (fase1.1 13-14: "nunca reiniciar anos de coleta"). Esta tabela grava o
-- resultado de cada janela ja tentada para permitir retomar de onde parou.
--
-- ``status`` distingue explicitamente "sucesso com resultado", "sucesso sem
-- resultado" e as varias formas de falha (fase1.1 12: "nao confundir
-- resultado vazio com sucesso"). "0 noticias encontradas" so e valido quando
-- status = 'success_empty' -- nunca quando a chamada falhou.
create table public.news_backfill_checkpoints (
  checkpoint_id    bigint generated always as identity primary key,
  provider         text        not null,
  instrument_id    bigint      not null references public.instruments (instrument_id),
  language         text        not null,
  window_start     date        not null,
  window_end       date        not null,
  status           text        not null
    check (status in (
      'success_with_results', 'success_empty', 'rate_limited',
      'timeout', 'http_error', 'parse_error'
    )),
  articles_fetched integer     not null default 0,
  attempts         integer     not null default 0,
  last_attempt_at  timestamptz,
  next_retry_at    timestamptz,
  error_message    text,
  run_id           bigint references public.ingestion_runs (run_id),
  updated_at       timestamptz not null default now(),
  constraint news_backfill_checkpoints_key
    unique (provider, instrument_id, language, window_start, window_end)
);

comment on table public.news_backfill_checkpoints is
  'Progresso do backfill historico de noticias por janela/idioma. Permite retomar sem reprocessar janelas ja resolvidas (fase1.1 14).';
comment on column public.news_backfill_checkpoints.status is
  'success_with_results | success_empty | rate_limited | timeout | http_error | parse_error -- nunca inferir sucesso de uma janela vazia sem checar isto.';

create trigger news_backfill_checkpoints_set_updated_at
  before update on public.news_backfill_checkpoints
  for each row execute function public.set_updated_at();

alter table public.news_backfill_checkpoints enable row level security;
