-- news_clusters nao tinha chave natural: analyze-news recriava clusters do
-- zero a cada execucao (inclusive execucoes que falharam no meio, como a
-- run 28 real que morreu no bug do article_id GENERATED ALWAYS), acumulando
-- linhas duplicadas para o mesmo grupo de artigos -- violando idempotencia
-- (fase1.md: "executar duas vezes nao pode duplicar dados").
--
-- canonical_article_id e uma escolha estavel e deterministica (fase1.md 37:
-- primeiro publicado) pro mesmo conjunto de artigos, entao serve como chave
-- natural: reexecutar upserta o mesmo cluster em vez de criar outro.

-- Limpa a duplicacao ja gravada pelas execucoes de teste antes de aplicar a
-- constraint (mantem o cluster_id mais baixo de cada canonical_article_id
-- repetido, redireciona os artigos que apontavam pro duplicado removido).
with ranked as (
  select cluster_id, canonical_article_id,
         row_number() over (partition by canonical_article_id order by cluster_id) as rn
  from public.news_clusters
),
to_remove as (
  select cluster_id from ranked where rn > 1
),
keep_map as (
  select r_dup.cluster_id as old_cluster_id, r_keep.cluster_id as new_cluster_id
  from ranked r_dup
  join ranked r_keep
    on r_keep.canonical_article_id = r_dup.canonical_article_id and r_keep.rn = 1
  where r_dup.rn > 1
)
update public.news_articles a
set duplicate_cluster_id = k.new_cluster_id
from keep_map k
where a.duplicate_cluster_id = k.old_cluster_id;

delete from public.news_clusters where cluster_id in (
  select cluster_id from (
    select cluster_id,
           row_number() over (partition by canonical_article_id order by cluster_id) as rn
    from public.news_clusters
  ) t where rn > 1
);

alter table public.news_clusters
  add constraint news_clusters_canonical_article_key unique (canonical_article_id);

