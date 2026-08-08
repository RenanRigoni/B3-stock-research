"""Retornos diarios (fase1.md 14-16).

``compute_daily_returns_frame`` e o calculo puro (testavel sem rede/banco); a
orquestracao -- ler precos e benchmark, gravar ``daily_returns`` -- fica em
``compute_and_store_returns``.

Recalculamos a serie inteira do instrumento a cada chamada, nao so a janela
afetada: as estatisticas moveis (``volume_avg_20``, ``volatility_20``)
dependem do historico, e o volume de dados de um projeto pessoal e pequeno o
bastante para isso ser barato. Prioridade do projeto e correcao > performance
(README) -- e a alternativa (recalcular so a janela nova) arriscaria deixar
uma estatistica movel desatualizada se um valor historico for corrigido.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from stock_research.config import load_settings
from stock_research.db import fetch_all, fetch_one, finish_run, start_run, upsert_many
from stock_research.logging import get_logger

logger = get_logger(__name__)

PIPELINE = "returns"
CALCULATION_VERSION = "returns_v1"
ROLLING_WINDOW = 20


def compute_daily_returns_frame(
    price_rows: pd.DataFrame,
    benchmark_returns: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Calcula as colunas de ``daily_returns`` a partir de precos ja carregados.

    ``price_rows``: colunas ``trade_date``, ``close``, ``adj_close``, ``volume``,
    um instrumento, qualquer ordem. Cada linha de entrada e um pregao real --
    nunca preenchemos retorno em dia sem pregao porque nunca inventamos uma
    linha que nao veio de ``daily_prices`` (fase1.md 14).

    ``benchmark_returns``: mesmas colunas ``trade_date``/``return_1d_adjusted``
    do benchmark, ja calculadas. Datas sem contrapartida no benchmark ficam
    com ``benchmark_return_1d``/``excess_return_1d`` NULL.
    """
    if price_rows.empty:
        return price_rows.copy()

    df = (
        price_rows.sort_values("trade_date")
        .drop_duplicates("trade_date", keep="last")
        .reset_index(drop=True)
    )
    close = df["close"].astype(float)
    adj_close = df["adj_close"].astype(float)

    df["return_1d_price"] = close.pct_change()
    df["return_1d_adjusted"] = adj_close.pct_change()
    with np.errstate(divide="ignore", invalid="ignore"):
        df["log_return_1d"] = np.log(adj_close / adj_close.shift(1)).replace([np.inf, -np.inf], np.nan)

    df = _with_volume_stats(df)
    df["volatility_20"] = df["return_1d_adjusted"].rolling(ROLLING_WINDOW, min_periods=2).std(ddof=1)
    return _with_benchmark(df, benchmark_returns)


def _with_volume_stats(df: pd.DataFrame) -> pd.DataFrame:
    volume = df["volume"].astype(float)
    df["volume_avg_20"] = volume.rolling(ROLLING_WINDOW, min_periods=1).mean()
    df["volume_median_20"] = volume.rolling(ROLLING_WINDOW, min_periods=1).median()
    df["volume_ratio_20"] = volume / df["volume_avg_20"].replace(0, np.nan)
    vol_std = volume.rolling(ROLLING_WINDOW, min_periods=2).std(ddof=1)
    df["volume_zscore_20"] = (volume - df["volume_avg_20"]) / vol_std.replace(0, np.nan)
    return df


def _with_benchmark(df: pd.DataFrame, benchmark_returns: pd.DataFrame | None) -> pd.DataFrame:
    if benchmark_returns is None or benchmark_returns.empty:
        df["benchmark_return_1d"] = np.nan
        df["excess_return_1d"] = np.nan
        return df

    bench = benchmark_returns[["trade_date", "return_1d_adjusted"]].rename(
        columns={"return_1d_adjusted": "benchmark_return_1d"}
    )
    df = df.merge(bench, on="trade_date", how="left")
    df["excess_return_1d"] = df["return_1d_adjusted"] - df["benchmark_return_1d"]
    return df


def compute_and_store_returns(ticker: str, *, run_id: int | None = None) -> dict[str, int]:
    """Recalcula e grava ``daily_returns`` para um instrumento. Idempotente (upsert)."""
    settings = load_settings()
    provider = settings["prices"]["primary_provider"]

    instrument = fetch_one(
        "select instrument_id, exchange, is_benchmark from public.instruments where ticker = %s",
        [ticker.upper()],
    )
    if instrument is None:
        raise ValueError(f"instrumento nao cadastrado: {ticker}")

    owns_run = run_id is None
    if owns_run:
        run_id = start_run(PIPELINE, provider=provider, ticker=ticker)
    assert run_id is not None
    try:
        stats = _recompute(instrument, provider, run_id)
        if owns_run:
            finish_run(run_id, status="success", records_raw=stats["rows"],
                       records_inserted=stats["inserted"], records_updated=stats["updated"])
        return stats
    except Exception as exc:
        if owns_run:
            finish_run(run_id, status="failed", error_message=str(exc))
        raise


def _recompute(instrument: dict[str, Any], provider: str, run_id: int | None) -> dict[str, int]:
    price_rows = _load_price_frame(instrument["instrument_id"], provider)
    if price_rows.empty:
        return {"rows": 0, "inserted": 0, "updated": 0, "total": 0}

    benchmark_id, benchmark_returns = _load_benchmark(instrument, provider)
    result = compute_daily_returns_frame(price_rows, benchmark_returns)

    rows = [
        _return_row(instrument["instrument_id"], provider, benchmark_id, run_id, record)
        for record in result.to_dict("records")
    ]
    write_stats = upsert_many("daily_returns", rows, conflict_columns=["instrument_id", "trade_date", "price_source"])
    return {"rows": len(rows), **write_stats}


def _load_benchmark(instrument: dict[str, Any], provider: str) -> tuple[int | None, pd.DataFrame | None]:
    if instrument["is_benchmark"]:
        return None, None
    benchmark = fetch_one(
        "select instrument_id from public.instruments where is_benchmark = true and exchange = %s limit 1",
        [instrument["exchange"]],
    )
    if benchmark is None:
        return None, None
    bench_prices = _load_price_frame(benchmark["instrument_id"], provider)
    if bench_prices.empty:
        return benchmark["instrument_id"], None
    return benchmark["instrument_id"], compute_daily_returns_frame(bench_prices)


def _load_price_frame(instrument_id: int, source: str) -> pd.DataFrame:
    rows = fetch_all(
        "select trade_date, close, adj_close, volume from public.daily_prices "
        "where instrument_id = %s and source = %s order by trade_date",
        [instrument_id, source],
    )
    return pd.DataFrame(rows, columns=["trade_date", "close", "adj_close", "volume"])


def _return_row(
    instrument_id: int, provider: str, benchmark_id: int | None, run_id: int | None, record: dict[str, Any]
) -> dict[str, Any]:
    return {
        "instrument_id": instrument_id,
        "trade_date": record["trade_date"],
        "price_source": provider,
        "close": _none_if_nan(record.get("close")),
        "adj_close": _none_if_nan(record.get("adj_close")),
        "return_1d_price": _none_if_nan(record.get("return_1d_price")),
        "return_1d_adjusted": _none_if_nan(record.get("return_1d_adjusted")),
        "log_return_1d": _none_if_nan(record.get("log_return_1d")),
        "volume": _none_if_nan(record.get("volume")),
        "volume_avg_20": _none_if_nan(record.get("volume_avg_20")),
        "volume_median_20": _none_if_nan(record.get("volume_median_20")),
        "volume_ratio_20": _none_if_nan(record.get("volume_ratio_20")),
        "volume_zscore_20": _none_if_nan(record.get("volume_zscore_20")),
        "volatility_20": _none_if_nan(record.get("volatility_20")),
        "benchmark_instrument_id": benchmark_id,
        "benchmark_return_1d": _none_if_nan(record.get("benchmark_return_1d")),
        "excess_return_1d": _none_if_nan(record.get("excess_return_1d")),
        "calculation_version": CALCULATION_VERSION,
        "run_id": run_id,
    }


def _none_if_nan(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value
