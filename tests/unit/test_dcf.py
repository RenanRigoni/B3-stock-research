"""Testes de ``analytics/dcf`` -- FCFF DCF, terminal value, cenários, MoS."""

from __future__ import annotations

from stock_research.analytics.dcf import compute_dcf, compute_dcf_scenarios

BASE = dict(
    fcff_start=90_000_000_000.0,
    wacc=0.13,
    terminal_growth=0.045,
    net_debt=340_000_000_000.0,
    shares=12_888_732_761.0,
    market_price_per_share=40.0,
)


def test_dcf_fluxo_completo_e_consistente():
    r = compute_dcf(forecast_growth=0.04, **BASE)
    assert r["quality_flag"] == "ok"
    assert len(r["fcff_by_year"]) == 5
    # FCFF cresce 4% a.a.
    assert abs(r["fcff_by_year"][0] - 90e9 * 1.04) < 1
    assert abs(r["fcff_by_year"][4] - 90e9 * 1.04**5) < 1
    # EV = PV explícito + PV terminal
    assert abs(r["enterprise_value"] - (r["pv_explicit"] + r["pv_terminal"])) < 1
    # equity = EV - net_debt ; fair value = equity / shares
    assert abs(r["equity_value"] - (r["enterprise_value"] - 340e9)) < 1
    assert abs(r["fair_value_per_share"] - r["equity_value"] / 12_888_732_761.0) < 1e-6
    # margem de segurança
    assert (
        abs(r["margin_of_safety"] - (r["fair_value_per_share"] - 40.0) / r["fair_value_per_share"])
        < 1e-9
    )


def test_terminal_value_gordon():
    r = compute_dcf(forecast_growth=0.0, **BASE)
    fcff5 = 90e9  # growth 0
    tv = fcff5 * 1.045 / (0.13 - 0.045)
    assert abs(r["terminal_value"] - tv) < 1
    assert abs(r["pv_terminal"] - tv / 1.13**5) < 1


def test_wacc_nao_acima_do_terminal_growth_e_not_applicable():
    r = compute_dcf(forecast_growth=0.04, **{**BASE, "wacc": 0.046})
    assert r["fair_value_per_share"] is None
    assert r["quality_flag"] == "not_applicable"


def test_fcff_inicial_negativo_e_not_applicable():
    r = compute_dcf(forecast_growth=0.04, **{**BASE, "fcff_start": -1_000.0})
    assert r["quality_flag"] == "not_applicable"


def test_insumo_faltando_e_missing_input():
    r = compute_dcf(forecast_growth=0.04, **{**BASE, "wacc": None})
    assert r["quality_flag"] == "missing_input"


def test_cenarios_pessimista_menor_que_otimista():
    scen = {
        "pessimista": {"forecast_growth": 0.0},
        "base": {"forecast_growth": 0.04},
        "otimista": {"forecast_growth": 0.08},
    }
    out = compute_dcf_scenarios(scenarios=scen, **BASE)
    fv = {k: v["fair_value_per_share"] for k, v in out.items()}
    assert fv["pessimista"] < fv["base"] < fv["otimista"]
