-- =============================================================================
-- Fase 3 -- M1: ciclo de vida do instrumento (ticker/classe) -- universo
-- historico / survivorship.
--
-- Ver docs/fase3_handoff_v2.md §4.2 + docs/historical_universe.md §3.2-§3.4.
--
-- REGRA BITEMPORAL: mesma de company_lifecycle. Gate de elegibilidade =
-- valid_from / valid_to / listing_start / listing_end (TEMPO EFETIVO).
-- source_* / ingested_at = TEMPO DE TRANSACAO, nunca gate.
--
-- Fonte: CVM FCA (fca_cia_aberta_valor_mobiliario_YYYY.csv). Limitacao real
-- (docs/historical_universe.md §3.3): Codigo_Negociacao vazio 2010-2017 -> a
-- coluna `ticker` e nullable; `share_class` + `company_id` discriminam. As
-- datas de negociacao/listagem estao completas o tempo todo.
--
-- Aditiva e reversivel. RLS sem policy.
-- =============================================================================

create table public.instrument_lifecycle (
  instrument_lifecycle_id  bigint generated always as identity primary key,
  company_id               bigint      not null references public.companies (company_id)
                                         on delete cascade,
  instrument_id            bigint      references public.instruments (instrument_id)
                                         on delete set null,   -- NULL quando nao ha ticker cadastrado

  -- ---- TEMPO EFETIVO (unico gate de elegibilidade) -------------------------
  valid_from               date        not null,   -- Data_Inicio_Negociacao (fallback: listing_start)
  valid_to                 date,                    -- Data_Fim_Negociacao; NULL = vigente (ver derivacao no pipeline)
  listing_start            date,                    -- Data_Inicio_Listagem
  listing_end              date,                    -- Data_Fim_Listagem

  -- ---- atributos ----------------------------------------------------------
  ticker                   text,                    -- Codigo_Negociacao (NULL antes de 2018 -- limitacao da fonte)
  share_class              text        not null     -- ON / PN / PNA / PNB / UNT / ...
                             check (share_class in ('ON','PN','PNA','PNB','PNC','PND','UNT','DR','OTHER')),
  isin                     text,
  market                   text                     -- Mercado normalizado
                             check (market is null or market in ('bolsa','balcao_organizado','balcao_nao_organizado','estrangeiro')),
  listing_venue            text,                    -- Sigla_Entidade_Administradora (BM&FBOVESPA -> B3)
  segment                  text,                    -- Segmento (Novo Mercado, N2, ...)
  trading_status           text        not null default 'trading'
                             check (trading_status in ('trading','suspended','delisted','unknown')),

  -- ---- TEMPO DE TRANSACAO (proveniencia -- NUNCA gate) --------------------
  source                   text        not null default 'cvm_fca',
  source_reference_year     int,                    -- Data_Referencia (ano do FCA)
  source_available_from    timestamptz,             -- DT_RECEB do indice FCA (NULL se ausente)
  source_observed_at       timestamptz not null,    -- quando o pipeline observou (ano do FCA, fim do dia)
  ingested_at              timestamptz not null default now(),
  run_id                   bigint      references public.ingestion_runs (run_id),

  quality_flag             text        not null default 'ok'
                             check (quality_flag in ('ok','estimated','incomplete','missing_input','inconsistent')),
  quality_reason           text,
  created_at               timestamptz not null default now(),

  constraint instrument_lifecycle_key
    unique (company_id, share_class, ticker, valid_from, source),
  constraint instrument_lifecycle_valid_range
    check (valid_to is null or valid_from is null or valid_to >= valid_from),
  constraint instrument_lifecycle_listing_range
    check (listing_end is null or listing_start is null or listing_end >= listing_start)
);

comment on table public.instrument_lifecycle is
  'Ciclo de vida do instrumento (ticker/classe de acao) -- universo historico, '
  'Fase 3 M1. valid_from / valid_to / listing_start / listing_end sao TEMPO '
  'EFETIVO (negociacao real) e sao o UNICO gate de elegibilidade do universo. '
  'source_* / ingested_at sao TEMPO DE TRANSACAO -- proveniencia apenas, NUNCA '
  'filtro de elegibilidade. ticker e NULL antes de 2018 (FCA nao informa). '
  'Ver docs/historical_universe.md.';

comment on column public.instrument_lifecycle.ticker is
  'Codigo_Negociacao da FCA. NULL 2010-2017 (fonte nao informa) -> quality_flag '
  'incomplete nesse caso. share_class + company_id discriminam o instrumento.';
comment on column public.instrument_lifecycle.valid_to is
  'TEMPO EFETIVO. NULL = vigente. So responde "negociava em D?" -- nunca exposto '
  'a camada de estrategia (Handoff §5.3).';

create index instrument_lifecycle_company_valid_idx
  on public.instrument_lifecycle (company_id, valid_from);
create index instrument_lifecycle_listing_idx
  on public.instrument_lifecycle (listing_start, listing_end);
create index instrument_lifecycle_ticker_idx
  on public.instrument_lifecycle (ticker);

alter table public.instrument_lifecycle enable row level security;
