-- RPC para executar SQL a partir do pipeline quando a senha direta do Postgres
-- nao esta disponivel (so temos as API keys).
--
-- SEGURANCA: security invoker + EXECUTE revogado de todos menos service_role.
-- A service_role ja tem acesso total ao banco por definicao, entao esta funcao
-- nao amplia a superficie de ataque -- apenas dá a ela um caminho de SQL
-- arbitrario que ela ja teria via conexao direta.
--
-- Se um dia DATABASE_URL for configurada, o pipeline passa a usar psycopg
-- automaticamente e esta funcao pode ser removida.

create or replace function public.exec_sql(query text, params jsonb default '[]'::jsonb)
returns jsonb
language plpgsql
security invoker
set search_path = ''
as $$
declare
  result jsonb;
begin
  execute format('select coalesce(jsonb_agg(t), ''[]''::jsonb) from (%s) t', query)
    into result;
  return result;
end;
$$;

revoke all on function public.exec_sql(text, jsonb) from public;
revoke all on function public.exec_sql(text, jsonb) from anon;
revoke all on function public.exec_sql(text, jsonb) from authenticated;
grant execute on function public.exec_sql(text, jsonb) to service_role;

comment on function public.exec_sql is
  'Executa SELECT e retorna jsonb. Restrita a service_role. Ponte usada pelo pipeline '
  'enquanto DATABASE_URL (senha do Postgres) nao estiver configurada.';


-- Variante para DDL/DML que nao retorna linhas.
create or replace function public.exec_ddl(query text)
returns void
language plpgsql
security invoker
set search_path = ''
as $$
begin
  execute query;
end;
$$;

revoke all on function public.exec_ddl(text) from public;
revoke all on function public.exec_ddl(text) from anon;
revoke all on function public.exec_ddl(text) from authenticated;
grant execute on function public.exec_ddl(text) to service_role;

comment on function public.exec_ddl is
  'Executa comando sem retorno. Restrita a service_role. Mesmo proposito de exec_sql.';

