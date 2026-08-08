"""``run-event-study``: retorno absoluto, excesso, market model e CAR por
evento (fase1.md 53-61, Milestone 10).

Cada evento com ``effective_trade_date`` resolvido (Milestone 9) recebe um
``event_studies`` (cabecalho: alpha/beta/janela) + um ``event_study_returns``
por horizonte (fase1.md 54, 59: positivos e pre-evento). Idempotente por
``(event_id, method, price_series, method_version)``.
"""

from __future__ import annotations

import statistics
from datetime import date
from decimal import Decimal
from typing import Any

from stock_research.analytics.event_study import (
    TradingIndexLookup,
    abnormal_returns_at_horizons,
    estimate_market_model,
    returns_at_horizons,
)
from stock_research.config import load_settings
from stock_research.db import fetch_all, finish_run, start_run, upsert_many
from stock_research.logging import get_logger

logger = get_logger(__name__)

PIPELINE = "event_study"
METHOD = "market_model"
PRICE_SERIES = "adjusted"
METHOD_VERSION = "event_study_v1"


class _CalendarIndex(TradingIndexLookup):
    def __init__(self, exchange: str) -> None:
        rows = fetch_all(
            "select trade_date, trading_day_index from public.trading_calendar "
            "where exchange = %s and trading_day_index is not null order by trading_day_index",
            [exchange],
        )
        self._date_to_index = {r["trade_date"]: r["trading_day_index"] for r in rows}
        self._index_to_date = {r["trading_day_index"]: r["trade_date"] for r in rows}

    def index_of(self, d: date) -> int | None:
        return self._date_to_index.get(d)

    def date_at(self, index: int) -> date | None:
        return self._index_to_date.get(index)


def run_event_study(ticker: str) -> dict[str, Any]:
    instrument = _get_instrument(ticker)
    benchmark = _get_benchmark(instrument["exchange"])
    run_id = start_run(PIPELINE, ticker=ticker)
    try:
        stats = _run_for_instrument(instrument, benchmark, run_id=run_id)
        finish_run(run_id, status="success", records_raw=stats["events"], records_inserted=stats["studies"])
        return {"status": "success", **stats}
    except Exception as exc:
        logger.error("run-event-study falhou para %s: %s", ticker, exc)
        finish_run(run_id, status="failed", error_message=str(exc))
        return {"status": "failed", "error": str(exc)}


def _run_for_instrument(instrument: dict[str, Any], benchmark: dict[str, Any], *, run_id: int) -> dict[str, int]:
    settings = load_settings()["event_study"]
    horizons = list(settings["horizons"])
    pre_horizons = list(settings["pre_event_horizons"])
    all_horizons = [*pre_horizons, 0, *horizons]
    est_start_offset = int(settings["estimation_start"])
    est_end_offset = int(settings["estimation_end"])
    min_observations = int(settings["min_observations"])

    events = fetch_all(
        "select event_id, effective_trade_date from public.events "
        "where instrument_id = %s and effective_trade_date is not null",
        [instrument["instrument_id"]],
    )
    if not events:
        return {"events": 0, "studies": 0}

    calendar = _CalendarIndex(instrument["exchange"])
    stock_prices = _price_series(instrument["instrument_id"])
    bench_prices = _price_series(benchmark["instrument_id"])
    stock_returns_by_date = _returns_series(instrument["instrument_id"])
    bench_returns_by_date = _returns_series(benchmark["instrument_id"])

    studies_created = 0
    for event in events:
        ref_date = event["effective_trade_date"]
        stock_horizons = returns_at_horizons(
            reference_date=ref_date, horizons=all_horizons, prices=stock_prices, calendar=calendar
        )
        bench_horizons = returns_at_horizons(
            reference_date=ref_date, horizons=all_horizons, prices=bench_prices, calendar=calendar
        )

        est_window = _estimation_window_returns(
            ref_date, est_start_offset, est_end_offset, calendar, stock_returns_by_date, bench_returns_by_date
        )
        model = estimate_market_model(
            est_window["stock"], est_window["market"], min_observations=min_observations
        )
        combined = abnormal_returns_at_horizons(stock_horizons, bench_horizons, model)

        volume_stats = _volume_and_volatility(instrument["instrument_id"], ref_date, calendar)
        data_quality = _classify_data_quality(combined, model)

        study_row = {
            "event_id": event["event_id"],
            "instrument_id": instrument["instrument_id"],
            "effective_trade_date": ref_date,
            "benchmark_instrument_id": benchmark["instrument_id"],
            "price_series": PRICE_SERIES,
            "method": METHOD,
            "estimation_window_start": est_window["window_start"],
            "estimation_window_end": est_window["window_end"],
            "observations": model.observations,
            "alpha": model.alpha,
            "beta": model.beta,
            "r_squared": model.r_squared,
            "residual_std": model.residual_std,
            "low_sample": model.low_sample,
            "volume_ratio_20": volume_stats["volume_ratio_20"],
            "volume_zscore_20": volume_stats["volume_zscore_20"],
            "volatility_pre_20": volume_stats["volatility_pre_20"],
            "volatility_post_20": volume_stats["volatility_post_20"],
            "data_quality": data_quality,
            "method_version": METHOD_VERSION,
            "run_id": run_id,
        }
        event_study_id = _upsert_study(study_row)

        return_rows = [
            {
                "event_study_id": event_study_id,
                "horizon_days": r.horizon_days,
                "return_actual": r.return_actual,
                "benchmark_return": r.benchmark_return,
                "excess_return": r.excess_return,
                "expected_return": r.expected_return,
                "abnormal_return": r.abnormal_return,
                "car": r.car,
                "end_trade_date": next((h.end_trade_date for h in stock_horizons if h.horizon_days == r.horizon_days), None),
                "is_censored": r.is_censored,
            }
            for r in combined
        ]
        upsert_many(
            "event_study_returns", return_rows, conflict_columns=["event_study_id", "horizon_days"],
            update_columns=[
                "return_actual", "benchmark_return", "excess_return", "expected_return",
                "abnormal_return", "car", "end_trade_date", "is_censored",
            ],
        )
        studies_created += 1

    return {"events": len(events), "studies": studies_created}


def _upsert_study(row: dict[str, Any]) -> int:
    """``event_studies`` ja tem chave natural
    (``unique(event_id, method, price_series, method_version)``) e nenhuma
    coluna identity no payload -- upsert direto, mesmo padrao usado para
    ``news_clusters`` e ``events``."""
    upsert_many(
        "event_studies", [row],
        conflict_columns=["event_id", "method", "price_series", "method_version"],
        update_columns=[k for k in row if k not in ("event_id", "method", "price_series", "method_version")],
    )
    found = fetch_all(
        "select event_study_id from public.event_studies "
        "where event_id = %s and method = %s and price_series = %s and method_version = %s",
        [row["event_id"], row["method"], row["price_series"], row["method_version"]],
    )
    return int(found[0]["event_study_id"])


def _price_series(instrument_id: int) -> dict[date, Decimal]:
    settings = load_settings()
    provider = settings["prices"]["primary_provider"]
    rows = fetch_all(
        "select trade_date, adj_close from public.daily_prices "
        "where instrument_id = %s and source = %s and adj_close is not null",
        [instrument_id, provider],
    )
    return {r["trade_date"]: r["adj_close"] for r in rows}


def _returns_series(instrument_id: int) -> dict[date, float]:
    settings = load_settings()
    provider = settings["prices"]["primary_provider"]
    rows = fetch_all(
        "select trade_date, return_1d_adjusted from public.daily_returns "
        "where instrument_id = %s and price_source = %s and return_1d_adjusted is not null",
        [instrument_id, provider],
    )
    return {r["trade_date"]: float(r["return_1d_adjusted"]) for r in rows}


def _estimation_window_returns(
    ref_date: date,
    start_offset: int,
    end_offset: int,
    calendar: _CalendarIndex,
    stock_returns: dict[date, float],
    bench_returns: dict[date, float],
) -> dict[str, Any]:
    """Janela ``[start_offset, end_offset]`` pregoes antes do evento
    (fase1.md 57, ex.: [-252, -30]). So entram pares onde os DOIS retornos
    (acao e benchmark) existem na mesma data -- regressao com par
    desalinhado enviesaria alpha/beta silenciosamente.
    """
    ref_index = calendar.index_of(ref_date)
    if ref_index is None:
        return {"stock": [], "market": [], "window_start": None, "window_end": None}

    dates_in_window = []
    for offset in range(start_offset, end_offset + 1):
        d = calendar.date_at(ref_index + offset)
        if d is not None:
            dates_in_window.append(d)

    stock_vals, market_vals = [], []
    for d in dates_in_window:
        s, m = stock_returns.get(d), bench_returns.get(d)
        if s is not None and m is not None:
            stock_vals.append(s)
            market_vals.append(m)

    return {
        "stock": stock_vals,
        "market": market_vals,
        "window_start": dates_in_window[0] if dates_in_window else None,
        "window_end": dates_in_window[-1] if dates_in_window else None,
    }


def _volume_and_volatility(instrument_id: int, ref_date: date, calendar: _CalendarIndex) -> dict[str, float | None]:
    """``volume_ratio_20``/``volume_zscore_20`` ja vem calculado em
    ``daily_returns`` para D0 (Milestone 2) -- so recupera. Volatilidade
    pre/pos e o desvio padrao dos retornos diarios nos 20 pregoes antes/
    depois do evento (fase1.md 60)."""
    rows = fetch_all(
        "select volume_ratio_20, volume_zscore_20 from public.daily_returns "
        "where instrument_id = %s and trade_date = %s",
        [instrument_id, ref_date],
    )
    volume_ratio = float(rows[0]["volume_ratio_20"]) if rows and rows[0]["volume_ratio_20"] is not None else None
    volume_zscore = float(rows[0]["volume_zscore_20"]) if rows and rows[0]["volume_zscore_20"] is not None else None

    returns_series = _returns_series(instrument_id)
    ref_index = calendar.index_of(ref_date)
    pre_vals, post_vals = [], []
    if ref_index is not None:
        for offset in range(-20, 0):
            d = calendar.date_at(ref_index + offset)
            if d is not None and d in returns_series:
                pre_vals.append(returns_series[d])
        for offset in range(1, 21):
            d = calendar.date_at(ref_index + offset)
            if d is not None and d in returns_series:
                post_vals.append(returns_series[d])

    return {
        "volume_ratio_20": volume_ratio,
        "volume_zscore_20": volume_zscore,
        "volatility_pre_20": statistics.pstdev(pre_vals) if len(pre_vals) >= 2 else None,
        "volatility_post_20": statistics.pstdev(post_vals) if len(post_vals) >= 2 else None,
    }


def _classify_data_quality(combined: list[Any], model: Any) -> str:
    censored = sum(1 for r in combined if r.is_censored)
    if model.alpha is None or model.beta is None:
        return "insufficient"
    if censored == 0:
        return "ok"
    if censored < len(combined):
        return "partial"
    return "insufficient"


def _get_instrument(ticker: str) -> dict[str, Any]:
    rows = fetch_all(
        "select instrument_id, exchange from public.instruments where ticker = %s", [ticker.upper()]
    )
    if not rows:
        raise ValueError(f"instrumento nao cadastrado: {ticker} (rode `stock-research init`)")
    return rows[0]


def _get_benchmark(exchange: str) -> dict[str, Any]:
    rows = fetch_all(
        "select instrument_id from public.instruments where exchange = %s and is_benchmark = true limit 1",
        [exchange],
    )
    if not rows:
        raise ValueError(f"nenhum benchmark cadastrado para {exchange} (rode `stock-research sync-prices --all`)")
    return rows[0]
