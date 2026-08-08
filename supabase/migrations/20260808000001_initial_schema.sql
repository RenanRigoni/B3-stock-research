-- =============================================================================
-- Fase 1 -- Schema inicial
-- Sistema pessoal de analise historica de acoes da B3.
--
-- Traducao para Postgres/Supabase do schema descrito em fase1.md
-- (secoes 7, 8, 11, 13, 14, 16, 21, 22, 28, 29, 33, 38, 41, 46, 51, 58,
--  59, 60, 64, 65, 88, 93-99).
--
-- Principios inegociaveis refletidos aqui:
--   * point-in-time: todo fato tem "quando ficou disponivel", nao so
--     "a que periodo se refere" (available_from / filing_received_at);
--   * rastreabilidade: toda linha aponta para o run e o arquivo raw que a
--     originou (run_id / raw_file / source_row_hash);
--   * idempotencia: chaves naturais UNIQUE permitem upsert seguro;
--   * nada e sobrescrito em silencio: reapresentacoes e correcoes viram
--     novas versoes + registro em data_changes.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 0. Helpers
-- -----------------------------------------------------------------------------

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


-- -----------------------------------------------------------------------------
-- 1. Linhagem e auditoria (fase1.md 64, 65, 98, 99)
--    Criado primeiro porque quase todas as tabelas referenciam run_id.
-- -----------------------------------------------------------------------------

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
  -- O mesmo caminho pode ser rebaixado com conteudo diferente (ex.: a CVM
  -- reprocessa um ZIP). Guardamos as duas versoes, nunca sobrescrevemos.
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


-- -----------------------------------------------------------------------------
-- 2. Cadastro mestre (fase1.md 7, 8, 50)
-- -----------------------------------------------------------------------------

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
  -- Benchmarks (^BVSP) vivem aqui como instrumento proprio: um unico
  -- pipeline de precos serve acao e indice (fase1.md 15).
  is_benchmark       boolean     not null default false,
  -- Flags setoriais: impedem aplicar metrica inadequada, nao resolvem
  -- valuation setorial nesta fase (fase1.md 50).
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


-- Aliases textuais usados na busca de noticias (fase1.md 26).
create table public.company_aliases (
  company_alias_id bigint generated always as identity primary key,
  instrument_id    bigint      not null references public.instruments (instrument_id)
                                 on delete cascade,
  alias            text        not null,
  alias_kind       text        not null default 'name'
                     check (alias_kind in ('name', 'legal_name', 'ticker', 'brand', 'other')),
  -- Alias "forte" identifica a empresa sozinho ("Petrobras").
  -- Alias fraco gera ruido e so conta acompanhado ("Vale" -> tambem substantivo comum).
  is_strong        boolean     not null default true,
  created_at       timestamptz not null default now(),
  constraint company_aliases_key unique (instrument_id, alias)
);

comment on table public.company_aliases is
  'Termos de busca por empresa. is_strong=false marca alias ambiguo que gera falso positivo.';


-- -----------------------------------------------------------------------------
-- 3. Calendario de negociacao (fase1.md 16)
-- -----------------------------------------------------------------------------

create table public.trading_calendar (
  exchange              text    not null default 'B3',
  trade_date            date    not null,
  is_trading_day        boolean not null,
  previous_trading_day  date,
  next_trading_day      date,
  -- Indice sequencial apenas sobre pregoes: D+N e aritmetica sobre esta coluna,
  -- nunca sobre dias corridos.
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


-- -----------------------------------------------------------------------------
-- 4. Precos (fase1.md 11, 12, 13, 14, 21, 22)
-- -----------------------------------------------------------------------------

create table public.daily_prices (
  price_id      bigint generated always as identity primary key,
  instrument_id bigint      not null references public.instruments (instrument_id)
                              on delete cascade,
  trade_date    date        not null,
  -- OHLC bruto e preco ajustado convivem separados. Nunca substituir close por
  -- adj_close em silencio (fase1.md 12).
  open          numeric(20, 6),
  high          numeric(20, 6),
  low           numeric(20, 6),
  close         numeric(20, 6),
  adj_close     numeric(20, 6),
  volume        numeric(24, 2),
  currency      text        not null default 'BRL',
  source        text        not null,
  source_symbol text,
  -- yfinance repair=True altera valores. Marcamos para nunca tratar dado
  -- reparado como se fosse a fonte original (fase1.md 10).
  is_repaired   boolean     not null default false,
  raw_file      text,
  run_id        bigint      references public.ingestion_runs (run_id),
  ingested_at   timestamptz not null default now(),
  updated_at    timestamptz not null default now(),
  -- Chave logica da fase1.md 11: multiplas fontes coexistem para a mesma data.
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
  -- Quando a fonte nao diferencia dividendo de JCP, gravamos o tipo que ela
  -- realmente informa. Nunca inventar classificacao (fase1.md 13).
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


-- -----------------------------------------------------------------------------
-- 5. Noticias (fase1.md 28-37)
-- -----------------------------------------------------------------------------

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
  -- Nunca fingir precisao inexistente: artigo so com data recebe date_only
  -- e a politica conservadora de effective_trade_date se aplica (fase1.md 39).
  time_precision        text        not null default 'unknown'
                          check (time_precision in ('exact', 'hour', 'date_only', 'unknown')),
  seen_at               timestamptz,
  tone                  double precision,
  image_url             text,
  query_used            text,
  -- Deduplicacao (fase1.md 30): copias sao preservadas, nunca apagadas.
  duplicate_cluster_id  bigint,
  is_cluster_canonical  boolean     not null default false,
  -- Texto completo e opcional; falha na extracao nao pode quebrar o pipeline.
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
  -- sentiment e impact sao coisas diferentes: "investimento de R$ 100 bi" pode
  -- ser positivo na linguagem e ambiguo no impacto economico (fase1.md 33).
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
  -- Nada de caixa preta: o score precisa ser explicavel (fase1.md 119).
  explanation         jsonb       not null default '{}'::jsonb,
  analyzed_at         timestamptz not null default now(),
  -- Classificacoes de metodos/versoes diferentes coexistem sem se misturar.
  constraint news_analysis_key unique nulls not distinct
    (article_id, instrument_id, analysis_method, analysis_version)
);

comment on table public.news_analysis is
  'Classificacao de noticias. Metodo, modelo e versao ficam gravados -- resultados nao se misturam.';

create index news_analysis_article_idx on public.news_analysis (article_id);
create index news_analysis_category_idx on public.news_analysis (category);


-- -----------------------------------------------------------------------------
-- 6. Fundamentos CVM (fase1.md 42-52)
-- -----------------------------------------------------------------------------

create table public.cvm_documents (
  document_id       bigint generated always as identity primary key,
  cvm_code          text        not null,
  cnpj              text,
  instrument_id     bigint      references public.instruments (instrument_id) on delete set null,
  document_type     text        not null check (document_type in ('DFP', 'ITR', 'FRE', 'FCA',
                                                                  'IPE', 'other')),
  reference_date    date        not null,
  -- Data em que o documento foi entregue/publicado. E ela, nao reference_date,
  -- que define o que o mercado sabia (fase1.md 47).
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
  -- account_code (CD_CONTA) e o identificador estavel da conta. Nunca descartar.
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
  -- Obrigatorio. Sem available_from nao existe analise point-in-time honesta.
  available_from      timestamptz not null,
  period_type         text        not null check (period_type in ('annual', 'quarterly',
                                                                  'ttm', 'ytd', 'point_in_time')),
  metric_name         text        not null,
  metric_value        numeric(30, 6),
  unit                text,
  calculation_version text        not null default 'fundamental_metrics_v1',
  source_document_ids bigint[],
  -- Conta ausente vira NULL + motivo. Nunca inventar valor (fase1.md 49).
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


-- -----------------------------------------------------------------------------
-- 7. Eventos e event study (fase1.md 38-41, 53-60, 93-96)
-- -----------------------------------------------------------------------------

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
  -- Primeiro pregao que poderia reagir. Campo critico (fase1.md 39).
  effective_trade_date     date,
  time_precision           text        not null default 'unknown'
                             check (time_precision in ('exact', 'hour', 'date_only', 'unknown')),
  market_session_uncertain boolean     not null default false,
  -- Escopo: se o minerio cai 10% e todas as mineradoras caem, nao e noticia da Vale.
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
  -- Eventos simultaneos contaminam a leitura: balanco + troca de CEO no mesmo dia
  -- nao permitem atribuir a reacao a um so (fase1.md 93).
  overlapping_event_count  integer     not null default 0,
  is_confounded            boolean     not null default false,
  -- Movimento forte sem noticia encontrada fica "unresolved". Nao inventamos causa.
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
  -- Amostra pequena na janela de estimacao invalida alpha/beta: sinalizar, nao esconder.
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
  -- Horizonte em PREGOES, nao dias corridos. Negativo = janela pre-evento
  -- (fase1.md 59): a noticia pode apenas confirmar o que ja estava no preco.
  horizon_days      integer not null,
  return_actual     double precision,
  benchmark_return  double precision,
  excess_return     double precision,
  expected_return   double precision,
  abnormal_return   double precision,
  car               double precision,
  end_trade_date    date,
  -- Sem horizonte completo: NULL + is_censored, nunca um numero inventado.
  is_censored       boolean not null default false,
  primary key (event_study_id, horizon_days)
);

comment on table public.event_study_returns is
  'Retornos por horizonte, em pregoes. Horizonte negativo cobre a janela pre-evento.';


-- -----------------------------------------------------------------------------
-- 8. Macro -- apenas a estrutura, sem pipeline nesta fase (fase1.md 95)
-- -----------------------------------------------------------------------------

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


-- =============================================================================
-- 9. Views (fase1.md 88)
--    security_invoker=true: a view respeita o RLS das tabelas base em vez de
--    rodar com os privilegios do dono.
-- =============================================================================

create view public.v_latest_prices with (security_invoker = true) as
select distinct on (p.instrument_id, p.source)
       p.instrument_id,
       i.ticker,
       i.company_name,
       p.source,
       p.trade_date,
       p.close,
       p.adj_close,
       p.volume,
       p.currency,
       p.is_repaired,
       p.ingested_at
from public.daily_prices p
join public.instruments i using (instrument_id)
order by p.instrument_id, p.source, p.trade_date desc;

comment on view public.v_latest_prices is 'Ultimo preco disponivel por instrumento e fonte.';


create view public.v_price_returns with (security_invoker = true) as
select r.instrument_id,
       i.ticker,
       r.trade_date,
       r.price_source,
       r.close,
       r.adj_close,
       r.return_1d_price,
       r.return_1d_adjusted,
       r.log_return_1d,
       r.volume,
       r.volume_ratio_20,
       r.volume_zscore_20,
       r.benchmark_return_1d,
       r.excess_return_1d,
       r.calculation_version
from public.daily_returns r
join public.instruments i using (instrument_id);


create view public.v_canonical_news with (security_invoker = true) as
select a.article_id,
       a.duplicate_cluster_id,
       c.article_count,
       c.unique_domains,
       c.first_seen,
       c.last_seen,
       a.canonical_url,
       a.domain,
       a.source_name,
       a.title,
       a.language,
       a.published_at_utc,
       a.published_at_local,
       a.time_precision,
       a.provider
from public.news_articles a
left join public.news_clusters c on c.cluster_id = a.duplicate_cluster_id
where a.is_cluster_canonical or a.duplicate_cluster_id is null;

comment on view public.v_canonical_news is
  'Um artigo por cluster. As republicacoes continuam gravadas em news_articles.';


create view public.v_news_with_company with (security_invoker = true) as
select a.article_id,
       l.instrument_id,
       i.ticker,
       i.company_name,
       l.relevance_score,
       l.is_primary_company,
       l.match_method,
       l.review_status,
       a.title,
       a.canonical_url,
       a.domain,
       a.published_at_utc,
       a.published_at_local,
       a.time_precision,
       a.duplicate_cluster_id,
       a.is_cluster_canonical
from public.news_company_links l
join public.news_articles a using (article_id)
join public.instruments i using (instrument_id);


create view public.v_events with (security_invoker = true) as
select e.event_id,
       e.instrument_id,
       i.ticker,
       i.company_name,
       e.event_type,
       e.event_subtype,
       e.event_title,
       e.event_date,
       e.effective_trade_date,
       e.time_precision,
       e.scope,
       e.source_type,
       e.relevance_score,
       e.sentiment,
       e.impact_score,
       e.is_confounded,
       e.overlapping_event_count,
       e.news_explanation_status,
       (select count(*) from public.event_articles ea where ea.event_id = e.event_id)
         as article_count
from public.events e
join public.instruments i using (instrument_id);


create view public.v_fundamentals_as_reported with (security_invoker = true) as
select f.fact_id,
       f.instrument_id,
       i.ticker,
       f.document_type,
       f.statement_type,
       f.reference_date,
       f.period_start,
       f.period_end,
       f.available_from,
       f.version,
       f.account_code,
       f.account_description,
       f.value,
       f.currency,
       f.scale,
       f.is_consolidated
from public.financial_statement_facts f
join public.instruments i using (instrument_id);

comment on view public.v_fundamentals_as_reported is
  'Todos os fatos, todas as versoes. Use com filtro available_from <= data para point-in-time.';


create view public.v_fundamentals_latest_restated with (security_invoker = true) as
select distinct on (f.instrument_id, f.statement_type, f.reference_date, f.account_code,
                    f.is_consolidated)
       f.instrument_id,
       i.ticker,
       f.statement_type,
       f.reference_date,
       f.account_code,
       f.account_description,
       f.is_consolidated,
       f.value,
       f.currency,
       f.scale,
       f.version,
       f.available_from
from public.financial_statement_facts f
join public.instruments i using (instrument_id)
order by f.instrument_id, f.statement_type, f.reference_date, f.account_code, f.is_consolidated,
         f.available_from desc nulls last, f.fact_id desc;

comment on view public.v_fundamentals_latest_restated is
  'Ultima versao conhecida de cada conta. NAO usar em backtest -- contem reapresentacoes futuras.';


create view public.v_event_study_summary with (security_invoker = true) as
select es.event_study_id,
       es.event_id,
       es.instrument_id,
       i.ticker,
       e.event_type,
       e.event_title,
       e.scope,
       e.is_confounded,
       es.effective_trade_date,
       es.method,
       es.price_series,
       es.alpha,
       es.beta,
       es.r_squared,
       es.observations,
       es.low_sample,
       es.data_quality,
       es.volume_ratio_20,
       max(r.return_actual)   filter (where r.horizon_days = -20) as return_pre_20,
       max(r.return_actual)   filter (where r.horizon_days = -5)  as return_pre_5,
       max(r.return_actual)   filter (where r.horizon_days = 0)   as return_d0,
       max(r.return_actual)   filter (where r.horizon_days = 1)   as return_d1,
       max(r.return_actual)   filter (where r.horizon_days = 5)   as return_d5,
       max(r.return_actual)   filter (where r.horizon_days = 20)  as return_d20,
       max(r.return_actual)   filter (where r.horizon_days = 60)  as return_d60,
       max(r.return_actual)   filter (where r.horizon_days = 252) as return_d252,
       max(r.excess_return)   filter (where r.horizon_days = 1)   as excess_d1,
       max(r.excess_return)   filter (where r.horizon_days = 5)   as excess_d5,
       max(r.excess_return)   filter (where r.horizon_days = 20)  as excess_d20,
       max(r.excess_return)   filter (where r.horizon_days = 252) as excess_d252,
       max(r.car)             filter (where r.horizon_days = 1)   as car_0_1,
       max(r.car)             filter (where r.horizon_days = 5)   as car_0_5,
       max(r.car)             filter (where r.horizon_days = 20)  as car_0_20,
       bool_or(r.is_censored)                                     as has_censored_horizon
from public.event_studies es
join public.events e using (event_id)
join public.instruments i on i.instrument_id = es.instrument_id
left join public.event_study_returns r using (event_study_id)
group by es.event_study_id, es.event_id, es.instrument_id, i.ticker, e.event_type,
         e.event_title, e.scope, e.is_confounded, es.effective_trade_date, es.method,
         es.price_series, es.alpha, es.beta, es.r_squared, es.observations, es.low_sample,
         es.data_quality, es.volume_ratio_20;

comment on view public.v_event_study_summary is
  'Formato largo do event study, para leitura humana e export. A fonte normalizada e event_study_returns.';


create view public.v_data_coverage with (security_invoker = true) as
select i.instrument_id,
       i.ticker,
       i.company_name,
       i.active,
       (select min(trade_date) from public.daily_prices p
         where p.instrument_id = i.instrument_id)              as price_first_date,
       (select max(trade_date) from public.daily_prices p
         where p.instrument_id = i.instrument_id)              as price_last_date,
       (select count(*) from public.daily_prices p
         where p.instrument_id = i.instrument_id)              as price_rows,
       (select count(*) from public.corporate_actions ca
         where ca.instrument_id = i.instrument_id)             as corporate_actions,
       (select count(*) from public.news_company_links l
         where l.instrument_id = i.instrument_id)              as news_links,
       (select count(distinct a.duplicate_cluster_id)
          from public.news_company_links l
          join public.news_articles a using (article_id)
         where l.instrument_id = i.instrument_id)              as news_clusters,
       (select count(*) from public.cvm_documents d
         where d.instrument_id = i.instrument_id)              as cvm_documents,
       (select max(reference_date) from public.cvm_documents d
         where d.instrument_id = i.instrument_id)              as cvm_last_reference,
       (select count(*) from public.events e
         where e.instrument_id = i.instrument_id)              as events,
       (select count(*) from public.event_studies es
         where es.instrument_id = i.instrument_id)             as event_studies
from public.instruments i;

comment on view public.v_data_coverage is
  'Cobertura por instrumento. Base do comando `stock-research status`.';


create view public.v_manual_review_queue with (security_invoker = true) as
-- Movimento forte sem evento associado: candidato a investigacao manual.
select 'unexplained_move'::text                      as reason,
       r.instrument_id,
       i.ticker,
       r.trade_date                                  as ref_date,
       'daily_returns'::text                         as entity_type,
       r.instrument_id::text || ':' || r.trade_date::text as entity_id,
       jsonb_build_object('return_1d_adjusted', r.return_1d_adjusted,
                          'volume_ratio_20', r.volume_ratio_20) as details
from public.daily_returns r
join public.instruments i using (instrument_id)
where abs(coalesce(r.return_1d_adjusted, 0)) >= 0.07
  and not exists (select 1 from public.events e
                   where e.instrument_id = r.instrument_id
                     and e.effective_trade_date = r.trade_date)

union all

-- Artigo ligado a empresa com relevancia ambigua.
select 'ambiguous_relevance',
       l.instrument_id,
       i.ticker,
       a.published_at_utc::date,
       'news_company_links',
       l.link_id::text,
       jsonb_build_object('relevance_score', l.relevance_score, 'title', a.title)
from public.news_company_links l
join public.news_articles a using (article_id)
join public.instruments i using (instrument_id)
where l.relevance_score between 0.40 and 0.60
  and l.review_status = 'auto'

union all

-- Noticia sem timestamp confiavel: effective_trade_date fica conservador.
select 'missing_timestamp',
       l.instrument_id,
       i.ticker,
       a.published_at_utc::date,
       'news_articles',
       a.article_id::text,
       jsonb_build_object('time_precision', a.time_precision, 'title', a.title)
from public.news_articles a
join public.news_company_links l using (article_id)
join public.instruments i using (instrument_id)
where a.time_precision in ('date_only', 'unknown');

comment on view public.v_manual_review_queue is
  'Fila de revisao humana: movimentos sem explicacao, relevancia ambigua, timestamp incerto.';


-- =============================================================================
-- 10. RLS
--    O banco e privado e so e acessado pelo pipeline local via service_role
--    (que ignora RLS). Habilitamos RLS SEM policies em tudo: qualquer chave
--    anon/authenticated que vaze nao le nada. Quando a Fase 4 expuser um app
--    no Vercel, as policies de leitura serao adicionadas conscientemente.
-- =============================================================================

do $$
declare
  t text;
begin
  for t in
    select tablename from pg_tables where schemaname = 'public'
  loop
    execute format('alter table public.%I enable row level security', t);
  end loop;
end;
$$;
