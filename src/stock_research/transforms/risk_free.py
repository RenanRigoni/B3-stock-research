"""Risk-free nominal em BRL a partir do Tesouro Prefixado (fase2_plan.md 21.2).

Regra determinística de seleção de maturidade (nunca escolha manual):

  1. Filtrar cotações com ``Data Base <= as_of`` e ficar com a mais recente
     (último pregão disponível).
  2. Para cada uma, ``maturidade_anos = (Data Vencimento - Data Base) / 365.25``.
  3. Escolher ``min |maturidade_anos - target|`` (target = 10).
  4. Empate: preferir "Tesouro Prefixado com Juros Semestrais".
  5. ``government_bond_yield_brl`` = ponto médio (Taxa Compra + Taxa Venda) / 2.

Ajuste de risco de crédito soberano (§21.2 / §21.6):

  risk_free_nominal_brl = government_bond_yield_brl - country_default_spread
"""

from __future__ import annotations

from datetime import date
from typing import Any

from stock_research.sources.macro.tesouro import PrefixadoQuote

_SEMESTRAL = "Tesouro Prefixado com Juros Semestrais"


def _maturity_years(q: PrefixadoQuote) -> float:
    return (q.maturity - q.base_date).days / 365.25


def select_bond(
    quotes: list[PrefixadoQuote], as_of: date, *, target_maturity_years: float = 10.0
) -> PrefixadoQuote | None:
    eligible = [q for q in quotes if q.base_date <= as_of]
    if not eligible:
        return None
    last_base = max(q.base_date for q in eligible)
    day_quotes = [q for q in eligible if q.base_date == last_base and q.maturity > last_base]
    if not day_quotes:
        return None

    def key(q: PrefixadoQuote) -> tuple[float, int]:
        # menor distância ao alvo; desempate preferindo "com Juros Semestrais"
        return (abs(_maturity_years(q) - target_maturity_years), 0 if q.tipo == _SEMESTRAL else 1)

    return min(day_quotes, key=key)


def compute_risk_free(
    quotes: list[PrefixadoQuote],
    as_of: date,
    *,
    country_default_spread: float | None,
    target_maturity_years: float = 10.0,
) -> dict[str, Any]:
    """``dict`` com o risk-free e toda a proveniência, ou
    ``quality_flag='missing_input'`` quando não há cotação elegível."""
    bond = select_bond(quotes, as_of, target_maturity_years=target_maturity_years)
    if bond is None:
        return {
            "as_of_date": as_of,
            "government_yield": None,
            "default_spread": country_default_spread,
            "risk_free_rate": None,
            "bond_maturity": None,
            "bond_base_date": None,
            "bond_type": None,
            "quality_flag": "missing_input",
            "quality_reason": f"sem cotação de Tesouro Prefixado com Data Base <= {as_of}",
        }

    gov_yield = bond.taxa_media
    if country_default_spread is None:
        return {
            "as_of_date": as_of,
            "government_yield": gov_yield,
            "default_spread": None,
            "risk_free_rate": gov_yield,
            "bond_maturity": bond.maturity,
            "bond_base_date": bond.base_date,
            "bond_type": bond.tipo,
            "quality_flag": "estimated",
            "quality_reason": (
                "sem country_default_spread -- risk_free = yield bruto do título "
                "(embute risco soberano; §21.2 pede a subtração)"
            ),
        }

    return {
        "as_of_date": as_of,
        "government_yield": gov_yield,
        "default_spread": country_default_spread,
        "risk_free_rate": gov_yield - country_default_spread,
        "bond_maturity": bond.maturity,
        "bond_base_date": bond.base_date,
        "bond_type": bond.tipo,
        "quality_flag": "ok",
        "quality_reason": (
            f"Tesouro Prefixado maturidade {_maturity_years(bond):.1f}a "
            f"(alvo {target_maturity_years:.0f}a), yield {gov_yield:.4f} - "
            f"default_spread {country_default_spread:.4f}"
        ),
    }
