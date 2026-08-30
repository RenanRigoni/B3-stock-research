"""Os 8 checks do backfill historico de precos (Fase 3 M2.1, HANDOFF rev.2).

Funcoes PURAS -- recebem dados ja buscados/normalizados, devolvem achados. O
pipeline decide o que fazer com CRITICAL (para a expansao).

    price_before_ticker   CRITICAL  linha GRAVADA antes de price_valid_from
    price_after_ticker    CRITICAL  linha GRAVADA depois de price_valid_to
    symbol_reuse          CRITICAL  gap interno > 250 pregoes, ou serie cruza
                                    company.valid_to
    duplicate_series      CRITICAL  hash das 100 primeiras (data, close) igual
                                    a outra serie
    resolved_empty        WARN      resolved/seeded e o provedor devolveu 0 linhas
    short_series          WARN      menos de 20 linhas gravadas
    abnormal_gaps         WARN      (esperado - negociado) / esperado > 0.20 na
                                    janela de 60
    calendar_drift        WARN      data gravada ausente do trading_calendar
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date
from itertools import pairwise
from typing import Any

CRITICAL = "CRITICAL"
WARN = "WARN"

_SYMBOL_REUSE_GAP_TRADING_DAYS = 250
_SHORT_SERIES_MIN = 20
_ABNORMAL_GAP_RATIO = 0.20
_GAP_WINDOW = 60


@dataclass(frozen=True)
class CheckFinding:
    name: str
    severity: str
    message: str


def series_fingerprint(written_dates: list[date], closes: list[float], *, n: int = 100) -> str | None:
    """Hash das ``n`` primeiras ``(data, close 4dp)`` -- para detectar a mesma
    serie devolvida sob dois simbolos (Yahoo reaproveita)."""
    if not written_dates:
        return None
    pairs = [
        f"{d.isoformat()}:{c:.4f}"
        for d, c in list(zip(written_dates, closes, strict=False))[:n]
        if c is not None
    ]
    if not pairs:
        return None
    return hashlib.sha256("|".join(pairs).encode("utf-8")).hexdigest()


def run_backfill_checks(
    *,
    ticker: str,
    resolution: str,
    raw_row_count: int,
    written_dates: list[date],
    written_closes: list[float],
    price_valid_from: date | None,
    price_valid_to: date | None,
    company_valid_to: date | None,
    calendar_index: dict[date, int],
    calendar_max: date | None = None,
    expected_trading_days_60: int | None,
    fingerprint: str | None,
    fingerprint_owner: dict[str, str],
) -> list[CheckFinding]:
    """Roda os 8 checks para uma tentativa. ``fingerprint_owner`` mapeia
    fingerprint -> ticker ja visto (OUTRO instrumento com a mesma serie).
    ``calendar_max`` = ultima data do trading_calendar; datas gravadas ALEM
    dela sao apenas defasagem do calendario, nao drift."""
    out: list[CheckFinding] = []

    if written_dates:
        first, last = written_dates[0], written_dates[-1]
        if price_valid_from is not None and first < price_valid_from:
            out.append(
                CheckFinding(
                    "price_before_ticker",
                    CRITICAL,
                    f"{ticker}: linha gravada {first} < price_valid_from {price_valid_from}",
                )
            )
        if price_valid_to is not None and last > price_valid_to:
            out.append(
                CheckFinding(
                    "price_after_ticker",
                    CRITICAL,
                    f"{ticker}: linha gravada {last} > price_valid_to {price_valid_to}",
                )
            )
        # symbol_reuse: gap interno grande em pregoes
        max_gap = _max_trading_gap(written_dates, calendar_index)
        if max_gap > _SYMBOL_REUSE_GAP_TRADING_DAYS:
            out.append(
                CheckFinding(
                    "symbol_reuse",
                    CRITICAL,
                    f"{ticker}: gap interno de {max_gap} pregoes (> {_SYMBOL_REUSE_GAP_TRADING_DAYS}) "
                    "-- possivel simbolo reutilizado",
                )
            )
        if company_valid_to is not None and last > company_valid_to:
            out.append(
                CheckFinding(
                    "symbol_reuse",
                    CRITICAL,
                    f"{ticker}: serie vai ate {last}, apos company.valid_to {company_valid_to}",
                )
            )
        # calendar_drift -- so datas INTERIORES (<= calendar_max) contam; datas
        # apos o fim do calendario sao defasagem, nao drift.
        drift = [
            d
            for d in written_dates
            if d not in calendar_index and (calendar_max is None or d <= calendar_max)
        ]
        if drift:
            out.append(
                CheckFinding(
                    "calendar_drift",
                    WARN,
                    f"{ticker}: {len(drift)} data(s) gravada(s) fora do trading_calendar "
                    f"(ex.: {drift[0]}) -- pode ser simbolo de outra bolsa",
                )
            )

    # duplicate_series: so quando o dono da assinatura e OUTRO instrumento.
    if (
        fingerprint is not None
        and fingerprint in fingerprint_owner
        and fingerprint_owner[fingerprint] != ticker
    ):
        out.append(
            CheckFinding(
                "duplicate_series",
                CRITICAL,
                f"{ticker}: 100 primeiras (data, close) identicas a "
                f"{fingerprint_owner[fingerprint]} -- serie duplicada entre instrumentos",
            )
        )

    if raw_row_count == 0 and resolution in {"resolved", "seeded"}:
        out.append(
            CheckFinding("resolved_empty", WARN, f"{ticker}: resolvido mas o provedor devolveu 0 linhas")
        )

    if 0 < len(written_dates) < _SHORT_SERIES_MIN:
        out.append(
            CheckFinding(
                "short_series",
                WARN,
                f"{ticker}: apenas {len(written_dates)} linha(s) gravada(s) (< {_SHORT_SERIES_MIN})",
            )
        )

    if expected_trading_days_60 and written_dates:
        recent = [d for d in written_dates if d in calendar_index]
        if recent:
            last_idx = calendar_index[recent[-1]]
            window_start_idx = last_idx - _GAP_WINDOW + 1
            traded = sum(1 for d in recent if calendar_index[d] >= window_start_idx)
            expected = min(_GAP_WINDOW, expected_trading_days_60)
            if expected and (expected - traded) / expected > _ABNORMAL_GAP_RATIO:
                out.append(
                    CheckFinding(
                        "abnormal_gaps",
                        WARN,
                        f"{ticker}: {traded}/{expected} pregoes negociados na janela de 60 "
                        f"(> {_ABNORMAL_GAP_RATIO:.0%} ausentes)",
                    )
                )

    return out


def _max_trading_gap(dates: list[date], calendar_index: dict[date, int]) -> int:
    idxs = sorted(calendar_index[d] for d in dates if d in calendar_index)
    if len(idxs) < 2:
        return 0
    return max(b - a for a, b in pairwise(idxs))


def summarize(findings: list[CheckFinding]) -> dict[str, Any]:
    by_name: dict[str, int] = {}
    for f in findings:
        by_name[f.name] = by_name.get(f.name, 0) + 1
    return {
        "critical": [f.name for f in findings if f.severity == CRITICAL],
        "warn": [f.name for f in findings if f.severity == WARN],
        "by_name": by_name,
        "messages": [f"{f.severity}:{f.name}:{f.message}" for f in findings],
    }
