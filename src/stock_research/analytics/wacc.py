"""WACC nominal em BRL -- decomposição explícita (fase2_plan.md 21.6).

Toda premissa é uma linha rastreável, nunca um número solto. O risco soberano
Brasil entra **duas vezes no modelo inteiro**, mas por dois canais diferentes e
teoricamente distintos -- isso NÃO é dupla contagem (ver §21.6):

  PASSO 1  risk_free_nominal_brl = government_bond_yield_brl - brazil_default_spread
           (título soberano em BRL não é literalmente livre de risco)

  PASSO 2  cost_of_equity = risk_free_nominal_brl
                          + beta * mature_market_erp
                          + country_risk_premium
           (country_risk_premium é DERIVADO do mesmo default_spread, mas entra
            como termo aditivo separado -- prêmio de investir em AÇÃO no Brasil,
            não o risco de crédito do título público)

  PASSO 3  cost_of_debt = (risk_free_nominal_brl + company_credit_spread)
                          * (1 - tax_rate)
           (company_credit_spread é medido SOBRE o título público, que já embute
            o risco soberano -- não sobre um "livre de risco global")

  PASSO 4  wacc = equity_weight * cost_of_equity + debt_weight * cost_of_debt
"""

from __future__ import annotations

from typing import Any


def compute_wacc(
    *,
    risk_free_nominal_brl: float | None,
    beta: float | None,
    mature_market_erp: float | None,
    country_risk_premium: float | None,
    pretax_cost_of_debt: float | None,
    tax_rate: float | None,
    market_cap: float | None,
    gross_debt: float | None,
    cost_of_debt_quality_flag: str = "ok",
    cost_of_debt_quality_reason: str | None = None,
) -> dict[str, Any]:
    """Monta o WACC passo a passo. Qualquer insumo faltando -> ``quality_flag``
    degradado e ``wacc=None`` (nunca número inventado, §10/§21).

    ``cost_of_debt_quality_flag`` propaga a qualidade do custo de dívida: quando
    a taxa usada não é a observada (piso do risk-free, conta agregada com
    câmbio), o WACC inteiro sai ``estimated`` (§36)."""
    missing = [
        name
        for name, val in {
            "risk_free_nominal_brl": risk_free_nominal_brl,
            "beta": beta,
            "mature_market_erp": mature_market_erp,
            "country_risk_premium": country_risk_premium,
            "pretax_cost_of_debt": pretax_cost_of_debt,
            "tax_rate": tax_rate,
            "market_cap": market_cap,
            "gross_debt": gross_debt,
        }.items()
        if val is None
    ]
    if missing:
        return {
            "wacc": None,
            "quality_flag": "missing_input",
            "quality_reason": "insumos ausentes: " + ", ".join(missing),
        }

    assert risk_free_nominal_brl is not None
    assert beta is not None and mature_market_erp is not None and country_risk_premium is not None
    assert pretax_cost_of_debt is not None and tax_rate is not None
    assert market_cap is not None and gross_debt is not None

    cost_of_equity = risk_free_nominal_brl + beta * mature_market_erp + country_risk_premium
    company_credit_spread = pretax_cost_of_debt - risk_free_nominal_brl
    cost_of_debt = pretax_cost_of_debt * (1.0 - tax_rate)

    total = market_cap + gross_debt
    if total <= 0:
        return {
            "wacc": None,
            "quality_flag": "missing_input",
            "quality_reason": "market_cap + gross_debt <= 0",
        }
    equity_weight = market_cap / total
    debt_weight = gross_debt / total
    wacc = equity_weight * cost_of_equity + debt_weight * cost_of_debt

    return {
        "risk_free_nominal_brl": risk_free_nominal_brl,
        "beta": beta,
        "mature_market_erp": mature_market_erp,
        "country_risk_premium": country_risk_premium,
        "cost_of_equity": cost_of_equity,
        "pretax_cost_of_debt": pretax_cost_of_debt,
        "company_credit_spread": company_credit_spread,
        "tax_rate": tax_rate,
        "cost_of_debt": cost_of_debt,
        "equity_weight": equity_weight,
        "debt_weight": debt_weight,
        "wacc": wacc,
        "quality_flag": cost_of_debt_quality_flag,
        "quality_reason": cost_of_debt_quality_reason,
    }
