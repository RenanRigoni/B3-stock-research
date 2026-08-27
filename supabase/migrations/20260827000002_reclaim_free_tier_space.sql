-- =============================================================================
-- Fase 2 -- adia o indice de company_id em financial_statement_facts.
--
-- Criado em 20260827000001_companies_issuer_entity.sql junto com a coluna, mas
-- nada consulta `financial_statement_facts` por `company_id` ate a camada de
-- valuation existir (YAGNI). A org esta no Free Plan do Supabase e ja acima da
-- cota de Database Size -- ~15 MB de indice sem uso (231k linhas) pesam. Recriar
-- no bloco que fizer a primeira query por company_id sobre fatos.
--
-- A coluna `company_id` e as outras 3 FKs/indices (instruments, cvm_documents,
-- fundamental_metrics -- todas em tabelas pequenas) continuam.
-- =============================================================================

drop index if exists public.financial_statement_facts_company_idx;
