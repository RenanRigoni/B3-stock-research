"""Relatorio de cobertura do universo (Fase 3 M2, Opus regra 9).

Mede o vies residual em vez de esconde-lo. Para cada data:

    structural_companies     companhias existentes e nao canceladas em D
    structural_instruments   instrumentos negociando em D (1 por companhia+classe)
    resolved_instruments     identificaveis em D (resolved | seeded)
    instruments_with_prices  com serie de preco ligada ate D
    investable_instruments   passaram TODOS os gates
    unresolved_rate          (unresolved + back_projected) / structural_instruments
    unresolved_band          low | moderate | high | severe

As bandas sao NORMATIVAS (Opus). Elas **anotam** o resultado, nao bloqueiam a
execucao -- mesmo espirito das bandas de amostra do `fase3.md` §64. `severe`
(> 60%) e gatilho de escalada para o Opus.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from stock_research.analytics.universe import (
    BACK_PROJECTED_INSTRUMENT,
    NO_PRICE_LINK,
    REJECTION_REASONS,
    UNRESOLVED_INSTRUMENT,
    InvestabilityInputs,
    InvestabilityThresholds,
    apply_investable_gates,
    load_lifecycles,
    load_liquidity,
    load_price_spans,
    select_structural_universe,
)
from stock_research.transforms.instrument_lifecycle import IDENTIFIABLE

# Bandas normativas (Opus, "residual survivorship 2010-2017").
BANDS = (
    ("low", 0.10),
    ("moderate", 0.30),
    ("high", 0.60),
    ("severe", 1.01),
)
SEVERE = "severe"


def unresolved_band(rate: float) -> str:
    for name, ceiling in BANDS:
        if rate < ceiling:
            return name
    return SEVERE


@dataclass(frozen=True)
class CoverageRow:
    as_of: date
    structural_companies: int
    structural_instruments: int
    resolved_instruments: int
    instruments_with_prices: int
    investable_instruments: int
    unresolved_rate: float
    unresolved_band: str
    naming_variants_collapsed: int
    rejections: dict[str, int]

    def as_dict(self) -> dict[str, Any]:
        return {
            "as_of": str(self.as_of),
            "structural_companies": self.structural_companies,
            "structural_instruments": self.structural_instruments,
            "resolved_instruments": self.resolved_instruments,
            "instruments_with_prices": self.instruments_with_prices,
            "investable_instruments": self.investable_instruments,
            "unresolved_rate": round(self.unresolved_rate, 4),
            "unresolved_band": self.unresolved_band,
            "naming_variants_collapsed": self.naming_variants_collapsed,
            **{f"rej_{k}": v for k, v in self.rejections.items()},
        }


def coverage_for_dates(
    dates: list[date],
    *,
    thresholds: InvestabilityThresholds | None = None,
    include_suspended: bool = False,
    allowed_share_classes: frozenset[str] | None = None,
) -> list[CoverageRow]:
    """Uma passada de leitura dos lifecycles; os gates rodam por data."""
    company_rows, instrument_rows = load_lifecycles()
    th = thresholds or InvestabilityThresholds()

    out: list[CoverageRow] = []
    for as_of in sorted(dates):
        structural = select_structural_universe(
            company_rows,
            instrument_rows,
            as_of,
            include_suspended=include_suspended,
            allowed_share_classes=allowed_share_classes,
        )
        inputs = InvestabilityInputs(
            price_dates=load_price_spans(as_of), liquidity=load_liquidity(as_of)
        )
        result = apply_investable_gates(structural, inputs, th)

        n_struct = len(structural.instruments)
        resolved = sum(1 for i in structural.instruments if i.resolution in IDENTIFIABLE)
        counts = result.rejection_counts
        unresolved = counts[UNRESOLVED_INSTRUMENT] + counts[BACK_PROJECTED_INSTRUMENT]
        rate = (unresolved / n_struct) if n_struct else 0.0
        with_prices = resolved - counts[NO_PRICE_LINK]

        out.append(
            CoverageRow(
                as_of=as_of,
                structural_companies=len(structural.companies),
                structural_instruments=n_struct,
                resolved_instruments=resolved,
                instruments_with_prices=with_prices,
                investable_instruments=len(result.instruments),
                unresolved_rate=rate,
                unresolved_band=unresolved_band(rate),
                naming_variants_collapsed=structural.naming_variants_collapsed,
                rejections={k: counts.get(k, 0) for k in REJECTION_REASONS},
            )
        )
    return out


def yearly_dates(start_year: int, end_year: int, month: int = 6, day: int = 30) -> list[date]:
    return [date(y, month, day) for y in range(start_year, end_year + 1)]


def has_severe(rows: list[CoverageRow]) -> list[CoverageRow]:
    """Datas que disparam o gatilho de escalada do Opus."""
    return [r for r in rows if r.unresolved_band == SEVERE]
