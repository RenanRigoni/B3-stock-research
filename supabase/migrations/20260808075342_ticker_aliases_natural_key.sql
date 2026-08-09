-- ticker_aliases precisa de chave natural para permitir upsert idempotente.
-- valid_from entra na chave porque um mesmo codigo pode ser reaproveitado em
-- periodos diferentes (empresa devolve o ticker, outra assume depois).
alter table public.ticker_aliases
  add constraint ticker_aliases_key unique nulls not distinct (instrument_id, ticker, valid_from);

