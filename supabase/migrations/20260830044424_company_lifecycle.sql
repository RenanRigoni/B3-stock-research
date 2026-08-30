-- =============================================================================
-- Fase 3 -- M1: ciclo de vida da companhia (universo historico / survivorship).
--
-- Ver docs/fase3_handoff_v2.md §4.1 + docs/historical_universe.md.
--
-- REGRA BITEMPORAL (Handoff §1-§3): a elegibilidade historica desta tabela e
-- decidida por TEMPO EFETIVO (valid_from / valid_to) -- a existencia de uma
-- companhia negociando em D era fato publico em D. As colunas source_* /
-- ingested_at sao TEMPO DE TRANSACAO (proveniencia / reprodutibilidade) e
-- NUNCA filtram elegibilidade. O rename de proveniencia (source_available_from
-- em vez de available_from) e uma guarda deliberada contra o reflexo de
-- escrever `where available_from <= as_of`.
--
-- Aditiva e reversivel (drop table nao afeta Fase 1/2). RLS sem policy
-- (pipeline usa service_role).
-- =============================================================================

create table public.company_lifecycle (
  company_lifecycle_id   bigint generated always as identity primary key,
  company_id             bigint      not null references public.companies (company_id)
                                       on delete cascade,

  -- ---- TEMPO EFETIVO (unico gate de elegibilidade do universo) --------------
  valid_from             date        not null,          -- DT_REG (ou inicio da transicao)
  valid_to               date,                           -- DT_CANCEL; NULL = vigente
  event_date             date,                           -- data do evento que abre esta linha
  cvm_registration_date  date,                           -- DT_REG
  cvm_cancel_date        date,                           -- DT_CANCEL

  -- ---- atributos -----------------------------------------------------------
  registration_status    text        not null            -- de SIT
                           check (registration_status in ('registered','canceled','suspended')),
  issuer_status          text,                            -- de SIT_EMISSOR (operational/pre_operational/judicial_recovery/bankrupt/...)
  event_type             text        not null
                           check (event_type in ('registration','cancellation','status_change')),
  reason                 text,                            -- MOTIVO_CANCEL bruto
  reason_category        text,                            -- normalizado: incorporation/voluntary_delisting/bankruptcy_liquidation/regulatory/other
  successor_company_id   bigint      references public.companies (company_id),
  predecessor_company_id bigint      references public.companies (company_id),

  -- ---- TEMPO DE TRANSACAO (proveniencia -- NUNCA gate de elegibilidade) -----
  source                 text        not null default 'cvm_cad',
  source_document_ref    text,
  source_available_from  timestamptz,                     -- quando a fonte oficial disponibilizou (NULL se nao informa)
  source_observed_at     timestamptz not null,            -- quando o pipeline observou o snapshot
  ingested_at            timestamptz not null default now(),
  run_id                 bigint      references public.ingestion_runs (run_id),

  quality_flag           text        not null default 'ok'
                           check (quality_flag in ('ok','estimated','incomplete','missing_input','inconsistent')),
  quality_reason         text,
  created_at             timestamptz not null default now(),

  constraint company_lifecycle_key unique (company_id, event_type, valid_from, source),
  constraint company_lifecycle_valid_range
    check (valid_to is null or valid_from is null or valid_to >= valid_from)
);

comment on table public.company_lifecycle is
  'Ciclo de vida da companhia emissora (universo historico, Fase 3 M1). '
  'valid_from / valid_to sao TEMPO EFETIVO (existencia real) e sao o UNICO gate '
  'de elegibilidade do universo. source_available_from / source_observed_at / '
  'ingested_at sao TEMPO DE TRANSACAO -- proveniencia e reprodutibilidade apenas, '
  'NUNCA filtro de elegibilidade. Ver docs/historical_universe.md.';

comment on column public.company_lifecycle.valid_from is
  'TEMPO EFETIVO. DT_REG do cadastro CVM (ou inicio da transicao). Gate do universo.';
comment on column public.company_lifecycle.valid_to is
  'TEMPO EFETIVO. DT_CANCEL; NULL = vigente. So responde "estava viva em D?" -- '
  'nunca exposto a camada de estrategia (Handoff §5.3).';
comment on column public.company_lifecycle.source_available_from is
  'TEMPO DE TRANSACAO. Proveniencia -- NUNCA usar em WHERE de elegibilidade.';

create index company_lifecycle_company_valid_idx
  on public.company_lifecycle (company_id, valid_from);
create index company_lifecycle_valid_to_idx
  on public.company_lifecycle (valid_to);

alter table public.company_lifecycle enable row level security;
