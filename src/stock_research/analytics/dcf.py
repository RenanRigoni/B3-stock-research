"""FCFF DCF nominal em BRL (fase2_plan.md 10, 21.1).

5 anos de projeção explícita + Terminal Value por Gordon Growth. Tudo nominal:
FCFF, WACC e ``terminal_growth`` -- nenhuma mistura de fluxo real com taxa
nominal (§21.1).

    FCFF_t         = FCFF_0 * (1 + g_forecast)^t            para t = 1..N
    PV_explicito   = Σ FCFF_t / (1 + wacc)^t
    TV_ano_N       = FCFF_N * (1 + g_terminal) / (wacc - g_terminal)
    PV_terminal    = TV_ano_N / (1 + wacc)^N
    EV             = PV_explicito + PV_terminal
    equity_value   = EV - net_debt
    fair_value_ps  = equity_value / shares
    margin_of_safety = (fair_value_ps - market_price_ps) / fair_value_ps

``margin_of_safety`` NÃO é recomendação de compra/venda -- é um número para o
usuário olhar (§11).
"""

from __future__ import annotations

from typing import Any

_MIN_WACC_MINUS_G = 0.005  # wacc precisa estar ao menos 0,5 p.p. acima do g terminal


def compute_dcf(
    *,
    fcff_start: float | None,
    wacc: float | None,
    terminal_growth: float,
    forecast_growth: float,
    net_debt: float | None,
    shares: float | None,
    market_price_per_share: float | None,
    forecast_years: int = 5,
) -> dict[str, Any]:
    if fcff_start is None or wacc is None or net_debt is None or shares is None or shares <= 0:
        return {
            "fair_value_per_share": None,
            "quality_flag": "missing_input",
            "quality_reason": "requer fcff_start, wacc, net_debt e shares > 0",
        }
    if fcff_start <= 0:
        return {
            "fair_value_per_share": None,
            "quality_flag": "not_applicable",
            "quality_reason": f"FCFF inicial não positivo ({fcff_start:.0f}) -- DCF FCFF não se aplica",
        }
    if wacc - terminal_growth < _MIN_WACC_MINUS_G:
        return {
            "fair_value_per_share": None,
            "quality_flag": "not_applicable",
            "quality_reason": (
                f"wacc ({wacc:.4f}) não está suficientemente acima do terminal_growth "
                f"({terminal_growth:.4f}) -- Gordon Growth diverge"
            ),
        }

    fcff_by_year: list[float] = []
    pv_explicit = 0.0
    fcff = fcff_start
    for t in range(1, forecast_years + 1):
        fcff = fcff * (1.0 + forecast_growth)
        fcff_by_year.append(fcff)
        pv_explicit += fcff / (1.0 + wacc) ** t

    fcff_n = fcff_by_year[-1]
    terminal_value = fcff_n * (1.0 + terminal_growth) / (wacc - terminal_growth)
    pv_terminal = terminal_value / (1.0 + wacc) ** forecast_years

    enterprise_value = pv_explicit + pv_terminal
    equity_value = enterprise_value - net_debt
    fair_value_ps = equity_value / shares

    mos = None
    if market_price_per_share is not None and fair_value_ps != 0:
        mos = (fair_value_ps - market_price_per_share) / fair_value_ps

    return {
        "fcff_start": fcff_start,
        "forecast_growth": forecast_growth,
        "terminal_growth": terminal_growth,
        "wacc": wacc,
        "forecast_years": forecast_years,
        "fcff_by_year": fcff_by_year,
        "pv_explicit": pv_explicit,
        "terminal_value": terminal_value,
        "pv_terminal": pv_terminal,
        "enterprise_value": enterprise_value,
        "equity_value": equity_value,
        "fair_value_per_share": fair_value_ps,
        "market_price_per_share": market_price_per_share,
        "margin_of_safety": mos,
        "terminal_value_share_of_ev": pv_terminal / enterprise_value if enterprise_value else None,
        "quality_flag": "ok",
        "quality_reason": None,
    }


def compute_dcf_scenarios(
    *,
    fcff_start: float | None,
    wacc: float | None,
    terminal_growth: float,
    net_debt: float | None,
    shares: float | None,
    market_price_per_share: float | None,
    scenarios: dict[str, dict[str, float]],
    forecast_years: int = 5,
) -> dict[str, dict[str, Any]]:
    """Roda o DCF uma vez por cenário (pessimista/base/otimista, §11)."""
    return {
        name: compute_dcf(
            fcff_start=fcff_start,
            wacc=wacc,
            terminal_growth=cfg.get("terminal_growth", terminal_growth),
            forecast_growth=cfg["forecast_growth"],
            net_debt=net_debt,
            shares=shares,
            market_price_per_share=market_price_per_share,
            forecast_years=forecast_years,
        )
        for name, cfg in scenarios.items()
    }
