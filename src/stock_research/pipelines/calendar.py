"""Orquestracao do calendario de negociacao (fase1.md 16).

Le as datas reais em que o benchmark negociou (``daily_prices``) e mantem
``trading_calendar`` sincronizado. Chamado automaticamente pelo pipeline de
precos sempre que o benchmark e sincronizado -- nao existe um comando de CLI
separado para isso.
"""

from __future__ import annotations

from datetime import date

from stock_research.db import execute, fetch_all, fetch_one, insert_many
from stock_research.logging import get_logger
from stock_research.transforms.calendar import build_calendar_days

logger = get_logger(__name__)


def rebuild_trading_calendar(exchange: str = "B3") -> dict[str, int]:
    """Reconstroi ``trading_calendar`` a partir dos pregoes observados do benchmark.

    Substituicao completa (delete + insert), nao upsert: ``trading_day_index``
    e recalculado do zero a cada chamada a partir do conjunto INTEIRO de datas
    do benchmark, entao alargar o historico desloca os indices de todas as
    linhas existentes. Um upsert por ``(exchange, trade_date)`` nao evita isso
    -- a tabela tambem tem UNIQUE ``(exchange, trading_day_index)``, que o
    ON CONFLICT no outro par de colunas nao cobre, e a insercao esbarra nessa
    segunda constraint quando um indice antigo e reatribuido a outra data.
    Delete+insert e idempotente por construcao: mesmo conjunto de precos ->
    mesmas linhas no final, nao importa quantas vezes rodar.
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
    execute("delete from public.trading_calendar where exchange = %s", [exchange])
    inserted = insert_many("trading_calendar", calendar_rows)
    logger.info("calendario reconstruido: %d pregoes (%s)", len(days), exchange)
    return {"days": len(days), "inserted": inserted}


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
