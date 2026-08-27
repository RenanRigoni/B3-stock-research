"""Dividend Discount Model -- segunda metodologia para bancos (fase2_plan.md 10).

    Fair_Value_ps = Σ_{t=1..N} D_t / (1 + coe)^t
                    + [ D_N * (1 + g) / (coe - g) ] / (1 + coe)^N

``D_t`` projetado a partir do provento por ação trailing 12 meses, crescendo a
``dividend_growth`` na janela explícita e a ``terminal_growth`` na perpetuidade.
``coe`` (cost of equity) é o mesmo de ``analytics.residual_income``.

DDM é a metodologia MAIS SIMPLES (§10): o insumo principal -- histórico de
proventos -- já existe em ``corporate_actions``. O que falta é a projeção
futura; a V1 usa ``dividend_growth`` fixo por cenário (config), não payout
histórico.
"""

from __future__ import annotations

from typing import Any

_MIN_COE_MINUS_G = 0.005


def compute_ddm(
    *,
    dividend_ttm_per_share: float | None,
    coe: float | None,
    dividend_growth: float,
    terminal_growth: float,
    market_price_per_share: float | None,
    forecast_years: int = 5,
) -> dict[str, Any]:
    if dividend_ttm_per_share is None or coe is None:
        return {
            "fair_value_per_share": None,
            "quality_flag": "missing_input",
            "quality_reason": "requer dividend_ttm_per_share e cost_of_equity",
        }
    if dividend_ttm_per_share <= 0:
        return {
            "fair_value_per_share": None,
            "quality_flag": "not_applicable",
            "quality_reason": "sem proventos nos últimos 12 meses -- DDM não se aplica",
        }
    if coe - terminal_growth < _MIN_COE_MINUS_G:
        return {
            "fair_value_per_share": None,
            "quality_flag": "not_applicable",
            "quality_reason": f"cost_of_equity ({coe:.4f}) não está acima do terminal_growth ({terminal_growth:.4f})",
        }

    div = dividend_ttm_per_share
    pv = 0.0
    div_by_year: list[float] = []
    for t in range(1, forecast_years + 1):
        div = div * (1.0 + dividend_growth)
        div_by_year.append(div)
        pv += div / (1.0 + coe) ** t

    div_n = div_by_year[-1]
    terminal = div_n * (1.0 + terminal_growth) / (coe - terminal_growth)
    pv_terminal = terminal / (1.0 + coe) ** forecast_years

    fair_value_ps = pv + pv_terminal
    mos = None
    if market_price_per_share is not None and fair_value_ps != 0:
        mos = (fair_value_ps - market_price_per_share) / fair_value_ps

    return {
        "cost_of_equity": coe,
        "dividend_ttm_per_share": dividend_ttm_per_share,
        "dividend_growth": dividend_growth,
        "terminal_growth": terminal_growth,
        "dividend_by_year": div_by_year,
        "pv_explicit": pv,
        "pv_terminal": pv_terminal,
        "fair_value_per_share": fair_value_ps,
        "market_price_per_share": market_price_per_share,
        "margin_of_safety": mos,
        "quality_flag": "ok",
        "quality_reason": None,
    }
