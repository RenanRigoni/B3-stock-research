-- =============================================================================
-- Fase 1 -- Schema inicial (ver supabase/migrations/20260808000001_initial_schema.sql)
-- =============================================================================

create or replace function public.set_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = ''
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

comment on function public.set_updated_at is
  'Trigger helper: mantem updated_at sincronizado em UPDATE.';

-- 1. Linhagem e auditoria -----------------------------------------------------

create table public.ingestion_runs (
  run_id           bigint generated always as identity primary key,
  pipeline         text        not null,
  provider         text,
  ticker           text,
  started_at       timestamptz not null default now(),
  finished_at      timestamptz,
  status           text        not null default 'running'
                     check (status in ('running', 'success', 'partial', 'failed')),
  records_raw      bigint      not null default 0,
  records_inserted bigint      not null default 0,
  records_updated  bigint      not null default 0,
  records_rejected bigint      not null default 0,
  params           jsonb       not null default '{}'::jsonb,
  config_hash      text,
  code_version     text,
  error_message    text
);

comment on table public.ingestion_runs is
  'Uma linha por execucao de pipeline. Toda tabela derivada aponta para o run que a produziu.';

create index ingestion_runs_pipeline_started_idx
  on public.ingestion_runs (pipeline, started_at desc);

create table public.raw_files (
  raw_file_id   bigint generated always as identity primary key,
  file_path     text        not null,
  sha256        text        not null,
  provider      text        not null,
  source_url    text,
  content_type  text,
  bytes         bigint,
  downloaded_at timestamptz not null default now(),
  run_id        bigint      references public.ingestion_runs (run_id),
  constraint raw_files_path_hash_key unique (file_path, sha256)
);

comment on table public.raw_files is
  'Inventario de arquivos brutos em disco com checksum, para deteccao de mudanca na fonte.';

create table public.data_changes (
  change_id   bigint generated always as identity primary key,
  table_name  text        not null,
  entity_key  text        not null,
  field_name  text        not null,
  old_value   text,
  new_value   text,
  provider    text,
  reason      text,
  detected_at timestamptz not null default now(),
  run_id      bigint      references public.ingestion_runs (run_id)
);

comment on table public.data_changes is
  'Historico de alteracoes retroativas feitas pelas fontes (ex.: Yahoo corrigindo serie ajustada).';

create index data_changes_table_detected_idx
  on public.data_changes (table_name, detected_at desc);

create table public.manual_overrides (
  override_id  bigint generated always as identity primary key,
  entity_type  text        not null,
  entity_id    text        not null,
  field_name   text        not null,
  old_value    text,
  new_value    text        not null,
  reason       text        not null,
  is_active    boolean     not null default true,
  created_by   text,
  created_at   timestamptz not null default now(),
  updated_at   timestamptz not null default now()
);

comment on table public.manual_overrides is
  'Correcoes manuais. As transformacoes curated devem respeita-las sem editar a camada bruta.';

create unique index manual_overrides_active_key
  on public.manual_overrides (entity_type, entity_id, field_name)
  where is_active;

create trigger manual_overrides_set_updated_at
  before update on public.manual_overrides
  for each row execute function public.set_updated_at();

create table public.quality_findings (
  finding_id  bigint generated always as identity primary key,
  run_id      bigint      references public.ingestion_runs (run_id),
  pipeline    text        not null,
  check_name  text        not null,
  severity    text        not null check (severity in ('INFO', 'WARNING', 'ERROR')),
  entity_type text,
  entity_id   text,
  trade_date  date,
  message     text        not null,
  details     jsonb       not null default '{}'::jsonb,
  detected_at timestamptz not null default now(),
  resolved_at timestamptz
);

comment on table public.quality_findings is
  'Anomalias detectadas pelos checks de qualidade. Nada e descartado em silencio (fase1.md 22).';

create index quality_findings_open_idx
  on public.quality_findings (severity, detected_at desc)
  where resolved_at is null;

-- 2. Cadastro mestre ----------------------------------------------------------

create table public.instruments (
  instrument_id      bigint generated always as identity primary key,
  ticker             text        not null,
  yahoo_symbol       text,
  company_name       text        not null,
  legal_name         text,
  cnpj               text,
  cvm_code           text,
  isin               text,
  asset_type         text        not null default 'stock'
                       check (asset_type in ('stock', 'unit', 'bdr', 'etf', 'index',
                                             'fii', 'bond', 'other')),
  share_class        text,
  sector             text,
  subsector          text,
  segment            text,
  currency           text        not null default 'BRL',
  exchange           text        not null default 'B3',
  is_benchmark       boolean     not null default false,
  financial_company  boolean     not null default false,
  utility            boolean     not null default false,
  commodity_exposed  boolean     not null default false,
  holding            boolean     not null default false,
  active             boolean     not null default true,
  valid_from         date,
  valid_to           date,
  notes              text,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  constraint instruments_ticker_exchange_key unique (ticker, exchange),
  constraint instruments_valid_range_ck check (valid_to is null or valid_from is null
                                               or valid_to >= valid_from)
);

comment on table public.instruments is
  'Cadastro mestre. O ticker NAO e identificador eterno -- use instrument_id em toda FK.';
comment on column public.instruments.is_benchmark is
  'True para indices usados como referencia no event study (ex.: ^BVSP).';

create unique index instruments_yahoo_symbol_key
  on public.instruments (yahoo_symbol) where yahoo_symbol is not null;
create unique index instruments_cvm_code_key
  on public.instruments (cvm_code) where cvm_code is not null;
create index instruments_cnpj_idx on public.instruments (cnpj) where cnpj is not null;

create trigger instruments_set_updated_at
  before update on public.instruments
  for each row execute function public.set_updated_at();

create table public.ticker_aliases (
  alias_id      bigint generated always as identity primary key,
  instrument_id bigint      not null references public.instruments (instrument_id)
                              on delete cascade,
  ticker        text        not null,
  valid_from    date,
  valid_to      date,
  source        text,
  confidence    numeric(4, 3) check (confidence between 0 and 1),
  created_at    timestamptz not null default now(),
  constraint ticker_aliases_valid_range_ck check (valid_to is null or valid_from is null
                                                  or valid_to >= valid_from)
);

comment on table public.ticker_aliases is
  'Tickers historicos. Base para evitar survivorship bias quando empresas trocam de codigo.';

create index ticker_aliases_ticker_idx on public.ticker_aliases (ticker);
create index ticker_aliases_instrument_idx on public.ticker_aliases (instrument_id);

create table public.company_aliases (
  company_alias_id bigint generated always as identity primary key,
  instrument_id    bigint      not null references public.instruments (instrument_id)
                                 on delete cascade,
  alias            text        not null,
  alias_kind       text        not null default 'name'
                     check (alias_kind in ('name', 'legal_name', 'ticker', 'brand', 'other')),
  is_strong        boolean     not null default true,
  created_at       timestamptz not null default now(),
  constraint company_aliases_key unique (instrument_id, alias)
);

comment on table public.company_aliases is
  'Termos de busca por empresa. is_strong=false marca alias ambiguo que gera falso positivo.';

-- 3. Calendario de negociacao -------------------------------------------------

create table public.trading_calendar (
  exchange              text    not null default 'B3',
  trade_date            date    not null,
  is_trading_day        boolean not null,
  previous_trading_day  date,
  next_trading_day      date,
  trading_day_index     integer,
  source                text    not null,
  created_at            timestamptz not null default now(),
  primary key (exchange, trade_date)
);

comment on table public.trading_calendar is
  'Calendario derivado das datas reais do benchmark. Sabado/domingo/feriado nunca contam como D+1.';

create unique index trading_calendar_index_key
  on public.trading_calendar (exchange, trading_day_index)
  where trading_day_index is not null;

-- 4. Precos -------------------------------------------------------------------

create table public.daily_prices (
  price_id      bigint generated always as identity primary key,
  instrument_id bigint      not null references public.instruments (instrument_id)
                              on delete cascade,
  trade_date    date        not null,
  open          numeric(20, 6),
  high          numeric(20, 6),
  low           numeric(20, 6),
  close         numeric(20, 6),
  adj_close     numeric(20, 6),
  volume        numeric(24, 2),
  currency      text        not null default 'BRL',
  source        text        not null,
  source_symbol text,
  is_repaired   boolean     not null default false,
  raw_file      text,
  run_id        bigint      references public.ingestion_runs (run_id),
  ingested_at   timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  constraint daily_prices_key unique (instrument_id, trade_date, source),
  constraint daily_prices_high_low_ck check (high is null or low is null or high >= low)
);

comment on table public.daily_prices is
  'OHLCV diario por instrumento e fonte. Varias fontes coexistem para permitir validacao cruzada.';

create index daily_prices_instrument_date_idx
  on public.daily_prices (instrument_id, trade_date desc);
create index daily_prices_date_idx on public.daily_prices (trade_date);

create trigger daily_prices_set_updated_at
  before update on public.daily_prices
  for each row execute function public.set_updated_at();

create table public.corporate_actions (
  action_id     bigint generated always as identity primary key,
  instrument_id bigint      not null references public.instruments (instrument_id)
                              on delete cascade,
  action_date   date        not null,
  action_type   text        not null
                  check (action_type in ('dividend', 'jcp', 'split', 'reverse_split',
                                         'bonus', 'subscription', 'other')),
  value         numeric(20, 8),
  currency      text,
  ratio         numeric(20, 8),
  source        text        not null,
  raw_payload   jsonb,
  run_id        bigint      references public.ingestion_runs (run_id),
  ingested_at   timestamptz not null default now(),
  constraint corporate_actions_key unique nulls not distinct
    (instrument_id, action_date, action_type, source, value)
);

comment on table public.corporate_actions is
  'Proventos e eventos societarios. action_type reflete o que a fonte informa, sem inferencia.';

create index corporate_actions_instrument_date_idx
  on public.corporate_actions (instrument_id, action_date desc);

create table public.daily_returns (
  instrument_id           bigint  not null references public.instruments (instrument_id)
                                    on delete cascade,
  trade_date              date    not null,
  price_source            text    not null,
  close                   numeric(20, 6),
  adj_close               numeric(20, 6),
  return_1d_price         double precision,
  return_1d_adjusted      double precision,
  log_return_1d           double precision,
  volume                  numeric(24, 2),
  volume_avg_20           numeric(24, 2),
  volume_median_20        numeric(24, 2),
  volume_ratio_20         double precision,
  volume_zscore_20        double precision,
  volatility_20           double precision,
  benchmark_instrument_id bigint  references public.instruments (instrument_id),
  benchmark_return_1d     double precision,
  excess_return_1d        double precision,
  calculation_version     text    not null default 'returns_v1',
  run_id                  bigint  references public.ingestion_runs (run_id),
  created_at              timestamptz not null default now(),
  primary key (instrument_id, trade_date, price_source)
);

comment on table public.daily_returns is
  'Retornos derivados. Dia sem pregao nao recebe linha -- nunca preencher retorno artificialmente.';

create index daily_returns_date_idx on public.daily_returns (trade_date);

create table public.price_validations (
  validation_id  bigint generated always as identity primary key,
  instrument_id  bigint      not null references public.instruments (instrument_id)
                               on delete cascade,
  trade_date     date        not null,
  source_a       text        not null,
  source_b       text        not null,
  close_a        numeric(20, 6),
  close_b        numeric(20, 6),
  difference_abs numeric(20, 6),
  difference_pct double precision,
  status         text        not null check (status in ('ok', 'warning', 'error', 'missing')),
  run_id         bigint      references public.ingestion_runs (run_id),
  checked_at     timestamptz not null default now(),
  constraint price_validations_key unique (instrument_id, trade_date, source_a, source_b)
);

comment on table public.price_validations is
  'Comparacao cruzada entre provedores. Divergencia NAO e corrigida automaticamente (fase1.md 21).';

-- 5. Noticias -----------------------------------------------------------------

create table public.news_articles (
  article_id            bigint generated always as identity primary key,
  provider              text        not null,
  provider_id           text,
  url                   text        not null,
  canonical_url         text        not null,
  url_hash              text        not null,
  domain                text,
  source_name           text,
  title                 text,
  title_normalized      text,
  title_hash            text,
  language              text,
  country               text,
  source_country        text,
  published_at_utc      timestamptz,
  published_at_local    timestamptz,
  source_timezone       text,
  time_precision        text        not null default 'unknown'
                          check (time_precision in ('exact', 'hour', 'date_only', 'unknown')),
  seen_at               timestamptz,
  tone                  double precision,
  image_url             text,
  query_used            text,
  duplicate_cluster_id  bigint,
  is_cluster_canonical  boolean     not null default false,
  article_text          text,
  text_extraction_status text       not null default 'not_attempted'
                          check (text_extraction_status in ('success', 'unavailable', 'blocked',
                                                            'paywall', 'timeout', 'parse_error',
                                                            'not_attempted')),
  text_extracted_at     timestamptz,
  text_hash             text,
  raw_file              text,
  run_id                bigint      references public.ingestion_runs (run_id),
  ingested_at           timestamptz not null default now(),
  updated_at            timestamptz not null default now(),
  constraint news_articles_key unique (provider, url_hash)
);

comment on table public.news_articles is
  'Artigos normalizados. A ligacao com empresas fica em news_company_links -- nunca um ticker aqui.';
comment on column public.news_articles.duplicate_cluster_id is
  'Agrupa republicacoes do mesmo conteudo. 50 portais republicando != 50 eventos (fase1.md 31).';

create index news_articles_published_idx on public.news_articles (published_at_utc desc);
create index news_articles_cluster_idx on public.news_articles (duplicate_cluster_id);
create index news_articles_title_hash_idx on public.news_articles (title_hash);
create index news_articles_domain_idx on public.news_articles (domain);

create trigger news_articles_set_updated_at
  before update on public.news_articles
  for each row execute function public.set_updated_at();

create table public.news_clusters (
  cluster_id             bigint generated always as identity primary key,
  canonical_article_id   bigint      references public.news_articles (article_id)
                                       on delete set null,
  representative_title   text,
  article_count          integer     not null default 0,
  unique_domains         integer     not null default 0,
  first_seen             timestamptz,
  last_seen              timestamptz,
  dedup_method           text,
  dedup_version          text        not null default 'dedup_v1',
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now()
);

comment on table public.news_clusters is
  'Cluster de republicacoes. article_count/unique_domains medem repercussao, nao importancia.';

create trigger news_clusters_set_updated_at
  before update on public.news_clusters
  for each row execute function public.set_updated_at();

alter table public.news_articles
  add constraint news_articles_cluster_fk
  foreign key (duplicate_cluster_id)
  references public.news_clusters (cluster_id) on delete set null;

create table public.news_company_links (
  link_id            bigint generated always as identity primary key,
  article_id         bigint      not null references public.news_articles (article_id)
                                   on delete cascade,
  instrument_id      bigint      not null references public.instruments (instrument_id)
                                   on delete cascade,
  match_method       text        not null,
  relevance_score    numeric(4, 3) check (relevance_score between 0 and 1),
  match_terms        text[],
  is_primary_company boolean     not null default false,
  review_status      text        not null default 'auto'
                       check (review_status in ('auto', 'pending_review', 'confirmed', 'rejected')),
  created_at         timestamptz not null default now(),
  constraint news_company_links_key unique (article_id, instrument_id)
);

comment on table public.news_company_links is
  'Relacao N:N artigo/empresa. Uma noticia pode afetar varias empresas (fase1.md 29).';

create index news_company_links_instrument_idx
  on public.news_company_links (instrument_id, relevance_score desc);

create table public.news_analysis (
  analysis_id         bigint generated always as identity primary key,
  article_id          bigint      not null references public.news_articles (article_id)
                                    on delete cascade,
  instrument_id       bigint      references public.instruments (instrument_id) on delete cascade,
  category            text,
  subcategory         text,
  sentiment           text        check (sentiment in ('positive', 'neutral', 'negative', 'mixed',
                                                       'unknown')),
  sentiment_score     double precision,
  relevance_score     numeric(4, 3) check (relevance_score between 0 and 1),
  novelty_score       numeric(4, 3) check (novelty_score between 0 and 1),
  impact_score        numeric(4, 3) check (impact_score between 0 and 1),
  is_company_specific boolean,
  is_macro            boolean,
  is_sector           boolean,
  is_rumor            boolean,
  is_official_source  boolean,
  analysis_method     text        not null
                        check (analysis_method in ('heuristic', 'local_model', 'llm', 'manual')),
  analysis_model      text,
  analysis_version    text        not null,
  explanation         jsonb       not null default '{}'::jsonb,
  analyzed_at         timestamptz not null default now(),
  constraint news_analysis_key unique nulls not distinct
    (article_id, instrument_id, analysis_method, analysis_version)
);

comment on table public.news_analysis is
  'Classificacao de noticias. Metodo, modelo e versao ficam gravados -- resultados nao se misturam.';

create index news_analysis_article_idx on public.news_analysis (article_id);
create index news_analysis_category_idx on public.news_analysis (category);

-- 6. Fundamentos CVM ----------------------------------------------------------

create table public.cvm_documents (
  document_id       bigint generated always as identity primary key,
  cvm_code          text        not null,
  cnpj              text,
  instrument_id     bigint      references public.instruments (instrument_id) on delete set null,
  document_type     text        not null check (document_type in ('DFP', 'ITR', 'FRE', 'FCA',
                                                                  'IPE', 'other')),
  reference_date    date        not null,
  filing_received_at timestamptz,
  available_from    timestamptz,
  version           text,
  situation         text,
  source_file       text,
  source_url        text,
  run_id            bigint      references public.ingestion_runs (run_id),
  ingested_at       timestamptz not null default now(),
  constraint cvm_documents_key unique nulls not distinct
    (cvm_code, document_type, reference_date, version)
);

comment on table public.cvm_documents is
  'Cabecalho dos documentos CVM. Reapresentacoes viram novas versoes, nunca sobrescrevem (fase1.md 48).';
comment on column public.cvm_documents.available_from is
  'Momento a partir do qual o dado era publico. Fonte da verdade do point-in-time.';

create index cvm_documents_instrument_idx
  on public.cvm_documents (instrument_id, reference_date desc);
create index cvm_documents_available_idx on public.cvm_documents (available_from);

create table public.financial_statement_facts (
  fact_id             bigint generated always as identity primary key,
  document_id         bigint      references public.cvm_documents (document_id) on delete cascade,
  cvm_code            text        not null,
  cnpj                text,
  instrument_id       bigint      references public.instruments (instrument_id) on delete set null,
  document_type       text        not null,
  statement_type      text        not null,
  reference_date      date        not null,
  period_start        date,
  period_end          date,
  filing_received_at  timestamptz,
  available_from      timestamptz,
  version             text,
  account_code        text        not null,
  account_description text,
  value               numeric(30, 6),
  currency            text        not null default 'BRL',
  scale               integer     not null default 1,
  fiscal_year_order   text,
  is_consolidated     boolean,
  source_file         text,
  source_row_hash     text        not null,
  run_id              bigint      references public.ingestion_runs (run_id),
  ingested_at         timestamptz not null default now(),
  constraint financial_statement_facts_key unique (source_row_hash)
);

comment on table public.financial_statement_facts is
  'Fatos contabeis como reportados pela CVM, sem normalizacao destrutiva. account_code preservado.';

create index financial_statement_facts_lookup_idx
  on public.financial_statement_facts (instrument_id, account_code, reference_date desc);
create index financial_statement_facts_available_idx
  on public.financial_statement_facts (instrument_id, available_from desc);

create table public.fundamental_metrics (
  metric_id           bigint generated always as identity primary key,
  instrument_id       bigint      not null references public.instruments (instrument_id)
                                    on delete cascade,
  reference_date      date        not null,
  available_from      timestamptz not null,
  period_type         text        not null check (period_type in ('annual', 'quarterly',
                                                                  'ttm', 'ytd', 'point_in_time')),
  metric_name         text        not null,
  metric_value        numeric(30, 6),
  unit                text,
  calculation_version text        not null default 'fundamental_metrics_v1',
  source_document_ids bigint[],
  quality_flag        text        not null default 'ok'
                        check (quality_flag in ('ok', 'estimated', 'incomplete', 'not_applicable',
                                                'missing_input', 'sector_inadequate')),
  quality_reason      text,
  run_id              bigint      references public.ingestion_runs (run_id),
  created_at          timestamptz not null default now(),
  constraint fundamental_metrics_key
    unique (instrument_id, reference_date, period_type, metric_name, calculation_version)
);

comment on table public.fundamental_metrics is
  'Metricas derivadas. quality_flag=sector_inadequate impede aplicar net_debt/EBITDA a banco.';

create index fundamental_metrics_asof_idx
  on public.fundamental_metrics (instrument_id, metric_name, available_from desc);

-- 7. Eventos e event study ----------------------------------------------------

create table public.events (
  event_id                 bigint generated always as identity primary key,
  instrument_id            bigint      not null references public.instruments (instrument_id)
                                         on delete cascade,
  event_type               text        not null,
  event_subtype            text,
  event_title              text,
  event_description        text,
  event_time_utc           timestamptz,
  event_time_local         timestamptz,
  event_date               date        not null,
  effective_trade_date     date,
  time_precision           text        not null default 'unknown'
                             check (time_precision in ('exact', 'hour', 'date_only', 'unknown')),
  market_session_uncertain boolean     not null default false,
  scope                    text        not null default 'unknown'
                             check (scope in ('company_specific', 'sector', 'macro', 'unknown')),
  source_type              text        not null
                             check (source_type in ('news', 'corporate_action', 'cvm_filing',
                                                    'manual', 'price_anomaly', 'other')),
  source_id                text,
  relevance_score          numeric(4, 3) check (relevance_score between 0 and 1),
  sentiment                text,
  impact_score             numeric(4, 3) check (impact_score between 0 and 1),
  confidence               numeric(4, 3) check (confidence between 0 and 1),
  overlapping_event_count  integer     not null default 0,
  is_confounded            boolean     not null default false,
  news_explanation_status  text        not null default 'not_applicable'
                             check (news_explanation_status in ('resolved', 'partial',
                                                                'unresolved', 'not_applicable')),
  clustering_version       text        not null default 'event_clustering_v1',
  run_id                   bigint      references public.ingestion_runs (run_id),
  created_at               timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);

comment on table public.events is
  'Evento economico, nao artigo. Dezenas de materias sobre o mesmo fato geram UM evento.';

create index events_instrument_date_idx on public.events (instrument_id, event_date desc);
create index events_effective_date_idx on public.events (effective_trade_date);
create index events_type_idx on public.events (event_type);

create trigger events_set_updated_at
  before update on public.events
  for each row execute function public.set_updated_at();

create table public.event_articles (
  event_id     bigint  not null references public.events (event_id) on delete cascade,
  article_id   bigint  not null references public.news_articles (article_id) on delete cascade,
  relationship text    not null default 'reports'
                 check (relationship in ('reports', 'follow_up', 'context', 'contradicts',
                                         'republication')),
  is_primary   boolean not null default false,
  created_at   timestamptz not null default now(),
  primary key (event_id, article_id)
);

comment on table public.event_articles is
  'Ligacao evento <-> artigos que o reportam.';

create table public.event_studies (
  event_study_id          bigint generated always as identity primary key,
  event_id                bigint      not null references public.events (event_id)
                                        on delete cascade,
  instrument_id           bigint      not null references public.instruments (instrument_id)
                                        on delete cascade,
  effective_trade_date    date        not null,
  benchmark_instrument_id bigint      references public.instruments (instrument_id),
  price_series            text        not null default 'adjusted'
                            check (price_series in ('price', 'adjusted')),
  method                  text        not null
                            check (method in ('raw', 'market_adjusted', 'market_model')),
  estimation_window_start date,
  estimation_window_end   date,
  observations            integer,
  alpha                   double precision,
  beta                    double precision,
  r_squared               double precision,
  residual_std            double precision,
  low_sample              boolean     not null default false,
  volume_ratio_20         double precision,
  volume_zscore_20        double precision,
  volatility_pre_20       double precision,
  volatility_post_20      double precision,
  data_quality            text        not null default 'ok'
                            check (data_quality in ('ok', 'partial', 'insufficient')),
  method_version          text        not null default 'event_study_v1',
  run_id                  bigint      references public.ingestion_runs (run_id),
  calculated_at           timestamptz not null default now(),
  constraint event_studies_key unique (event_id, method, price_series, method_version)
);

comment on table public.event_studies is
  'Cabecalho do estudo de evento: metodo, janela de estimacao e parametros do market model.';

create index event_studies_instrument_idx
  on public.event_studies (instrument_id, effective_trade_date desc);

create table public.event_study_returns (
  event_study_id    bigint  not null references public.event_studies (event_study_id)
                              on delete cascade,
  horizon_days      integer not null,
  return_actual     double precision,
  benchmark_return  double precision,
  excess_return     double precision,
  expected_return   double precision,
  abnormal_return   double precision,
  car               double precision,
  end_trade_date    date,
  is_censored       boolean not null default false,
  primary key (event_study_id, horizon_days)
);

comment on table public.event_study_returns is
  'Retornos por horizonte, em pregoes. Horizonte negativo cobre a janela pre-evento.';

-- 8. Macro (apenas estrutura) -------------------------------------------------

create table public.macro_series (
  macro_id       bigint generated always as identity primary key,
  series_code    text        not null,
  series_name    text,
  source         text        not null,
  reference_date date        not null,
  value          numeric(30, 6),
  unit           text,
  available_from timestamptz,
  run_id         bigint      references public.ingestion_runs (run_id),
  ingested_at    timestamptz not null default now(),
  constraint macro_series_key unique (series_code, reference_date, source)
);

comment on table public.macro_series is
  'Reservado para Selic/CDI/IPCA/dolar/commodities. Estrutura criada; pipeline fica para depois.';

