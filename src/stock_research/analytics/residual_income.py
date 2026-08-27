"""Residual Income Model -- valuation de instituições financeiras (fase2_plan.md 10).

Bancos não têm FCFF tradicional. O RIM avalia o patrimônio direto:

    RI_t          = Net_Income_t - cost_of_equity * Equity_{t-1}
    Equity_t       = Equity_{t-1} + Net_Income_t * (1 - payout)
    Fair_Value_Eq  = Equity_0
                     + Σ_{t=1..N} RI_t / (1 + coe)^t
                     + [ RI_{N} * (1 + g) / (coe - g) ] / (1 + coe)^N   (terminal Gordon sobre o RI)

``cost_of_equity`` vem da mesma decomposição do WACC (§21.6), sem os pesos:

    coe = risk_free_nominal_brl + beta * mature_market_erp + country_risk_premium

``margin_of_safety = (fair_value_ps - market_price_ps) / fair_value_ps`` -- número
para o usuário olhar, nunca recomendação (§11).
"""

from __future__ import annotations

from typing import Any

_MIN_COE_MINUS_G = 0.005


def cost_of_equity(
    *,
    risk_free_nominal_brl: float | None,
    beta: float | None,
    mature_market_erp: float | None,
    country_risk_premium: float | None,
) -> float | None:
    if None in (risk_free_nominal_brl, beta, mature_market_erp, country_risk_premium):
        return None
    assert risk_free_nominal_brl is not None and beta is not None
    assert mature_market_erp is not None and country_risk_premium is not None
    return risk_free_nominal_brl + beta * mature_market_erp + country_risk_premium


def compute_residual_income(
    *,
    equity_start: float | None,
    net_income_start: float | None,
    coe: float | None,
    net_income_growth: float,
    terminal_growth: float,
    payout_ratio: float,
    shares: float | None,
    market_price_per_share: float | None,
    forecast_years: int = 5,
) -> dict[str, Any]:
    if None in (equity_start, net_income_start, coe, shares) or (
        shares is not None and shares <= 0
    ):
        return {
            "fair_value_per_share": None,
            "quality_flag": "missing_input",
            "quality_reason": "requer equity, net_income, cost_of_equity e shares > 0",
        }
    assert equity_start is not None and net_income_start is not None
    assert coe is not None and shares is not None
    if equity_start <= 0:
        return {
            "fair_value_per_share": None,
            "quality_flag": "not_applicable",
            "quality_reason": "patrimônio líquido não positivo",
        }
    if coe - terminal_growth < _MIN_COE_MINUS_G:
        return {
            "fair_value_per_share": None,
            "quality_flag": "not_applicable",
            "quality_reason": f"cost_of_equity ({coe:.4f}) não está acima do terminal_growth ({terminal_growth:.4f})",
        }

    equity = equity_start
    net_income = net_income_start
    pv_ri = 0.0
    ri_by_year: list[float] = []
    for t in range(1, forecast_years + 1):
        net_income = net_income * (1.0 + net_income_growth)
        ri = net_income - coe * equity
        ri_by_year.append(ri)
        pv_ri += ri / (1.0 + coe) ** t
        equity = equity + net_income * (1.0 - payout_ratio)

    ri_n = ri_by_year[-1]
    terminal_ri = ri_n * (1.0 + terminal_growth) / (coe - terminal_growth)
    pv_terminal = terminal_ri / (1.0 + coe) ** forecast_years

    equity_value = equity_start + pv_ri + pv_terminal
    fair_value_ps = equity_value / shares
    mos = None
    if market_price_per_share is not None and fair_value_ps != 0:
        mos = (fair_value_ps - market_price_per_share) / fair_value_ps

    return {
        "cost_of_equity": coe,
        "equity_start": equity_start,
        "net_income_start": net_income_start,
        "net_income_growth": net_income_growth,
        "terminal_growth": terminal_growth,
        "payout_ratio": payout_ratio,
        "ri_by_year": ri_by_year,
        "pv_residual_income": pv_ri,
        "pv_terminal": pv_terminal,
        "equity_value": equity_value,
        "fair_value_per_share": fair_value_ps,
        "market_price_per_share": market_price_per_share,
        "margin_of_safety": mos,
        "quality_flag": "ok",
        "quality_reason": None,
    }
