"""Universo historico point-in-time -- os testes obrigatorios do Handoff v2 §15.

Blocos:
  15.1 bitemporal      -- 1..6
  15.2 falha silenciosa -- 7..10
  15.3 anti-survivorship / estrutural -- 11..14, 16
  15.4 regressao de sinal -- 18

Tudo sobre a funcao PURA ``select_investable_universe`` (sem banco), mesmo
espirito de ``tests/unit/test_lookahead.py``.
"""

from __future__ import annotations

from dataclasses import fields
from datetime import UTC, date

from stock_research.analytics.universe import (
    UniverseInstrument,
    select_investable_universe,
)
from stock_research.transforms.company_lifecycle import company_eligible_at
from stock_research.transforms.instrument_lifecycle import instrument_eligible_at

FUTURE = "2099-01-01T00:00:00+00:00"  # proveniencia absurda: nunca pode virar gate


def _company(company_id, cnpj, valid_from, valid_to=None, *, status="registered", src_avail=FUTURE):
    return {
        "company_id": company_id,
        "cnpj": cnpj,
        "valid_from": date.fromisoformat(valid_from),
        "valid_to": date.fromisoformat(valid_to) if valid_to else None,
        "registration_status": status,
        "event_type": "cancellation" if valid_to else "registration",
        "source": "cvm_cad",
        "source_available_from": src_avail,
        "source_observed_at": src_avail,
        "ingested_at": src_avail,
    }


def _instrument(
    company_id,
    cnpj,
    *,
    ticker=None,
    share_class="ON",
    valid_from="2010-01-01",
    valid_to=None,
    listing_start="2010-01-01",
    listing_end=None,
    src_avail=FUTURE,
    quality_flag="ok",
):
    return {
        "company_id": company_id,
        "cnpj": cnpj,
        "instrument_id": None,
        "ticker": ticker,
        "share_class": share_class,
        "valid_from": date.fromisoformat(valid_from) if valid_from else None,
        "valid_to": date.fromisoformat(valid_to) if valid_to else None,
        "listing_start": date.fromisoformat(listing_start) if listing_start else None,
        "listing_end": date.fromisoformat(listing_end) if listing_end else None,
        "market": "bolsa",
        "listing_venue": "B3",
        "segment": None,
        "quality_flag": quality_flag,
        "source": "cvm_fca",
        "source_available_from": src_avail,
        "source_observed_at": src_avail,
        "ingested_at": src_avail,
    }


# --- fixture central: A vive 2010-2026, B vive 2010-2015 e some ---------------
def _fixture_a_b():
    companies = [
        _company(1, "A", "2010-03-01"),
        _company(2, "B", "2010-03-01", "2018-06-30"),
    ]
    instruments = [
        _instrument(1, "A", ticker="AAAA3", valid_from="2010-03-01"),
        _instrument(2, "B", ticker=None, valid_from="2010-03-01", valid_to="2018-06-30",
                    listing_end="2018-06-30"),
    ]
    return companies, instruments


# ===========================================================================
# 15.1 -- Bloco bitemporal
# ===========================================================================


def test_1_company_registered_2010_canceled_2018_appears_in_2013():
    companies, instruments = _fixture_a_b()
    u = select_investable_universe(companies, instruments, date(2013, 6, 15))
    assert {c[1] for c in u.companies} == {"A", "B"}


def test_2_same_company_still_present_in_2017():
    companies, instruments = _fixture_a_b()
    u = select_investable_universe(companies, instruments, date(2017, 12, 31))
    assert 2 in {c[0] for c in u.companies}


def test_3_absent_after_effective_cancellation_boundary_inclusive():
    companies, instruments = _fixture_a_b()
    on_last_day = select_investable_universe(companies, instruments, date(2018, 6, 30))
    day_after = select_investable_universe(companies, instruments, date(2018, 7, 1))
    assert 2 in {c[0] for c in on_last_day.companies}
    assert 2 not in {c[0] for c in day_after.companies}


def test_4_universe_is_invariant_to_provenance_dates():
    """O teste decisivo: mexer em source_available_from/source_observed_at/
    ingested_at em +-10 anos NAO pode mudar o universo."""
    companies, instruments = _fixture_a_b()
    base = select_investable_universe(companies, instruments, date(2014, 1, 1))

    def shift(rows, years):
        out = []
        for r in rows:
            c = dict(r)
            for k in ("source_available_from", "source_observed_at", "ingested_at"):
                y = int(c[k][:4]) + years
                c[k] = f"{y}{c[k][4:]}"
            out.append(c)
        return out

    for delta in (-10, +10):
        moved = select_investable_universe(
            shift(companies, delta), shift(instruments, delta), date(2014, 1, 1)
        )
        assert {c[0] for c in moved.companies} == {c[0] for c in base.companies}
        assert {i.ticker for i in moved.instruments} == {i.ticker for i in base.instruments}
        assert len(moved.instruments) == len(base.instruments)


def test_5_future_delisting_never_excludes_before_effective_date():
    companies, instruments = _fixture_a_b()
    # varre do inicio EFETIVO de B (2010-03-01) ate o fim EFETIVO (2018-06-30)
    d = date(2010, 3, 1)
    while d <= date(2018, 6, 30):
        u = select_investable_universe(companies, instruments, d)
        assert 2 in {c[0] for c in u.companies}, f"B sumiu em {d} -- look-ahead de delisting"
        month = d.month % 12 + 1
        year = d.year + (1 if d.month == 12 else 0)
        d = date(year, month, 1)


def test_6_valid_to_not_exposed_to_strategy_layer():
    field_names = {f.name for f in fields(UniverseInstrument)}
    assert "valid_to" not in field_names
    assert "listing_end" not in field_names
    assert "valid_from" not in field_names
    assert "listing_start" not in field_names


# ===========================================================================
# 15.2 -- Bloco de falha silenciosa
# ===========================================================================


def test_7_missing_listing_start_is_reported_not_silently_dropped():
    companies = [_company(1, "A", "2010-01-01")]
    instruments = [_instrument(1, "A", ticker="AAAA3", listing_start=None)]
    u = select_investable_universe(companies, instruments, date(2013, 1, 1))
    assert u.instruments == ()  # nao entra como elegivel
    assert len(u.not_eligible_data) == 1  # mas e CONTABILIZADO, nao some


def test_8_missing_valid_from_is_reported_not_silently_dropped():
    companies = [_company(1, "A", "2010-01-01")]
    instruments = [_instrument(1, "A", ticker="AAAA3", valid_from=None)]
    u = select_investable_universe(companies, instruments, date(2013, 1, 1))
    assert u.instruments == ()
    assert len(u.not_eligible_data) == 1


def test_9_null_effective_date_predicate_returns_false_never_raises():
    row = _instrument(1, "A", listing_start=None)
    assert instrument_eligible_at(row, date(2013, 1, 1)) is False
    row2 = _instrument(1, "A", valid_from=None)
    assert instrument_eligible_at(row2, date(2013, 1, 1)) is False


def test_10_company_eligibility_ignores_provenance_entirely():
    row = _company(1, "A", "2010-01-01", src_avail="2099-01-01T00:00:00+00:00")
    assert company_eligible_at(row, date(2013, 1, 1)) is True


# ===========================================================================
# 15.3 -- Bloco anti-survivorship / estrutural
# ===========================================================================


def test_11_anti_survivorship_universe_contents_by_date():
    companies, instruments = _fixture_a_b()
    u2013 = select_investable_universe(companies, instruments, date(2013, 1, 1))
    u2020 = select_investable_universe(companies, instruments, date(2020, 1, 1))
    assert {c[1] for c in u2013.companies} == {"A", "B"}
    assert {c[1] for c in u2020.companies} == {"A"}


def test_13_vale_class_structure_2012_differs_from_2020():
    companies = [_company(2, "VALE", "1968-04-01")]
    instruments = [
        _instrument(2, "VALE", ticker=None, share_class="ON",
                    valid_from="2003-12-12", valid_to="2017-12-31", listing_end="2017-12-31"),
        _instrument(2, "VALE", ticker="VALE3", share_class="ON", valid_from="2017-12-22"),
        _instrument(2, "VALE", ticker="VALE5", share_class="PNA",
                    valid_from="2000-01-01", valid_to="2017-12-22", listing_end="2017-12-22"),
    ]
    in_2012 = select_investable_universe(companies, instruments, date(2012, 6, 1))
    in_2020 = select_investable_universe(companies, instruments, date(2020, 6, 1))
    classes_2012 = sorted(i.share_class for i in in_2012.instruments)
    classes_2020 = sorted(i.share_class for i in in_2020.instruments)
    assert classes_2012 == ["ON", "PNA"]
    assert classes_2020 == ["ON"]
    assert "VALE5" in {i.ticker for i in in_2012.instruments}
    assert "VALE5" not in {i.ticker for i in in_2020.instruments}


def _allos_fixture():
    """Caso real (company_id 81). SSBR3 -> ALSO3 -> ALOS3 sao a MESMA acao
    ordinaria: a FCA anual da aos tres o mesmo Data_Inicio_Negociacao
    (2011-02-02, listagem da CLASSE), e so o ano da FCA os separa."""
    companies = [_company(9, "ALLOS", "2011-01-01")]
    rows = [
        _instrument(9, "ALLOS", ticker="SSBR3", valid_from="2011-02-02",
                    valid_to="2018-12-31", listing_end="2018-12-31"),
        _instrument(9, "ALLOS", ticker="ALSO3", valid_from="2011-02-02",
                    valid_to="2022-12-31", listing_end="2022-12-31"),
        _instrument(9, "ALLOS", ticker="ALOS3", valid_from="2011-02-02"),
    ]
    for r, first, last in zip(rows, (2018, 2019, 2023), (2018, 2022, 2026), strict=True):
        r["source_reference_year_first"] = first
        r["source_reference_year"] = last
    return companies, rows


def test_successive_tickers_collapse_to_ticker_observed_at_that_date():
    """O universo em D devolve UM ticker por (companhia, classe) -- o observado
    mais recente que ja existia em D, nunca o atual retroagido."""
    companies, rows = _allos_fixture()

    u2024 = select_investable_universe(companies, rows, date(2024, 1, 1))
    assert [i.ticker for i in u2024.instruments] == ["ALOS3"]

    u2020 = select_investable_universe(companies, rows, date(2020, 1, 1))
    assert [i.ticker for i in u2020.instruments] == ["ALSO3"]  # ALOS3 so existe em 2023
    assert u2020.instruments[0].resolution == "resolved"


def test_collapse_never_promotes_future_ticker_to_past_date():
    """Em 2013 a FCA nao publicava codigo nenhum -- a classe ON continua no
    universo ESTRUTURAL, mas marcada back_projected (a camada investivel a
    reprova)."""
    companies, rows = _allos_fixture()
    u2013 = select_investable_universe(companies, rows, date(2013, 6, 15))
    assert len(u2013.instruments) == 1  # a acao ordinaria existia
    assert u2013.instruments[0].resolution == "back_projected"


def test_collapsed_naming_variants_are_counted_not_hidden():
    companies, rows = _allos_fixture()
    u2020 = select_investable_universe(companies, rows, date(2020, 1, 1))
    # ALSO3 e ALOS3 elegiveis estruturalmente em 2020; uma colapsa na outra.
    assert u2020.naming_variants_collapsed == 1


def test_16_idempotent_selection_pure_function():
    companies, instruments = _fixture_a_b()
    a = select_investable_universe(companies, instruments, date(2014, 1, 1))
    b = select_investable_universe(companies, instruments, date(2014, 1, 1))
    assert a == b


def test_suspended_company_excluded_by_default_included_on_flag():
    companies = [_company(1, "A", "2010-01-01", status="suspended")]
    instruments = [_instrument(1, "A", ticker="AAAA3")]
    default = select_investable_universe(companies, instruments, date(2015, 1, 1))
    opted_in = select_investable_universe(
        companies, instruments, date(2015, 1, 1), include_suspended=True
    )
    assert default.companies == ()
    assert 1 in {c[0] for c in opted_in.companies}


# ===========================================================================
# 15.4 -- Regressao: a excecao de lifecycle NAO vazou para o sinal
# ===========================================================================


def test_18_signal_selection_still_gates_on_available_from():
    """`select_point_in_time` (fundamentos/valuation) continua rejeitando
    `available_from` futuro -- a excecao bitemporal e SO das duas tabelas de
    lifecycle."""
    from datetime import datetime

    from stock_research.analytics.fundamentals import select_point_in_time

    boundary = datetime(2020, 5, 15, 23, 59, 59, tzinfo=UTC)
    facts = [
        {
            "statement_type": "DRE", "reference_date": "2019-12-31", "account_code": "3.01",
            "is_consolidated": True, "available_from": datetime(2020, 3, 1, tzinfo=UTC),
            "fact_id": 1, "value": 100,
        },
        {
            "statement_type": "DRE", "reference_date": "2020-03-31", "account_code": "3.01",
            "is_consolidated": True, "available_from": datetime(2020, 8, 1, tzinfo=UTC),
            "fact_id": 2, "value": 200,
        },
    ]
    kept = select_point_in_time(facts, boundary)
    assert [f["fact_id"] for f in kept] == [1]  # o fato de available_from futuro NAO passa
