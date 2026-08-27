"""Beta da ação vs. benchmark (IBOV) -- insumo do custo de capital próprio
(fase2_plan.md 13.5, 21.6).

Beta = cov(retorno_ação, retorno_benchmark) / var(retorno_benchmark)  (OLS).

Retornos **semanais** por padrão: reduzem o ruído de microestrutura de preço
diário sem perder janela (5 anos ~= 260 semanas). Beta BRUTO -- sem ajuste de
Blume/Bloomberg na V1 (decisão documentada em `config/wacc_v1.yaml`).
"""

from __future__ import annotations

import statistics
from datetime import date
from itertools import pairwise
from typing import Any


def weekly_last_price(prices: dict[date, float]) -> dict[date, float]:
    """Reduz a série diária ao último preço de cada semana ISO ``(ano, semana)``.
    A data-chave é a do último pregão da semana."""
    by_week: dict[tuple[int, int], tuple[date, float]] = {}
    for d, p in sorted(prices.items()):
        wk = d.isocalendar()[:2]
        cur = by_week.get(wk)
        if cur is None or d > cur[0]:
            by_week[wk] = (d, p)
    return {d: p for d, p in by_week.values()}


def simple_returns(series: dict[date, float]) -> dict[date, float]:
    """Retorno simples período a período, indexado pela data final."""
    items = sorted(series.items())
    out: dict[date, float] = {}
    for (_, p0), (d1, p1) in pairwise(items):
        if p0 and p0 != 0:
            out[d1] = p1 / p0 - 1.0
    return out


def align(a: dict[date, float], b: dict[date, float]) -> tuple[list[float], list[float]]:
    common = sorted(set(a) & set(b))
    return [a[d] for d in common], [b[d] for d in common]


def compute_beta(
    asset_returns: list[float], benchmark_returns: list[float], *, min_observations: int = 104
) -> dict[str, Any]:
    """``dict`` com beta, alpha, r², n e ``quality_flag``."""
    n = len(asset_returns)
    if n != len(benchmark_returns) or n < 2:
        return _incomplete(n, "séries desalinhadas ou curtas demais")

    var_b = statistics.pvariance(benchmark_returns)
    if var_b == 0:
        return _incomplete(n, "variância do benchmark é zero")

    mean_a = statistics.fmean(asset_returns)
    mean_b = statistics.fmean(benchmark_returns)
    cov = (
        sum(
            (a - mean_a) * (b - mean_b)
            for a, b in zip(asset_returns, benchmark_returns, strict=True)
        )
        / n
    )
    beta = cov / var_b
    alpha = mean_a - beta * mean_b

    var_a = statistics.pvariance(asset_returns)
    r_squared = (cov**2) / (var_a * var_b) if var_a > 0 else None

    flag = "ok" if n >= min_observations else "estimated"
    reason = None if flag == "ok" else f"apenas {n} observações (mínimo {min_observations})"
    return {
        "beta": beta,
        "alpha": alpha,
        "r_squared": r_squared,
        "observations": n,
        "quality_flag": flag,
        "quality_reason": reason,
    }


def _incomplete(n: int, reason: str) -> dict[str, Any]:
    return {
        "beta": None,
        "alpha": None,
        "r_squared": None,
        "observations": n,
        "quality_flag": "missing_input",
        "quality_reason": reason,
    }
