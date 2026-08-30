-- =============================================================================
-- Fase 3 -- M2: liquidez point-in-time por instrumento.
--
-- Ver "OPUS -- LIBERACAO M2", secao "liquidity storage", e
-- docs/historical_universe.md §8.
--
-- POR QUE TABELA PROPRIA (nao fundamental_metrics):
--   1. Volume/preco sao dado de MERCADO -- frequencia diaria, lifecycle e
--      granularidade diferentes de fato contabil.
--   2. `fundamental_metrics` e chaveada por documento CVM e carrega
--      `available_from` (o gate point-in-time da Fase 1/2). Liquidez nao tem
--      essa semantica -- e conhecida no mesmo dia. Enfiar ali obrigaria a
--      FABRICAR um `available_from`, poluindo exatamente o gate que a Fase 1/2
--      protege.
--
-- REGRA DURA -- volume financeiro usa `close` BRUTO, nunca `adj_close`:
--   `adj_close` e recalculado retroativamente a partir de proventos e splits
--   FUTUROS. Usa-lo aqui injetaria informacao do futuro numa metrica historica
--   e tornaria o numero irreproduzivel (a Fase 1.1 mediu: 81% das linhas do
--   PETR4 mudando entre duas leituras identicas da mesma serie ajustada).
--   Ha teste dedicado garantindo isso.
--
-- JANELAS em PREGOES (trading_calendar.trading_day_index), nunca dias
-- corridos. Somente `trade_date <= as_of_date`.
--
-- MEDIA sobre a janela ESPERADA (pregao sem negocio conta como volume zero) --
-- e a medida honesta de "quanto da para negociar por pregao"; dividir so pelos
-- dias com negocio superestimaria papel ilíquido. `trading_days_*` x
-- `expected_trading_days_*` expoem a esparsidade para quem quiser a outra
-- leitura.
--
-- MEDIANA de 60 alem da media: volume financeiro tem cauda pesada -- um unico
-- leilao infla a media de 20 dias e faz papel morto parecer liquido.
--
-- Aditiva, reversivel (drop table). RLS sem policy.
-- =============================================================================

create table public.liquidity_metrics (
  liquidity_id              bigint generated always as identity primary key,
  instrument_id             bigint      not null references public.instruments (instrument_id)
                                          on delete cascade,
  as_of_date                date        not null,

  avg_volume_20             numeric(24, 4),   -- quantidade media por pregao esperado
  avg_volume_60             numeric(24, 4),
  avg_financial_volume_20   numeric(24, 4),   -- BRL: sum(close_bruto * volume) / pregoes esperados
  avg_financial_volume_60   numeric(24, 4),
  median_financial_volume_60 numeric(24, 4),  -- robusto a leilao isolado

  trading_days_20           int,              -- pregoes COM negocio na janela
  trading_days_60           int,
  expected_trading_days_20  int,              -- pregoes do trading_calendar na janela
  expected_trading_days_60  int,

  source                    text        not null default 'daily_prices',
  price_field               text        not null default 'close',  -- NUNCA adj_close
  calculation_version       text        not null default 'liquidity_v1',
  quality_flag              text        not null default 'ok'
                              check (quality_flag in ('ok','estimated','incomplete','missing_input')),
  quality_reason            text,
  run_id                    bigint      references public.ingestion_runs (run_id),
  created_at                timestamptz not null default now(),

  constraint liquidity_metrics_key
    unique (instrument_id, as_of_date, calculation_version)
);

comment on table public.liquidity_metrics is
  'Liquidez point-in-time por instrumento (Fase 3 M2). Volume financeiro = '
  'close BRUTO x volume -- NUNCA adj_close (recalculado a partir de proventos/'
  'splits futuros = look-ahead). Janelas de 20/60 PREGOES via trading_calendar, '
  'somente trade_date <= as_of_date. Media sobre a janela ESPERADA (pregao sem '
  'negocio = volume zero); trading_days_* expoe a esparsidade. Deliberadamente '
  'fora de fundamental_metrics: dado de mercado nao tem semantica de '
  'available_from. Ver docs/historical_universe.md 8.';

comment on column public.liquidity_metrics.price_field is
  'Sempre "close" (bruto). Existe para provar no dado, nao so no codigo, que '
  'adj_close nunca foi usado em volume financeiro historico.';

create index liquidity_metrics_asof_idx
  on public.liquidity_metrics (as_of_date, instrument_id);
create index liquidity_metrics_instrument_idx
  on public.liquidity_metrics (instrument_id, as_of_date desc);

alter table public.liquidity_metrics enable row level security;
