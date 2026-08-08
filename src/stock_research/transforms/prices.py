"""Normalizacao pura de precos (fase1.md 11-13). Sem I/O.

``flatten_yfinance_frame`` achata o retorno do ``yf.download`` (MultiIndex
campo/ticker) em colunas simples -- e o formato que vai tanto para o arquivo
raw em disco quanto para as funcoes de normalizacao abaixo.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

# yfinance guarda o valor do split como razao (2.0 = desdobra 1:2, 0.5 = grupa
# 2:1). >= 1 e desdobramento; < 1 e grupamento (fase1.md 13).
_SPLIT_RATIO_THRESHOLD = 1.0


def flatten_yfinance_frame(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Achata o MultiIndex (campo, ticker) do ``yf.download`` para colunas simples.

    Preservamos este formato tambem como arquivo raw em disco: e portavel em
    parquet (MultiIndex de colunas nao e) sem descartar nenhum valor devolvido
    pela fonte.
    """
    df = frame.copy()
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.reset_index().rename(columns={"Date": "trade_date", "index": "trade_date"})
    df["trade_date"] = pd.to_datetime(df["trade_date"]).dt.date
    df.insert(1, "symbol", symbol)
    return df


def to_daily_price_rows(
    frame: pd.DataFrame,
    *,
    instrument_id: int,
    source: str,
    source_symbol: str,
    currency: str,
    raw_file: str | None,
    run_id: int | None,
) -> list[dict[str, Any]]:
    """Linhas prontas para upsert em ``daily_prices`` (fase1.md 11).

    ``keepna=True`` preserva NaN do bruto; aqui, na camada curated, tambem
    preservamos como NULL -- dado ausente e melhor que dado inventado
    (fase1.md 123).
    """
    rows: list[dict[str, Any]] = []
    for record in frame.to_dict("records"):
        rows.append(
            {
                "instrument_id": instrument_id,
                "trade_date": record["trade_date"],
                "open": _clean(record.get("Open")),
                "high": _clean(record.get("High")),
                "low": _clean(record.get("Low")),
                "close": _clean(record.get("Close")),
                "adj_close": _clean(record.get("Adj Close")),
                "volume": _clean(record.get("Volume")),
                "currency": currency,
                "source": source,
                "source_symbol": source_symbol,
                "is_repaired": bool(record.get("Repaired?", False)),
                "raw_file": raw_file,
                "run_id": run_id,
            }
        )
    return rows


def to_corporate_action_rows(
    frame: pd.DataFrame,
    *,
    instrument_id: int,
    source: str,
    run_id: int | None,
) -> list[dict[str, Any]]:
    """Dividendos e splits reportados pela fonte (fase1.md 13).

    O yfinance NAO diferencia dividendo comum de JCP -- os dois chegam somados
    na coluna "Dividends". Gravamos ``action_type="dividend"`` porque e a
    classificacao compativel com o que a fonte realmente informa; nunca
    inferimos JCP a partir do valor sozinho (fase1.md 13, 123).
    """
    rows: list[dict[str, Any]] = []
    for record in frame.to_dict("records"):
        trade_date = record["trade_date"]
        dividend = _clean(record.get("Dividends"))
        if dividend:
            rows.append(_action_row(instrument_id, trade_date, "dividend", source, run_id,
                                     value=dividend, ratio=None, field="Dividends", raw_value=dividend))
        split = _clean(record.get("Stock Splits"))
        if split:
            action_type = "split" if split >= _SPLIT_RATIO_THRESHOLD else "reverse_split"
            rows.append(_action_row(instrument_id, trade_date, action_type, source, run_id,
                                     value=None, ratio=split, field="Stock Splits", raw_value=split))
    return rows


def _action_row(
    instrument_id: int,
    action_date: Any,
    action_type: str,
    source: str,
    run_id: int | None,
    *,
    value: float | None,
    ratio: float | None,
    field: str,
    raw_value: float,
) -> dict[str, Any]:
    return {
        "instrument_id": instrument_id,
        "action_date": action_date,
        "action_type": action_type,
        "value": value,
        "currency": None,
        "ratio": ratio,
        "source": source,
        "raw_payload": {"field": field, "value": raw_value},
        "run_id": run_id,
    }


def _clean(value: Any) -> Any:
    """NaN/NaT vira ``None`` -- Postgres nao entende ``float('nan')`` como NULL."""
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return float(value)
    return value
