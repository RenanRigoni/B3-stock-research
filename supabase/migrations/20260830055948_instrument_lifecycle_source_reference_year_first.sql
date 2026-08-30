-- =============================================================================
-- Fase 3 -- M2 (pre-requisito 1): instrument_lifecycle.source_reference_year_first
--
-- Ver "OPUS -- LIBERACAO M2", secao "structural vs investable".
--
-- O M1 guarda apenas o MAIOR ano de FCA que reportou cada identidade
-- (`source_reference_year`). Para responder "o ticker era OBSERVAVEL em D?" o
-- que importa e o MENOR ano que reportou aquele ticker -- nao e derivavel do
-- que esta gravado, e usar a constante global 2018 (primeiro ano em que a FCA
-- publica Codigo_Negociacao) estaria errado por ticker: ALOS3 so aparece na
-- FCA de 2023, e o piso global o marcaria como observavel desde 2018.
--
-- TEMPO DE TRANSACAO. Prefixo `source_` mantido de proposito (Handoff §3): esta
-- coluna NUNCA pode ser gate do universo ESTRUTURAL. Ela alimenta apenas a
-- camada INVESTIVEL, como criterio de identificabilidade -- mesma classe de
-- "tem preco" / "tem fundamento", e sempre CONTADA quando reprova.
--
-- Aditiva (coluna nullable, nada existente muda), reversivel (drop column).
-- =============================================================================

alter table public.instrument_lifecycle
  add column source_reference_year_first int;

comment on column public.instrument_lifecycle.source_reference_year_first is
  'TEMPO DE TRANSACAO. Menor ano de referencia da FCA que reportou esta '
  'identidade (companhia+classe+ticker). Usado SO na camada investivel para '
  'decidir se o ticker era observavel em D (resolved x back_projected). '
  'NUNCA gate do universo estrutural. Ver docs/historical_universe.md §7.';

create index instrument_lifecycle_year_first_idx
  on public.instrument_lifecycle (source_reference_year_first);
