-- =============================================================================
-- Fase 2 -- Secao 19: entidade `companies` (emissor/issuer) separada de
-- `instruments` (ticker/classe de acao). Ver docs/fase2_plan.md secoes 4, 13.4 e 19.
--
-- ESTRITAMENTE ADITIVA: nenhuma coluna existente e renomeada, removida ou muda
-- de tipo. `instrument_id` continua sendo a chave de tudo que a Fase 1/1.1 ja
-- consulta (`get_fundamentals_as_of`, `fundamental_metrics`, eventos, noticias).
-- A Fase 2 ganha um caminho novo e paralelo (`company_id`) para o que precisa ser
-- agregado no nivel da empresa (fundamentos consolidados PETR3+PETR4, market cap
-- somado, quantidade de acoes por classe).
-- =============================================================================

-- 1. Tabela nova -------------------------------------------------------------

create table public.companies (
  company_id         bigint generated always as identity primary key,
  cnpj               text        not null unique,
  cvm_code           text,
  legal_name         text        not null,
  display_name       text,
  sector             text,
  subsector          text,
  segment            text,
  financial_company  boolean     not null default false,
  utility            boolean     not null default false,
  commodity_exposed  boolean     not null default false,
  holding            boolean     not null default false,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);

comment on table public.companies is
  'Entidade emissora (empresa legal, chave natural = CNPJ). Um instrumento '
  '(ticker/classe de acao) pertence a uma companhia; uma companhia pode ter '
  'varios instrumentos (ex.: PETR3 + PETR4). Fundamentos e valuation da Fase 2 '
  'agregam por company_id, nunca por instrument_id (fase2_plan.md 4, 13.4).';

create unique index companies_cvm_code_key
  on public.companies (cvm_code) where cvm_code is not null;

create trigger companies_set_updated_at
  before update on public.companies
  for each row execute function public.set_updated_at();

-- 2. FKs aditivas ---------------------------------------------------------------
-- Todas nullable: um benchmark (IBOV) nao e companhia; um fato antigo pode nao
-- ter CNPJ resolvido. Mesma folga que `instrument_id` ja tem hoje nessas tabelas.

alter table public.instruments
  add column company_id bigint references public.companies (company_id);
create index instruments_company_idx
  on public.instruments (company_id) where company_id is not null;

alter table public.cvm_documents
  add column company_id bigint references public.companies (company_id);
create index cvm_documents_company_idx
  on public.cvm_documents (company_id, reference_date desc);

alter table public.financial_statement_facts
  add column company_id bigint references public.companies (company_id);
create index financial_statement_facts_company_idx
  on public.financial_statement_facts (company_id, account_code, reference_date desc);

-- fundamental_metrics segue o mesmo padrao (fase2_plan.md 13.4): a camada de
-- valuation vai consultar por company_id.
alter table public.fundamental_metrics
  add column company_id bigint references public.companies (company_id);
create index fundamental_metrics_company_asof_idx
  on public.fundamental_metrics (company_id, metric_name, available_from desc);

-- 3. Backfill do que ja existe ------------------------------------------------
-- Nenhum re-download da CVM. instruments.cnpj ja esta preenchido e confirmado
-- em config/company_mapping.yaml para os 3 tickers atuais.

insert into public.companies
  (cnpj, cvm_code, legal_name, display_name,
   sector, subsector, segment,
   financial_company, utility, commodity_exposed, holding)
select distinct on (i.cnpj)
       i.cnpj,
       i.cvm_code,
       coalesce(i.legal_name, i.company_name),
       i.company_name,
       i.sector, i.subsector, i.segment,
       i.financial_company, i.utility, i.commodity_exposed, i.holding
from public.instruments i
where i.cnpj is not null
order by i.cnpj, i.instrument_id;

-- Instrumentos existentes -> sua companhia, pelo CNPJ.
update public.instruments i
   set company_id = c.company_id
  from public.companies c
 where i.cnpj = c.cnpj
   and i.company_id is null;

-- Historico ja ingerido: documentos e fatos, pelo CNPJ (fase2_plan.md 19.3).
update public.cvm_documents d
   set company_id = c.company_id
  from public.companies c
 where d.cnpj = c.cnpj
   and d.company_id is null;

update public.financial_statement_facts f
   set company_id = c.company_id
  from public.companies c
 where f.cnpj = c.cnpj
   and f.company_id is null;

-- Rede de seguranca: qualquer linha sem cnpj proprio mas com instrument_id
-- resolvido herda a companhia pelo instrumento.
update public.cvm_documents d
   set company_id = i.company_id
  from public.instruments i
 where d.instrument_id = i.instrument_id
   and d.company_id is null
   and i.company_id is not null;

update public.financial_statement_facts f
   set company_id = i.company_id
  from public.instruments i
 where f.instrument_id = i.instrument_id
   and f.company_id is null
   and i.company_id is not null;

-- fundamental_metrics nao carrega cnpj proprio -- herda via instrument_id.
update public.fundamental_metrics m
   set company_id = i.company_id
  from public.instruments i
 where m.instrument_id = i.instrument_id
   and m.company_id is null
   and i.company_id is not null;

-- 4. PETR3 e ITUB3 -----------------------------------------------------------
-- Classes ON das mesmas companhias de PETR4/ITUB4 (mesmo CNPJ, validadas no
-- yfinance desde 2010 -- fase2_plan.md 13.3). Entram INATIVAS: os pipelines da
-- Fase 1/1.1 filtram `active = true` (prices, news) e a ingestao de fundamentos
-- passa a filtrar tambem (ver pipelines/fundamentals_ingest.py nesta mesma
-- entrega), entao nada comeca a baixar preco/noticia/CVM para elas agora. A
-- ativacao vem junto com o bloco de market cap agregado por companhia
-- (fase2_plan.md 4).
--
-- `cvm_code` fica NULL nestes instrumentos de proposito: o codigo CVM e um
-- atributo da companhia (agora em `public.companies`), nao do ticker, e o
-- indice unico parcial `instruments_cvm_code_key` rejeitaria o codigo repetido
-- de PETR4/ITUB4. O vinculo com o codigo CVM e via `company_id`.

insert into public.instruments
  (ticker, yahoo_symbol, company_name, legal_name, cnpj, cvm_code,
   asset_type, share_class, sector, subsector, segment, currency, exchange,
   financial_company, utility, commodity_exposed, holding,
   company_id, active, notes)
select v.ticker, v.yahoo_symbol, i.company_name, i.legal_name, i.cnpj, null,
       'stock', 'ON', i.sector, i.subsector, i.segment, i.currency, i.exchange,
       i.financial_company, i.utility, i.commodity_exposed, i.holding,
       i.company_id, false,
       'Classe ON. Cadastrada inativa na migration da Fase 2 (secao 19); '
       'ativar no bloco de market cap por companhia (secao 4).'
from (values
        ('PETR3', 'PETR3.SA', 'PETR4'),
        ('ITUB3', 'ITUB3.SA', 'ITUB4')
     ) as v(ticker, yahoo_symbol, sibling)
join public.instruments i on i.ticker = v.sibling and i.exchange = 'B3'
on conflict (ticker, exchange) do nothing;

-- RLS: `companies` nasce com row level security habilitado e sem policy, igual a
-- todas as outras (o pipeline usa service_role, que ignora RLS).
alter table public.companies enable row level security;
