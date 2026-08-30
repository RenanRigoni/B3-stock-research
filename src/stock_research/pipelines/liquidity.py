"""Pipeline de liquidez (Fase 3 M2): ``daily_prices`` -> ``liquidity_metrics``.

Le apenas ``close`` (bruto) e ``volume`` -- **nunca** ``adj_close`` (ver o
docstring de ``analytics/liquidity.py`` e o teste de guarda). Idempotente por
chave natural ``(instrument_id, as_of_date, calculation_version)``.

Escopo deliberado: so instrumentos que **ja tem** serie em ``daily_prices``.
Este pipeline **nao baixa preco** -- expansao de cobertura de precos e decisao
separada, fora do M2 (Opus, regra 4).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from stock_research.analytics.liquidity import (
    CALCULATION_VERSION,
    compute_liquidity_series,
    to_bars,
)
from stock_research.db import fetch_all, finish_run, start_run, upsert_many
from stock_research.logging import get_logger

logger = get_logger(__name__)

PIPELINE = "liquidity"
_UPDATE_COLUMNS = [
    "avg_volume_20",
    "avg_volume_60",
    "avg_financial_volume_20",
    "avg_financial_volume_60",
    "median_financial_volume_60",
    "trading_days_20",
    "trading_days_60",
    "expected_trading_days_20",
    "expected_trading_days_60",
    "source",
    "price_field",
    "quality_flag",
    "quality_reason",
    "run_id",
]


def trading_calendar(exchange: str = "B3") -> list[date]:
    rows = fetch_all(
        "select trade_date from public.trading_calendar "
        "where exchange = %s and is_trading_day = true order by trading_day_index",
        [exchange],
    )
    return [_as_date(r["trade_date"]) for r in rows]


def instruments_with_prices() -> list[dict[str, Any]]:
    return fetch_all(
        "select distinct p.instrument_id, i.ticker "
        "from public.daily_prices p join public.instruments i "
        "on i.instrument_id = p.instrument_id order by i.ticker"
    )


def compute_liquidity(
    *, from_date: str | None = None, to_date: str | None = None
) -> dict[str, Any]:
    """Calcula ``liquidity_metrics`` para todo instrumento com serie de preco."""
    run_id = start_run(PIPELINE, provider="internal", params={"from": from_date, "to": to_date})
    try:
        calendar = trading_calendar()
        if not calendar:
            raise RuntimeError("trading_calendar vazio -- rode sync-prices/rebuild antes")

        lo = date.fromisoformat(from_date) if from_date else calendar[0]
        hi = date.fromisoformat(to_date) if to_date else calendar[-1]
        targets = [d for d in calendar if lo <= d <= hi]

        targets_by_instrument: dict[str, int] = {}
        total = 0
        for inst in instruments_with_prices():
            iid = int(inst["instrument_id"])
            # NUNCA seleciona adj_close aqui -- ver analytics/liquidity.py.
            price_rows = fetch_all(
                "select trade_date, close, volume from public.daily_prices "
                "where instrument_id = %s and trade_date <= %s order by trade_date",
                [iid, hi],
            )
            bars = to_bars(price_rows)
            if not bars:
                continue
            rows = compute_liquidity_series(bars, calendar, as_of_dates=targets)
            payload = [{**r, "instrument_id": iid, "run_id": run_id} for r in rows]
            written = 0
            for chunk in _chunks(payload, 2000):
                written += upsert_many(
                    "liquidity_metrics",
                    chunk,
                    conflict_columns=["instrument_id", "as_of_date", "calculation_version"],
                    update_columns=_UPDATE_COLUMNS,
                )["total"]
            targets_by_instrument[str(inst["ticker"])] = written
            total += written
            logger.info("liquidez %s: %d linha(s)", inst["ticker"], written)

        finish_run(run_id, status="success", records_inserted=total)
        return {
            "status": "success",
            "instruments": len(targets_by_instrument),
            "rows": total,
            "by_ticker": targets_by_instrument,
            "calculation_version": CALCULATION_VERSION,
            "first_date": str(targets[0]) if targets else None,
            "last_date": str(targets[-1]) if targets else None,
        }
    except Exception as exc:
        logger.error("compute-liquidity falhou: %s", exc)
        finish_run(run_id, status="failed", error_message=str(exc))
        raise


def _chunks(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
