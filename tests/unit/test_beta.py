"""Testes de ``analytics/beta``."""

from __future__ import annotations

from datetime import date

from stock_research.analytics.beta import (
    align,
    compute_beta,
    simple_returns,
    weekly_last_price,
)


def test_weekly_last_price_pega_ultimo_pregao_da_semana():
    prices = {
        date(2026, 1, 5): 10.0,  # seg
        date(2026, 1, 9): 11.0,  # sex
        date(2026, 1, 12): 12.0,  # seg semana seguinte
    }
    wk = weekly_last_price(prices)
    assert wk[date(2026, 1, 9)] == 11.0
    assert wk[date(2026, 1, 12)] == 12.0


def test_simple_returns():
    r = simple_returns({date(2026, 1, 1): 100.0, date(2026, 1, 8): 110.0, date(2026, 1, 15): 99.0})
    assert abs(r[date(2026, 1, 8)] - 0.10) < 1e-12
    assert abs(r[date(2026, 1, 15)] - (-0.10)) < 1e-12


def test_beta_igual_a_um_quando_replica_o_indice():
    bench = [0.01, -0.02, 0.03, -0.01, 0.02, 0.00, 0.015, -0.025] * 20
    asset = list(bench)
    r = compute_beta(asset, bench, min_observations=50)
    assert abs(r["beta"] - 1.0) < 1e-9
    assert r["r_squared"] is not None and abs(r["r_squared"] - 1.0) < 1e-9
    assert r["quality_flag"] == "ok"


def test_beta_dois_quando_amplifica_o_indice():
    bench = [0.01, -0.02, 0.03, -0.01, 0.02, -0.015, 0.025, -0.005] * 20
    asset = [2 * x for x in bench]
    r = compute_beta(asset, bench, min_observations=50)
    assert abs(r["beta"] - 2.0) < 1e-9


def test_poucas_observacoes_vira_estimated():
    bench = [0.01, -0.02, 0.03, -0.01]
    asset = [0.02, -0.01, 0.02, -0.02]
    r = compute_beta(asset, bench, min_observations=104)
    assert r["quality_flag"] == "estimated"


def test_series_desalinhada_e_missing_input():
    r = compute_beta([0.01], [0.01, 0.02], min_observations=1)
    assert r["quality_flag"] == "missing_input"


def test_align():
    a = {date(2026, 1, 1): 0.1, date(2026, 1, 2): 0.2}
    b = {date(2026, 1, 2): 0.3, date(2026, 1, 3): 0.4}
    xa, xb = align(a, b)
    assert xa == [0.2] and xb == [0.3]
