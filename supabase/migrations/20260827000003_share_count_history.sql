-- =============================================================================
-- Fase 2 -- Secao 3: quantidade historica de acoes por companhia e classe.
--
-- Fonte primaria: CVM FRE (Formulario de Referencia), arquivos
--   fre_cia_aberta_capital_social_AAAA.csv       -> shares_issued (Capital Integralizado)
--   fre_cia_aberta_distribuicao_capital_AAAA.csv  -> free_float_shares (*_Circulacao)
-- Ver docs/fase2_plan.md secoes 3, 4, 13.1-13.2, e o achado de 2026-08-27 no §24
-- (o campo *_Circulacao da FRE e FREE FLOAT -- exclui o bloco de controle --,
-- nao "emitidas menos tesouraria"; o denominador de market cap e shares_issued).
--
-- Mesma disciplina point-in-time de financial_statement_facts:
-- available_from (= DT_RECEB fim-do-dia BRT) e reapresentacoes preservadas
-- (VERSAO na chave natural, nunca sobrescreve versao anterior).
-- =============================================================================

create table public.share_count_history (
  share_count_id      bigint generated always as identity primary key,
  company_id          bigint      not null references public.companies (company_id)
                                    on delete cascade,
  share_class         text        not null,          -- 'ON' | 'PN' | 'TOTAL'
  reference_date      date        not null,          -- DT_REFER da FRE
  version             text        not null,          -- VERSAO da FRE
  filing_received_at  timestamptz,
  available_from      timestamptz,                   -- DT_RECEB fim-do-dia BRT; null = fora de point-in-time
  shares_issued       numeric(30, 0),                -- capital_social, Tipo_Capital='Capital Integralizado'
  free_float_shares   numeric(30, 0),                -- distribuicao_capital *_Circulacao (FREE FLOAT, nao issued-treasury)
  treasury_shares     numeric(30, 0),                -- FRE nao traz de forma consistente -> tipicamente null
  shares_outstanding  numeric(30, 0),                -- issued - treasury quando treasury conhecido; senao null
  source              text        not null default 'cvm_fre',
  source_document_id  bigint      references public.cvm_documents (document_id) on delete set null,
  calculation_version text        not null default 'share_count_v1',
  quality_flag        text        not null default 'ok'
                        check (quality_flag in ('ok', 'estimated', 'incomplete',
                                                'missing_input', 'inconsistent')),
  quality_reason      text,
  run_id              bigint      references public.ingestion_runs (run_id),
  created_at          timestamptz not null default now(),
  constraint share_count_history_key
    unique (company_id, share_class, reference_date, version)
);

comment on table public.share_count_history is
  'Quantidade de acoes por companhia e classe, point-in-time, da CVM FRE. '
  'shares_issued (Capital Integralizado) e o denominador de market cap (§4). '
  'free_float_shares vem do campo *_Circulacao da FRE, que e FREE FLOAT -- exclui '
  'o bloco de controle --, nao "emitidas menos tesouraria". Reapresentacoes '
  'preservadas: VERSAO na chave, nunca sobrescreve.';

comment on column public.share_count_history.available_from is
  'DT_RECEB da FRE, fim-do-dia BRT. Mesma politica de financial_statement_facts. '
  'null = documento sem data de recebimento -> nao entra em consulta point-in-time.';

create index share_count_history_asof_idx
  on public.share_count_history (company_id, share_class, available_from desc);

alter table public.share_count_history enable row level security;
