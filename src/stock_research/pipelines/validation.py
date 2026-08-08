"""Validacao cruzada de precos entre yfinance e brapi (fase1.md 20-21).

Brapi so serve para conferencia recente -- plano gratuito cobre ~3 meses de
historico, nunca usar para backfill. Sem ``BRAPI_TOKEN``, pula com aviso
claro e nunca quebra (fase1.md 20).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from stock_research.config import load_settings
from stock_research.db import fetch_all, fetch_one, finish_run, start_run, upsert_many
from stock_research.logging import get_logger
from stock_research.sources.prices.brapi_source import BRAPI_FREE_PLAN_MAX_DAYS, BrapiPriceSource

logger = get_logger(__name__)

PIPELINE = "validate_prices"


def run_price_validation(ticker: str, days: int) -> dict[str, Any]:
    ticker = ticker.upper()
    source = BrapiPriceSource()
    if not source.is_available():
        logger.warning("BRAPI_TOKEN ausente: validacao cruzada pulada para %s", ticker)
        return {"skipped": True, "reason": "BRAPI_TOKEN ausente"}

    if days > BRAPI_FREE_PLAN_MAX_DAYS:
        logger.warning(
            "brapi (plano gratuito) cobre so ~%d dias; %d pedidos foram reduzidos.",
            BRAPI_FREE_PLAN_MAX_DAYS, days,
        )
        days = BRAPI_FREE_PLAN_MAX_DAYS

    instrument = fetch_one("select instrument_id from public.instruments where ticker = %s", [ticker])
    if instrument is None:
        raise ValueError(f"instrumento nao cadastrado: {ticker}")

    run_id = start_run(PIPELINE, provider=source.name, ticker=ticker, params={"days": days})
    try:
        result = _compare(instrument["instrument_id"], ticker, days, source, run_id)
        finish_run(run_id, status="success", records_raw=result["compared"], records_inserted=result["compared"])
        return {"skipped": False, **result}
    except Exception as exc:
        finish_run(run_id, status="failed", error_message=str(exc))
        raise


def _compare(instrument_id: int, ticker: str, days: int, source: BrapiPriceSource, run_id: int) -> dict[str, Any]:
    settings = load_settings()["price_validation"]
    end = date.today()
    start = end - timedelta(days=days)

    yf_closes = {
        r["trade_date"]: float(r["close"])
        for r in fetch_all(
            "select trade_date, close from public.daily_prices "
            "where instrument_id = %s and source = 'yfinance' and trade_date >= %s and close is not null",
            [instrument_id, start],
        )
    }
    fetched = source.fetch_daily_history(ticker, start, end + timedelta(days=1))
    brapi_closes = {
        r["trade_date"]: float(r["close"]) for r in fetched.frame.to_dict("records") if r.get("close") is not None
    }

    common_dates = sorted(set(yf_closes) & set(brapi_closes))
    if not common_dates:
        logger.warning("nenhuma data em comum entre yfinance e brapi para %s", ticker)

    rows = [_validation_row(instrument_id, d, yf_closes[d], brapi_closes[d], settings, run_id) for d in common_dates]
    stats = upsert_many("price_validations", rows, conflict_columns=["instrument_id", "trade_date", "source_a", "source_b"])
    return {"compared": len(rows), "by_status": _count_by_status(rows), "written": stats}


def _validation_row(
    instrument_id: int, trade_date: date, close_a: float, close_b: float, cfg: dict[str, Any], run_id: int
) -> dict[str, Any]:
    diff_abs = close_a - close_b
    diff_pct = abs(diff_abs) / close_a if close_a else None
    return {
        "instrument_id": instrument_id,
        "trade_date": trade_date,
        "source_a": "yfinance",
        "source_b": "brapi",
        "close_a": close_a,
        "close_b": close_b,
        "difference_abs": diff_abs,
        "difference_pct": diff_pct,
        "status": _status(diff_pct, cfg["warning_pct"], cfg["error_pct"]),
        "run_id": run_id,
    }


def _status(diff_pct: float | None, warning_pct: float, error_pct: float) -> str:
    if diff_pct is None:
        return "missing"
    if diff_pct >= error_pct:
        return "error"
    if diff_pct >= warning_pct:
        return "warning"
    return "ok"


def _count_by_status(rows: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts
