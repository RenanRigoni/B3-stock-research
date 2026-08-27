"""Testes de ``analytics/wacc`` -- decomposição do §21.6 e anti-dupla-contagem."""

from __future__ import annotations

from stock_research.analytics.wacc import compute_wacc

# Insumos calcados em ordens de grandeza reais (Brasil, 2026):
BASE = dict(
    risk_free_nominal_brl=0.1167,  # yield ~13,8% - default_spread 2,13%
    beta=1.10,
    mature_market_erp=0.0423,
    country_risk_premium=0.03241,
    pretax_cost_of_debt=0.14,
    tax_rate=0.34,
    market_cap=560_000_000_000.0,
    gross_debt=340_000_000_000.0,
)


def test_wacc_bate_com_calculo_manual():
    r = compute_wacc(**BASE)
    coe = 0.1167 + 1.10 * 0.0423 + 0.03241
    cod = 0.14 * (1 - 0.34)
    total = 560e9 + 340e9
    expected = (560e9 / total) * coe + (340e9 / total) * cod
    assert abs(r["cost_of_equity"] - coe) < 1e-12
    assert abs(r["cost_of_debt"] - cod) < 1e-12
    assert abs(r["wacc"] - expected) < 1e-9
    assert r["quality_flag"] == "ok"


def test_company_credit_spread_e_sobre_o_titulo_publico():
    r = compute_wacc(**BASE)
    # spread = pretax_cost_of_debt - risk_free_nominal_brl (não sobre "livre de risco global")
    assert abs(r["company_credit_spread"] - (0.14 - 0.1167)) < 1e-12


def test_risco_brasil_nao_conta_em_dobro_no_mesmo_lugar():
    """Uma implementação buggy usaria o yield BRUTO como risk-free E somaria o
    country_risk_premium inteiro -- o WACC ficaria inflado. Aqui o risk-free já
    vem líquido do default_spread, então o CRP entra só uma vez de forma aditiva."""
    correct = compute_wacc(**BASE)
    buggy = compute_wacc(**{**BASE, "risk_free_nominal_brl": 0.1167 + 0.021275})
    # o buggy infla o cost_of_equity exatamente pelo default_spread re-somado
    assert buggy["cost_of_equity"] - correct["cost_of_equity"] > 0.02
    assert buggy["wacc"] > correct["wacc"]


def test_insumo_faltando_nao_inventa_wacc():
    r = compute_wacc(**{**BASE, "beta": None})
    assert r["wacc"] is None
    assert r["quality_flag"] == "missing_input"
    assert "beta" in r["quality_reason"]
