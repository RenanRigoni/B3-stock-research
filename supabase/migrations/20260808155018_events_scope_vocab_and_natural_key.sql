-- Dois problemas achados so ao escrever o pipeline de eventos (Milestone 9):
--
-- 1. events.scope tinha CHECK ('company_specific','sector','macro','unknown'),
--    mas o vocabulario real vem de config/news_taxonomy.yaml (ja testado em
--    tests/unit/test_news_classifier.py), que usa 'company'/'mixed' -- nao
--    'company_specific' e sem 'mixed' nenhum. Alinhando o schema ao
--    vocabulario que a taxonomia (fonte da verdade) realmente usa.
--
-- 2. events nao tinha chave natural alem do PK: reexecutar build-events
--    criaria eventos duplicados pro mesmo cluster/artigo, repetindo o
--    problema ja corrigido em news_clusters (Milestone 7). source_id
--    (formato "cluster:<id>" ou "article:<id>") + clustering_version e
--    estavel entre execucoes.

alter table public.events drop constraint events_scope_check;
alter table public.events
  add constraint events_scope_check
  check (scope in ('company', 'sector', 'macro', 'mixed', 'unknown'));

alter table public.events
  add constraint events_natural_key
  unique nulls not distinct (instrument_id, source_type, source_id, clustering_version);

