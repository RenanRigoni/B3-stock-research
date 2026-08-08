"""Checks de qualidade de preco (fase1.md 22).

Funcoes puras (``check_*``) recebem linhas ja normalizadas de ``daily_prices``
e devolvem ``Finding``s -- nunca excluem uma linha suspeita, so classificam.
``record_findings`` e a unica parte com I/O: grava cada achado via
``db.record_finding`` (``quality_findings``). Nada e descartado em silencio.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from itertools import pairwise
from typing import Any

import numpy as np

from stock_research.db import record_finding
from stock_research.logging import get_logger

logger = get_logger(__name__)

INFO, WARNING, ERROR = "INFO", "WARNING", "ERROR"
# Ratio de preco consecutivo fora deste intervalo sugere erro de escala
# (ex.: fonte trocou de centavos para reais) -- fase1.md 22.
_SCALE_ANOMALY_RATIO = 50.0


@dataclass(frozen=True)
class Finding:
    check_name: str
    severity: str
    message: str
    trade_date: date | None = None
    details: dict[str, Any] = field(default_factory=dict)


def check_duplicates(rows: Sequence[Mapping[str, Any]]) -> list[Finding]:
    seen: dict[Any, int] = {}
    for row in rows:
        seen[row["trade_date"]] = seen.get(row["trade_date"], 0) + 1
    return [
        Finding("duplicates", ERROR, f"data duplicada nos dados normalizados: {d} ({count}x)", trade_date=d)
        for d, count in seen.items()
        if count > 1
    ]


def check_chronological_order(rows: Sequence[Mapping[str, Any]]) -> list[Finding]:
    findings: list[Finding] = []
    previous: date | None = None
    for row in rows:
        d = row["trade_date"]
        if previous is not None and d < previous:
            findings.append(Finding(
                "chronological_order", WARNING,
                f"data fora de ordem: {d} apareceu depois de {previous}", trade_date=d,
            ))
        previous = d
    return findings


def check_negative_volume(rows: Sequence[Mapping[str, Any]]) -> list[Finding]:
    return [
        Finding("negative_volume", ERROR, f"volume negativo: {row['volume']}", trade_date=row["trade_date"])
        for row in rows
        if row.get("volume") is not None and row["volume"] < 0
    ]


def check_ohlc_non_positive(rows: Sequence[Mapping[str, Any]]) -> list[Finding]:
    findings = []
    for row in rows:
        bad = [f for f in ("open", "high", "low", "close") if row.get(f) is not None and row[f] <= 0]
        if bad:
            findings.append(Finding(
                "ohlc_non_positive", ERROR, f"campo(s) <= 0: {', '.join(bad)}", trade_date=row["trade_date"],
                details={"fields": bad},
            ))
    return findings


def check_high_low_consistency(rows: Sequence[Mapping[str, Any]]) -> list[Finding]:
    findings: list[Finding] = []
    for row in rows:
        high, low = row.get("high"), row.get("low")
        d = row["trade_date"]
        if high is not None and low is not None and high < low:
            findings.append(Finding("high_low_consistency", ERROR, f"high ({high}) < low ({low})", trade_date=d))
        for field_name in ("open", "close"):
            value = row.get(field_name)
            if high is not None and value is not None and high < value:
                findings.append(Finding("high_low_consistency", WARNING,
                                         f"high ({high}) < {field_name} ({value})", trade_date=d))
            if low is not None and value is not None and low > value:
                findings.append(Finding("high_low_consistency", WARNING,
                                         f"low ({low}) > {field_name} ({value})", trade_date=d))
    return findings


def check_extreme_return(rows: Sequence[Mapping[str, Any]], threshold: float) -> list[Finding]:
    ordered = sorted(rows, key=lambda r: r["trade_date"])
    findings: list[Finding] = []
    previous_close: float | None = None
    for row in ordered:
        close = row.get("close")
        if previous_close and close is not None and previous_close > 0:
            change = close / previous_close - 1
            if abs(change) > threshold:
                findings.append(Finding(
                    "extreme_return", WARNING, f"retorno diario de {change:.1%} excede o limite de {threshold:.1%}",
                    trade_date=row["trade_date"], details={"return": change},
                ))
        if close is not None:
            previous_close = close
    return findings


def check_prolonged_gap(rows: Sequence[Mapping[str, Any]], max_gap_trading_days: int) -> list[Finding]:
    ordered = sorted(rows, key=lambda r: r["trade_date"])
    findings: list[Finding] = []
    for prev, curr in pairwise(ordered):
        gap = int(np.busday_count(prev["trade_date"], curr["trade_date"]))
        if gap > max_gap_trading_days:
            findings.append(Finding(
                "prolonged_gap", WARNING,
                f"gap de aproximadamente {gap} dias uteis entre {prev['trade_date']} e {curr['trade_date']}",
                trade_date=curr["trade_date"], details={"gap_business_days": gap},
            ))
    return findings


def check_unexpected_currency(rows: Sequence[Mapping[str, Any]], expected_currency: str) -> list[Finding]:
    return [
        Finding("unexpected_currency", WARNING, f"moeda inesperada: {row['currency']} (esperado {expected_currency})",
                trade_date=row["trade_date"])
        for row in rows
        if row.get("currency") and row["currency"] != expected_currency
    ]


def check_scale_anomaly(rows: Sequence[Mapping[str, Any]]) -> list[Finding]:
    ordered = sorted(rows, key=lambda r: r["trade_date"])
    findings: list[Finding] = []
    previous_close: float | None = None
    for row in ordered:
        close = row.get("close")
        if previous_close and close is not None and previous_close > 0 and close > 0:
            ratio = close / previous_close
            if ratio > _SCALE_ANOMALY_RATIO or ratio < 1 / _SCALE_ANOMALY_RATIO:
                findings.append(Finding(
                    "scale_anomaly", WARNING,
                    f"possivel erro de escala: close mudou {ratio:.1f}x em um pregao",
                    trade_date=row["trade_date"], details={"ratio": ratio},
                ))
        if close is not None:
            previous_close = close
    return findings


def run_all_price_checks(
    rows: Sequence[Mapping[str, Any]],
    *,
    extreme_return_threshold: float,
    max_gap_trading_days: int,
    expected_currency: str = "BRL",
) -> list[Finding]:
    """Roda todos os checks de preco (fase1.md 22) sobre um lote de linhas normalizadas."""
    if not rows:
        return []
    findings: list[Finding] = []
    findings += check_duplicates(rows)
    findings += check_chronological_order(rows)
    findings += check_negative_volume(rows)
    findings += check_ohlc_non_positive(rows)
    findings += check_high_low_consistency(rows)
    findings += check_extreme_return(rows, extreme_return_threshold)
    findings += check_prolonged_gap(rows, max_gap_trading_days)
    findings += check_unexpected_currency(rows, expected_currency)
    findings += check_scale_anomaly(rows)
    return findings


def record_findings(
    run_id: int | None, pipeline: str, instrument_id: int, findings: Sequence[Finding]
) -> None:
    """Grava cada achado em ``quality_findings``. Nunca descarta nada em silencio."""
    for f in findings:
        record_finding(
            run_id, pipeline, f.check_name, f.severity, f.message,
            entity_type="daily_prices", entity_id=str(instrument_id),
            trade_date=f.trade_date, details=f.details,
        )
    if findings:
        logger.info("quality: %d achado(s) registrados para instrument_id=%d", len(findings), instrument_id)
