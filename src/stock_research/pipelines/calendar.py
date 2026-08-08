"""Orquestracao do calendario de negociacao (fase1.md 16).

Le as datas reais em que o benchmark negociou (``daily_prices``) e mantem
``trading_calendar`` sincronizado. Chamado automaticamente pelo pipeline de
precos sempre que o benchmark e sincronizado -- nao existe um comando de CLI
separado para isso.
"""

from __future__ import annotations

from datetime import date

from stock_research.db import fetch_all, fetch_one, upsert_many
from stock_research.logging import get_logger
from stock_research.transforms.calendar import build_calendar_days

logger = get_logger(__name__)


def rebuild_trading_calendar(exchange: str = "B3") -> dict[str, int]:
    """Reconstroi ``trading_calendar`` a partir dos pregoes observados do benchmark.

    Idempotente: upsert por ``(exchange, trade_date)``.
    """
    rows = fetch_all(
        "select distinct p.trade_date from public.daily_prices p "
        "join public.instruments i using (instrument_id) "
        "where i.is_benchmark = true and i.exchange = %s",
        [exchange],
    )
    trading_dates = [r["trade_date"] for r in rows]
    if not trading_dates:
        logger.warning("calendario: nenhum preco de benchmark encontrado para %s", exchange)
        return {"days": 0}

    days = build_calendar_days(trading_dates)
    calendar_rows = [
        {
            "exchange": exchange,
            "trade_date": d.trade_date,
            "is_trading_day": True,
            "previous_trading_day": d.previous_trading_day,
            "next_trading_day": d.next_trading_day,
            "trading_day_index": d.trading_day_index,
            "source": "benchmark_dates",
        }
        for d in days
    ]
    stats = upsert_many(
        "trading_calendar",
        calendar_rows,
        conflict_columns=["exchange", "trade_date"],
        update_columns=[
            "is_trading_day",
            "previous_trading_day",
            "next_trading_day",
            "trading_day_index",
            "source",
        ],
    )
    logger.info("calendario reconstruido: %d pregoes (%s)", len(days), exchange)
    return {"days": len(days), **stats}


def trading_day_offset(exchange: str, reference: date, offset: int) -> date | None:
    """D+N/D-N consultando ``trading_calendar`` no banco (vs. a versao pura em
    ``transforms.calendar``, que opera sobre uma lista ja carregada em memoria).

    Retorna ``None`` se o calendario ainda nao cobre ``reference`` ou o destino.
    """
    row = fetch_one(
        "select trading_day_index from public.trading_calendar "
        "where exchange = %s and trade_date = %s",
        [exchange, reference],
    )
    if row is None or row["trading_day_index"] is None:
        return None
    target = fetch_one(
        "select trade_date from public.trading_calendar "
        "where exchange = %s and trading_day_index = %s",
        [exchange, row["trading_day_index"] + offset],
    )
    return target["trade_date"] if target else None
