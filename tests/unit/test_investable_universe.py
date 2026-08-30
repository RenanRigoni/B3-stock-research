"""Camada INVESTIVEL do universo (Fase 3 M2).

    structural -> resolution -> price link -> liquidity -> minimum data -> ELIGIBLE

Cobre as regras 5, 8, 10 e 11 do Opus. Tudo sobre funcoes PURAS (sem banco).
"""

from __future__ import annotations

from dataclasses import fields
from datetime import date

from stock_research.analytics.universe import (
    BACK_PROJECTED_INSTRUMENT,
    ILLIQUID,
    INSUFFICIENT_TRADING_HISTORY,
    NO_PRICE_LINK,
    UNRESOLVED_INSTRUMENT,
    InvestabilityInputs,
    InvestabilityThresholds,
    UniverseInstrument,
    apply_investable_gates,
    select_structural_universe,
)
from stock_research.analytics.universe_coverage import unresolved_band

FUTURE = "2099-01-01T00:00:00+00:00"


def _company(cid, cnpj, valid_from, valid_to=None, *, status="registered", src=FUTURE):
    return {
        "company_id": cid,
        "cnpj": cnpj,
        "valid_from": date.fromisoformat(valid_from),
        "valid_to": date.fromisoformat(valid_to) if valid_to else None,
        "registration_status": status,
        "event_type": "cancellation" if valid_to else "registration",
        "source": "cvm_cad",
        "source_available_from": src,
        "source_observed_at": src,
        "ingested_at": src,
    }


def _instrument(
    cid,
    cnpj,
    *,
    instrument_id=None,
    ticker=None,
    share_class="ON",
    valid_from="2010-01-01",
    valid_to=None,
    listing_start="2010-01-01",
    listing_end=None,
    year_first=2018,
    source="cvm_fca",
    src=FUTURE,
):
    return {
        "company_id": cid,
        "cnpj": cnpj,
        "instrument_id": instrument_id,
        "ticker": ticker,
        "share_class": share_class,
        "valid_from": date.fromisoformat(valid_from) if valid_from else None,
        "valid_to": date.fromisoformat(valid_to) if valid_to else None,
        "listing_start": date.fromisoformat(listing_start) if listing_start else None,
        "listing_end": date.fromisoformat(listing_end) if listing_end else None,
        "market": "bolsa",
        "listing_venue": "B3",
        "segment": None,
        "quality_flag": "ok",
        "source": source,
        "source_reference_year_first": year_first,
        "source_reference_year": 2026,
        "source_available_from": src,
        "source_observed_at": src,
        "ingested_at": src,
    }


def _run(companies, instruments, as_of, *, inputs=None, thresholds=None):
    structural = select_structural_universe(companies, instruments, as_of)
    return apply_investable_gates(
        structural,
        inputs or InvestabilityInputs(),
        thresholds or InvestabilityThresholds(),
    )


# ===========================================================================
# Regra 8 -- sequencia obrigatoria; nada some entre etapas
# ===========================================================================


def test_soma_de_elegiveis_e_reprovados_bate_com_o_estrutural():
    companies = [_company(1, "A", "2010-01-01"), _company(2, "B", "2010-01-01")]
    instruments = [
        _instrument(1, "A", instrument_id=10, ticker="AAAA3"),          # ok
        _instrument(1, "A", instrument_id=11, ticker="AAAA4", share_class="PN"),  # sem preco
        _instrument(2, "B", ticker=None, share_class="ON"),             # unresolved
        _instrument(2, "B", ticker="BBBB4", share_class="PN", year_first=2024),  # back_projected
    ]
    inputs = InvestabilityInputs(price_dates={10: (date(2010, 1, 4), date(2020, 6, 30))})
    res = _run(companies, instruments, date(2020, 6, 30), inputs=inputs)

    total_estrutural = len(res.structural.instruments)
    total_contado = len(res.instruments) + len(res.rejections)
    assert total_contado == total_estrutural, "instrumento sumiu entre etapas"
    assert total_estrutural == 4


def test_cada_motivo_de_reprovacao_e_explicito_e_contado():
    companies = [_company(1, "A", "2010-01-01"), _company(2, "B", "2010-01-01")]
    instruments = [
        _instrument(1, "A", ticker=None),
        _instrument(2, "B", ticker="BBBB3", year_first=2024),
    ]
    res = _run(companies, instruments, date(2020, 6, 30))
    counts = res.rejection_counts
    assert counts[UNRESOLVED_INSTRUMENT] == 1
    assert counts[BACK_PROJECTED_INSTRUMENT] == 1
    assert all(r.reason for r in res.rejections)


def test_ordem_dos_gates_resolution_antes_de_price_link():
    """Instrumento nao identificavel para em `unresolved`, nunca em
    `no_price_link` -- o motivo tem de ser o primeiro que reprova."""
    companies = [_company(1, "A", "2010-01-01")]
    instruments = [_instrument(1, "A", ticker=None)]
    res = _run(companies, instruments, date(2020, 6, 30))
    assert res.rejection_counts[UNRESOLVED_INSTRUMENT] == 1
    assert res.rejection_counts[NO_PRICE_LINK] == 0


# ===========================================================================
# Regra 5 -- price link != negociavel perto de D (zumbi)
# ===========================================================================


def test_sem_instrument_id_nao_ha_price_link():
    companies = [_company(1, "A", "2010-01-01")]
    instruments = [_instrument(1, "A", instrument_id=None, ticker="AAAA3")]
    res = _run(companies, instruments, date(2020, 6, 30))
    assert res.rejection_counts[NO_PRICE_LINK] == 1


def test_preco_antigo_isolado_nao_torna_zumbi_investivel():
    """Papel com preco so ate 2011 nao pode ser investivel em 2020 so porque
    'existe algum preco historico'. Quem barra e o gate de liquidez/historico,
    nao o price link -- por isso o teste exige o limiar ativo."""
    companies = [_company(1, "A", "2010-01-01")]
    instruments = [_instrument(1, "A", instrument_id=10, ticker="AAAA3")]
    inputs = InvestabilityInputs(
        price_dates={10: (date(2010, 1, 4), date(2011, 3, 1))},
        liquidity={10: {"trading_days_60": 0, "avg_financial_volume_60": 0.0}},
    )
    sem_limiar = _run(companies, instruments, date(2020, 6, 30), inputs=inputs)
    assert len(sem_limiar.instruments) == 1  # limiar nao aprovado -> gate inativo

    com_limiar = _run(
        companies,
        instruments,
        date(2020, 6, 30),
        inputs=inputs,
        thresholds=InvestabilityThresholds(min_trading_days_60=1),
    )
    assert len(com_limiar.instruments) == 0
    assert com_limiar.rejection_counts[INSUFFICIENT_TRADING_HISTORY] == 1


def test_gate_de_liquidez_reprova_por_volume():
    companies = [_company(1, "A", "2010-01-01")]
    instruments = [_instrument(1, "A", instrument_id=10, ticker="AAAA3")]
    inputs = InvestabilityInputs(
        price_dates={10: (date(2010, 1, 4), date(2020, 6, 30))},
        liquidity={10: {"trading_days_60": 60, "avg_financial_volume_60": 1_000.0}},
    )
    res = _run(
        companies,
        instruments,
        date(2020, 6, 30),
        inputs=inputs,
        thresholds=InvestabilityThresholds(min_avg_financial_volume_60=1_000_000.0),
    )
    assert res.rejection_counts[ILLIQUID] == 1


def test_limiar_none_nao_aplica_gate_nem_reprova():
    """`fase3.md` §15 / Opus regra 7: limiar nao aprovado nao vira numero
    inventado -- o gate simplesmente nao roda."""
    companies = [_company(1, "A", "2010-01-01")]
    instruments = [_instrument(1, "A", instrument_id=10, ticker="AAAA3")]
    # price link OK, mas NENHUMA metrica de liquidez disponivel.
    inputs = InvestabilityInputs(price_dates={10: (date(2010, 1, 4), date(2020, 6, 30))})
    assert inputs.liquidity == {}
    res = _run(companies, instruments, date(2020, 6, 30), inputs=inputs)
    # Sem limiar aprovado o gate nao roda -- e o instrumento passa, em vez de
    # ser reprovado por um numero inventado.
    assert len(res.instruments) == 1
    assert res.rejection_counts[ILLIQUID] == 0


# ===========================================================================
# Regra 10 -- anti-survivorship NA CAMADA INVESTIVEL
# ===========================================================================


def _fixture_cancelada_2014():
    """Company B negociava ate 2014 e foi cancelada; A sobrevive."""
    companies = [
        _company(1, "A", "2010-01-01"),
        _company(2, "B", "2010-01-01", "2014-06-30"),
    ]
    instruments = [
        _instrument(1, "A", instrument_id=10, ticker="AAAA3", year_first=2010),
        _instrument(
            2, "B", instrument_id=20, ticker="BBBB3", year_first=2010,
            valid_to="2014-06-30", listing_end="2014-06-30",
        ),
    ]
    inputs = InvestabilityInputs(
        price_dates={
            10: (date(2010, 1, 4), date(2026, 8, 26)),
            20: (date(2010, 1, 4), date(2014, 6, 30)),
        }
    )
    return companies, instruments, inputs


def test_empresa_cancelada_em_2014_e_investivel_em_2013():
    companies, instruments, inputs = _fixture_cancelada_2014()
    res = _run(companies, instruments, date(2013, 6, 30), inputs=inputs)
    assert {i.ticker for i in res.instruments} == {"AAAA3", "BBBB3"}


def test_empresa_cancelada_em_2014_sai_do_universo_em_2016():
    companies, instruments, inputs = _fixture_cancelada_2014()
    res = _run(companies, instruments, date(2016, 6, 30), inputs=inputs)
    assert {i.ticker for i in res.instruments} == {"AAAA3"}
    assert 2 not in {c[0] for c in res.structural.companies}


def test_instrumento_estrutural_sem_preco_falha_no_price_link_e_e_contado():
    """Regra 10: permanece contado no estrutural -> falha no price link ->
    nunca some em silencio."""
    companies = [_company(1, "A", "2010-01-01")]
    instruments = [_instrument(1, "A", instrument_id=10, ticker="AAAA3", year_first=2010)]
    res = _run(companies, instruments, date(2013, 6, 30), inputs=InvestabilityInputs())
    assert len(res.structural.instruments) == 1  # continua no estrutural
    assert len(res.instruments) == 0
    assert res.rejection_counts[NO_PRICE_LINK] == 1


# ===========================================================================
# Regra 11 -- invariancia a proveniencia NA CAMADA INVESTIVEL
# ===========================================================================


def test_universe_is_invariant_to_provenance_dates_investable_layer():
    companies, instruments, inputs = _fixture_cancelada_2014()
    as_of = date(2013, 6, 30)
    base = _run(companies, instruments, as_of, inputs=inputs)

    def shift(rows, years):
        out = []
        for r in rows:
            c = dict(r)
            for k in ("source_available_from", "source_observed_at", "ingested_at"):
                c[k] = f"{int(c[k][:4]) + years}{c[k][4:]}"
            out.append(c)
        return out

    for delta in (-10, +10):
        moved = _run(shift(companies, delta), shift(instruments, delta), as_of, inputs=inputs)
        assert {i.ticker for i in moved.instruments} == {i.ticker for i in base.instruments}
        assert moved.rejection_counts == base.rejection_counts
        assert {c[0] for c in moved.structural.companies} == {
            c[0] for c in base.structural.companies
        }


def test_resolucao_nao_muda_com_proveniencia_apenas_com_year_first():
    """`source_reference_year_first` decide a resolucao; as colunas de
    proveniencia pura (`source_available_from` etc.) nao decidem nada."""
    companies = [_company(1, "A", "2010-01-01")]
    base = _instrument(1, "A", instrument_id=10, ticker="AAAA3", year_first=2010)
    mexido = {**base, "source_available_from": "1990-01-01T00:00:00+00:00"}
    inputs = InvestabilityInputs(price_dates={10: (date(2010, 1, 4), date(2013, 6, 30))})
    a = _run(companies, [base], date(2013, 6, 30), inputs=inputs)
    b = _run(companies, [mexido], date(2013, 6, 30), inputs=inputs)
    assert [i.resolution for i in a.instruments] == [i.resolution for i in b.instruments]


# ===========================================================================
# Handoff §5.3 -- nada de futuro exposto a estrategia
# ===========================================================================


def test_retorno_investivel_nao_expoe_valid_to_nem_listing_end():
    names = {f.name for f in fields(UniverseInstrument)}
    for proibido in ("valid_to", "listing_end", "valid_from", "listing_start"):
        assert proibido not in names


# ===========================================================================
# Bandas normativas (Opus)
# ===========================================================================


def test_bandas_de_unresolved_rate():
    assert unresolved_band(0.05) == "low"
    assert unresolved_band(0.10) == "moderate"
    assert unresolved_band(0.29) == "moderate"
    assert unresolved_band(0.30) == "high"
    assert unresolved_band(0.59) == "high"
    assert unresolved_band(0.60) == "severe"
    assert unresolved_band(0.998) == "severe"
