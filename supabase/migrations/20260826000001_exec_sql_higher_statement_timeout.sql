-- exec_sql/exec_ddl herdam o statement_timeout padrao da role (8s), curto
-- demais pra consultas legitimas sobre volume real de producao (PETR4 tem
-- 166 mil artigos brutos na Fase 1.1 -- um SELECT + jsonb_agg sobre isso
-- nao termina em 8s no tier Nano). SET LOCAL aplica so dentro da propria
-- chamada de funcao (a transacao implicita do RPC), nunca muda o timeout
-- padrao da role/sessao pra mais nada. 120s casa com o timeout do cliente
-- httpx em db/rest.py (TIMEOUT = httpx.Timeout(120.0, connect=15.0)).

create or replace function public.exec_sql(query text, params jsonb default '[]'::jsonb)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  result jsonb;
begin
  set local statement_timeout = '120s';
  execute format('select coalesce(jsonb_agg(t), ''[]''::jsonb) from (%s) t', query)
    into result;
  return result;
end;
$$;

create or replace function public.exec_ddl(query text)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
begin
  set local statement_timeout = '120s';
  execute query;
end;
$$;
