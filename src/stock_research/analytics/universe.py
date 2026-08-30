"""Universo investivel point-in-time (Fase 3 M1, Handoff v2 §6).

`select_investable_universe` e uma funcao PURA -- espelha
`analytics.fundamentals.select_point_in_time`: a selecao fica separada do
acesso a banco, testavel sem SQL, e o contrato ("nenhuma linha decidida por
tempo de transacao") fica verificavel de forma direta.

CONTRATO (Handoff §6):

* Elegibilidade decidida SO por TEMPO EFETIVO
  (`valid_from`/`valid_to`/`listing_start`/`listing_end`). NENHUMA referencia a
  `source_available_from` / `source_observed_at` / `ingested_at`.
* NULL em `valid_from`/`listing_start` NUNCA cai no filtro em silencio -> vai
  para o balde `not_eligible_data`, contabilizado (spec §94).
* O retorno NAO expoe `valid_to` nem `listing_end` (Handoff §5.3 -- a camada de
  estrategia nunca ve o futuro).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from stock_research.db import fetch_all
from stock_research.transforms.company_lifecycle import company_eligible_at
from stock_research.transforms.instrument_lifecycle import instrument_eligible_at


@dataclass(frozen=True)
class UniverseInstrument:
    company_id: int
    cnpj: str
    instrument_id: int | None
    ticker: str | None
    share_class: str
    market: str | None
    listing_venue: str | None
    segment: str | None
    quality_flag: str


@dataclass(frozen=True)
class UniverseResult:
    as_of: date
    companies: tuple[tuple[int, str], ...]          # (company_id, cnpj) elegiveis
    instruments: tuple[UniverseInstrument, ...]     # instrumentos elegiveis
    not_eligible_data: tuple[UniverseInstrument, ...]  # empresa elegivel, dado do instrumento faltando

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(i.ticker for i in self.instruments if i.ticker)


def _entry(row: dict[str, Any], company_id: int, cnpj: str) -> UniverseInstrument:
    return UniverseInstrument(
        company_id=company_id,
        cnpj=cnpj,
        instrument_id=row.get("instrument_id"),
        ticker=row.get("ticker"),
        share_class=row["share_class"],
        market=row.get("market"),
        listing_venue=row.get("listing_venue"),
        segment=row.get("segment"),
        quality_flag=row.get("quality_flag", "ok"),
    )


def select_investable_universe(
    company_rows: list[dict[str, Any]],
    instrument_rows: list[dict[str, Any]],
    as_of: date,
    *,
    include_suspended: bool = False,
) -> UniverseResult:
    """Funcao pura. `company_rows`/`instrument_rows` sao linhas de
    `company_lifecycle`/`instrument_lifecycle` (cada uma com `company_id` e
    `cnpj`). Devolve o universo elegivel em `as_of`.
    """
    eligible_company: dict[int, str] = {}
    for row in company_rows:
        cid = row["company_id"]
        if not company_eligible_at(row, as_of):
            continue
        if not include_suspended and row.get("registration_status") == "suspended":
            continue
        eligible_company[cid] = row["cnpj"]

    # 1+ linha elegivel por (company_id, share_class): mantem so uma. V1: a FCA
    # so tem codigo de negociacao a partir de 2018, entao SSBR3/ALSO3/ALOS3
    # (mesma ON, tickers sucessivos) chegam com o mesmo Data_Inicio_Negociacao
    # (data de listagem da CLASSE, nao do ticker) -- sem esse colapso, D=2020
    # devolveria dois tickers para a mesma acao. Regra: o intervalo mais
    # "apertado" em torno de D (menor valid_to >= D) e o ticker mais provavel
    # naquela data; so cai para valid_to NULL quando nenhum e limitado; empate
    # -> conhecimento mais recente. Ver docs/historical_universe.md §3.3.
    best: dict[tuple[int, str], dict[str, Any]] = {}
    not_eligible_data: list[UniverseInstrument] = []
    for row in instrument_rows:
        cid = row["company_id"]
        if cid not in eligible_company:
            continue
        if row.get("valid_from") is None or row.get("listing_start") is None:
            not_eligible_data.append(_entry(row, cid, eligible_company[cid]))
            continue
        if not instrument_eligible_at(row, as_of):
            continue
        key = (cid, row["share_class"])
        cur = best.get(key)
        if cur is None or _prefer(row, cur, as_of):
            best[key] = row

    instruments = [
        _entry(r, r["company_id"], eligible_company[r["company_id"]]) for r in best.values()
    ]
    instruments.sort(key=lambda i: (i.company_id, i.share_class))

    return UniverseResult(
        as_of=as_of,
        companies=tuple(sorted(eligible_company.items())),
        instruments=tuple(instruments),
        not_eligible_data=tuple(not_eligible_data),
    )


def _prefer(candidate: dict[str, Any], incumbent: dict[str, Any], as_of: date) -> bool:
    """True se `candidate` substitui `incumbent` para a mesma
    (company_id, share_class). Chave: intervalo mais apertado em torno de `as_of`
    (menor `valid_to >= as_of`); limitado ganha de NULL; empate -> maior
    `source_reference_year`; ultimo desempate -> ordem do ticker (determinismo).
    """
    ck = _tightness(candidate, as_of)
    ik = _tightness(incumbent, as_of)
    if ck != ik:
        return ck < ik
    cy = candidate.get("source_reference_year") or 0
    iy = incumbent.get("source_reference_year") or 0
    if cy != iy:
        return cy > iy
    return (candidate.get("ticker") or "") > (incumbent.get("ticker") or "")


def _tightness(row: dict[str, Any], as_of: date) -> tuple[int, int]:
    """Ordena por "quao especifico o fim e": (0, dias ate valid_to) para
    intervalo limitado; (1, 0) para valid_to NULL (menos especifico)."""
    vt = row.get("valid_to")
    if vt is None:
        return (1, 0)
    return (0, (vt - as_of).days)


# ---------------------------------------------------------------------------
# Acesso a banco (fino -- a logica esta na funcao pura acima)
# ---------------------------------------------------------------------------

_COMPANY_QUERY = """
    select l.company_id, c.cnpj, l.valid_from, l.valid_to, l.registration_status,
           l.event_type, l.source
    from public.company_lifecycle l
    join public.companies c on c.company_id = l.company_id
"""

_INSTRUMENT_QUERY = """
    select l.company_id, c.cnpj, l.instrument_id, l.ticker, l.share_class,
           l.valid_from, l.valid_to, l.listing_start, l.listing_end,
           l.market, l.listing_venue, l.segment, l.quality_flag, l.source,
           l.source_reference_year
    from public.instrument_lifecycle l
    join public.companies c on c.company_id = l.company_id
"""


def get_investable_universe_as_of(
    as_of: date, *, include_suspended: bool = False
) -> UniverseResult:
    """Universo investivel conhecido em `as_of`. Le os dois lifecycles inteiros
    e aplica `select_investable_universe` (o filtro e por TEMPO EFETIVO, entao
    trazer tudo e filtrar em memoria e correto -- nao ha gate de proveniencia
    no SQL de proposito).
    """
    company_rows = fetch_all(_COMPANY_QUERY)
    instrument_rows = fetch_all(_INSTRUMENT_QUERY)
    for r in company_rows:
        r["valid_from"] = _as_date(r["valid_from"])
        r["valid_to"] = _as_date(r["valid_to"])
    for r in instrument_rows:
        for k in ("valid_from", "valid_to", "listing_start", "listing_end"):
            r[k] = _as_date(r[k])
    return select_investable_universe(
        company_rows, instrument_rows, as_of, include_suspended=include_suspended
    )


def _as_date(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
