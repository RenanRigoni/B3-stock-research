-- Views (fase1.md 88). security_invoker=true faz a view respeitar o RLS das
-- tabelas base em vez de rodar com privilegios do dono.

create view public.v_latest_prices with (security_invoker = true) as
select distinct on (p.instrument_id, p.source)
       p.instrument_id, i.ticker, i.company_name, p.source, p.trade_date,
       p.close, p.adj_close, p.volume, p.currency, p.is_repaired, p.ingested_at
from public.daily_prices p
join public.instruments i using (instrument_id)
order by p.instrument_id, p.source, p.trade_date desc;

comment on view public.v_latest_prices is 'Ultimo preco disponivel por instrumento e fonte.';

create view public.v_price_returns with (security_invoker = true) as
select r.instrument_id, i.ticker, r.trade_date, r.price_source, r.close, r.adj_close,
       r.return_1d_price, r.return_1d_adjusted, r.log_return_1d, r.volume,
       r.volume_ratio_20, r.volume_zscore_20, r.benchmark_return_1d,
       r.excess_return_1d, r.calculation_version
from public.daily_returns r
join public.instruments i using (instrument_id);

create view public.v_canonical_news with (security_invoker = true) as
select a.article_id, a.duplicate_cluster_id, c.article_count, c.unique_domains,
       c.first_seen, c.last_seen, a.canonical_url, a.domain, a.source_name, a.title,
       a.language, a.published_at_utc, a.published_at_local, a.time_precision, a.provider
from public.news_articles a
left join public.news_clusters c on c.cluster_id = a.duplicate_cluster_id
where a.is_cluster_canonical or a.duplicate_cluster_id is null;

comment on view public.v_canonical_news is
  'Um artigo por cluster. As republicacoes continuam gravadas em news_articles.';

create view public.v_news_with_company with (security_invoker = true) as
select a.article_id, l.instrument_id, i.ticker, i.company_name, l.relevance_score,
       l.is_primary_company, l.match_method, l.review_status, a.title, a.canonical_url,
       a.domain, a.published_at_utc, a.published_at_local, a.time_precision,
       a.duplicate_cluster_id, a.is_cluster_canonical
from public.news_company_links l
join public.news_articles a using (article_id)
join public.instruments i using (instrument_id);

create view public.v_events with (security_invoker = true) as
select e.event_id, e.instrument_id, i.ticker, i.company_name, e.event_type, e.event_subtype,
       e.event_title, e.event_date, e.effective_trade_date, e.time_precision, e.scope,
       e.source_type, e.relevance_score, e.sentiment, e.impact_score, e.is_confounded,
       e.overlapping_event_count, e.news_explanation_status,
       (select count(*) from public.event_articles ea where ea.event_id = e.event_id) as article_count
from public.events e
join public.instruments i using (instrument_id);

create view public.v_fundamentals_as_reported with (security_invoker = true) as
select f.fact_id, f.instrument_id, i.ticker, f.document_type, f.statement_type,
       f.reference_date, f.period_start, f.period_end, f.available_from, f.version,
       f.account_code, f.account_description, f.value, f.currency, f.scale, f.is_consolidated
from public.financial_statement_facts f
join public.instruments i using (instrument_id);

comment on view public.v_fundamentals_as_reported is
  'Todos os fatos, todas as versoes. Use com filtro available_from <= data para point-in-time.';

create view public.v_fundamentals_latest_restated with (security_invoker = true) as
select distinct on (f.instrument_id, f.statement_type, f.reference_date, f.account_code,
                    f.is_consolidated)
       f.instrument_id, i.ticker, f.statement_type, f.reference_date, f.account_code,
       f.account_description, f.is_consolidated, f.value, f.currency, f.scale,
       f.version, f.available_from
from public.financial_statement_facts f
join public.instruments i using (instrument_id)
order by f.instrument_id, f.statement_type, f.reference_date, f.account_code, f.is_consolidated,
         f.available_from desc nulls last, f.fact_id desc;

comment on view public.v_fundamentals_latest_restated is
  'Ultima versao conhecida de cada conta. NAO usar em backtest -- contem reapresentacoes futuras.';

create view public.v_event_study_summary with (security_invoker = true) as
select es.event_study_id, es.event_id, es.instrument_id, i.ticker, e.event_type, e.event_title,
       e.scope, e.is_confounded, es.effective_trade_date, es.method, es.price_series,
       es.alpha, es.beta, es.r_squared, es.observations, es.low_sample, es.data_quality,
       es.volume_ratio_20,
       max(r.return_actual) filter (where r.horizon_days = -20) as return_pre_20,
       max(r.return_actual) filter (where r.horizon_days = -5)  as return_pre_5,
       max(r.return_actual) filter (where r.horizon_days = 0)   as return_d0,
       max(r.return_actual) filter (where r.horizon_days = 1)   as return_d1,
       max(r.return_actual) filter (where r.horizon_days = 5)   as return_d5,
       max(r.return_actual) filter (where r.horizon_days = 20)  as return_d20,
       max(r.return_actual) filter (where r.horizon_days = 60)  as return_d60,
       max(r.return_actual) filter (where r.horizon_days = 252) as return_d252,
       max(r.excess_return) filter (where r.horizon_days = 1)   as excess_d1,
       max(r.excess_return) filter (where r.horizon_days = 5)   as excess_d5,
       max(r.excess_return) filter (where r.horizon_days = 20)  as excess_d20,
       max(r.excess_return) filter (where r.horizon_days = 252) as excess_d252,
       max(r.car) filter (where r.horizon_days = 1)  as car_0_1,
       max(r.car) filter (where r.horizon_days = 5)  as car_0_5,
       max(r.car) filter (where r.horizon_days = 20) as car_0_20,
       bool_or(r.is_censored) as has_censored_horizon
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
select i.instrument_id, i.ticker, i.company_name, i.active,
       (select min(trade_date) from public.daily_prices p where p.instrument_id = i.instrument_id) as price_first_date,
       (select max(trade_date) from public.daily_prices p where p.instrument_id = i.instrument_id) as price_last_date,
       (select count(*) from public.daily_prices p where p.instrument_id = i.instrument_id) as price_rows,
       (select count(*) from public.corporate_actions ca where ca.instrument_id = i.instrument_id) as corporate_actions,
       (select count(*) from public.news_company_links l where l.instrument_id = i.instrument_id) as news_links,
       (select count(distinct a.duplicate_cluster_id) from public.news_company_links l
          join public.news_articles a using (article_id)
         where l.instrument_id = i.instrument_id) as news_clusters,
       (select count(*) from public.cvm_documents d where d.instrument_id = i.instrument_id) as cvm_documents,
       (select max(reference_date) from public.cvm_documents d where d.instrument_id = i.instrument_id) as cvm_last_reference,
       (select count(*) from public.events e where e.instrument_id = i.instrument_id) as events,
       (select count(*) from public.event_studies es where es.instrument_id = i.instrument_id) as event_studies
from public.instruments i;

comment on view public.v_data_coverage is
  'Cobertura por instrumento. Base do comando `stock-research status`.';

create view public.v_manual_review_queue with (security_invoker = true) as
select 'unexplained_move'::text as reason, r.instrument_id, i.ticker, r.trade_date as ref_date,
       'daily_returns'::text as entity_type,
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
select 'ambiguous_relevance', l.instrument_id, i.ticker, a.published_at_utc::date,
       'news_company_links', l.link_id::text,
       jsonb_build_object('relevance_score', l.relevance_score, 'title', a.title)
from public.news_company_links l
join public.news_articles a using (article_id)
join public.instruments i using (instrument_id)
where l.relevance_score between 0.40 and 0.60 and l.review_status = 'auto'
union all
select 'missing_timestamp', l.instrument_id, i.ticker, a.published_at_utc::date,
       'news_articles', a.article_id::text,
       jsonb_build_object('time_precision', a.time_precision, 'title', a.title)
from public.news_articles a
join public.news_company_links l using (article_id)
join public.instruments i using (instrument_id)
where a.time_precision in ('date_only', 'unknown');

comment on view public.v_manual_review_queue is
  'Fila de revisao humana: movimentos sem explicacao, relevancia ambigua, timestamp incerto.';

-- RLS: habilitado SEM policies em todas as tabelas. O pipeline local usa
-- service_role (que ignora RLS). Qualquer chave anon/publishable que vaze nao le
-- nada. Policies de leitura serao adicionadas conscientemente quando a Fase 4
-- expuser um app no Vercel.
do $$
declare t text;
begin
  for t in select tablename from pg_tables where schemaname = 'public'
  loop
    execute format('alter table public.%I enable row level security', t);
  end loop;
end;
$$;

