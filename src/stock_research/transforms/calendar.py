"""Calendario de negociacao (fase1.md 16). Funcoes puras, sem I/O.

O calendario nasce das datas reais em que o benchmark negociou -- nunca de
uma regra de calendario civil. Se sabado, domingo ou feriado nao aparecem na
lista de entrada (porque o benchmark nao negociou), eles simplesmente nao
existem no calendario: aritmetica de D+N sobre ``trading_day_index`` os pula
automaticamente.

A orquestracao que le ``daily_prices`` e grava em ``trading_calendar`` fica em
``pipelines/calendar.py`` (transforms/ nao faz I/O -- ver docs/architecture.md).
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class CalendarDay:
    trade_date: date
    trading_day_index: int
    previous_trading_day: date | None
    next_trading_day: date | None


def build_calendar_days(trading_dates: Sequence[date]) -> list[CalendarDay]:
    """Constroi o calendario a partir das datas de pregao observadas.

    ``trading_dates`` pode vir em qualquer ordem e com duplicatas; sao
    normalizadas aqui. ``trading_day_index`` e sequencial e comeca em 0.
    """
    dates = sorted(set(trading_dates))
    return [
        CalendarDay(
            trade_date=d,
            trading_day_index=idx,
            previous_trading_day=dates[idx - 1] if idx > 0 else None,
            next_trading_day=dates[idx + 1] if idx < len(dates) - 1 else None,
        )
        for idx, d in enumerate(dates)
    ]


def shift_trading_days(sorted_dates: Sequence[date], reference: date, offset: int) -> date | None:
    """D+N / D-N por pregao (fase1.md 16, usado depois pelo event study em 54).

    ``sorted_dates`` precisa estar ordenada ascendente e sem duplicatas (e o
    que ``build_calendar_days`` produz). Sabado, domingo e feriado nunca
    contam como D+1 porque simplesmente nao estao em ``sorted_dates``.

    Retorna ``None`` quando a referencia nao e um pregao conhecido ou quando o
    destino cai fora do horizonte coberto -- quem chama decide o que fazer
    (ex.: marcar ``is_censored=true`` no event study).
    """
    pos = bisect_left(sorted_dates, reference)
    if pos == len(sorted_dates) or sorted_dates[pos] != reference:
        return None
    target = pos + offset
    if target < 0 or target >= len(sorted_dates):
        return None
    return sorted_dates[target]
