"""``effective_trade_date``: o campo mais critico do event study (fase1.md
39-41).

Regra conceitual (fase1.md 39), implementada aqui sem acesso a banco -- quem
chama fornece o calendario via ``CalendarLookup`` (evita reimplementar a
logica de pregao dentro de uma funcao "pura" e mantem isso testavel sem
Supabase):

    * notícia em dia sem pregão (fim de semana/feriado)   -> proximo pregão
    * notícia após o fechamento do pregão                 -> proximo pregão
    * notícia durante/antes do pregão daquele dia          -> o proprio dia
    * so a DATA e conhecida (sem hora confiavel)           -> proximo pregão
      (fase1.md 39: politica conservadora, "para evitar atribuir movimento
      anterior a publicacao" -- nunca fingir que uma noticia sem hora saiu
      as 9h da manha)

``market_session_uncertain`` fica ``True`` sempre que a hora usada nao e
garantidamente a hora real de publicacao (fase1.md 40) -- o caso pratico
aqui e ``time_precision == "hour"`` vindo do GDELT, cujo ``seendate`` e
quando o CRAWLER viu a pagina, nao a publicacao (documentado em
``transforms/news.py``).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Protocol
from zoneinfo import ZoneInfo

BRAZIL_TZ = ZoneInfo("America/Sao_Paulo")


class CalendarLookup(Protocol):
    def is_trading_day(self, d: date) -> bool: ...
    def next_trading_day(self, d: date) -> date | None: ...


@dataclass(frozen=True)
class EffectiveTradeDateResult:
    effective_trade_date: date | None
    market_session_uncertain: bool
    reasoning: str


def compute_effective_trade_date(
    *,
    event_date: date,
    event_time_local: datetime | None,
    time_precision: str,
    calendar: CalendarLookup,
    market_open_local: time,
    market_close_local: time,
) -> EffectiveTradeDateResult:
    """``time_precision``: ``exact`` | ``hour`` | ``date_only`` | ``unknown``.

    Devolve ``effective_trade_date=None`` quando o calendario nao tem
    cobertura para resolver o proximo pregao (nunca adivinha em dias
    corridos aqui -- essa aproximacao, quando necessaria, e decisao de quem
    chama, documentada em ``pipelines/prices.py:_incremental_start``).
    """
    if time_precision in ("date_only", "unknown") or event_time_local is None:
        # Sem hora confiavel, a politica e SEMPRE o proximo pregao a partir da
        # data do evento (fase1.md 39) -- mesmo que event_date ja seja pregao,
        # nao assumimos que a noticia saiu antes do fechamento.
        target = calendar.next_trading_day(event_date)
        return EffectiveTradeDateResult(
            effective_trade_date=target,
            market_session_uncertain=True,
            reasoning=f"sem hora confiavel (time_precision={time_precision}): proximo pregao a partir de {event_date}",
        )

    local_time = event_time_local.astimezone(BRAZIL_TZ)
    local_date = local_time.date()

    if not calendar.is_trading_day(local_date):
        target = calendar.next_trading_day(local_date)
        return EffectiveTradeDateResult(
            effective_trade_date=target,
            market_session_uncertain=time_precision != "exact",
            reasoning=f"{local_date} nao e pregao (fim de semana/feriado): proximo pregao",
        )

    if local_time.time() > market_close_local:
        target = calendar.next_trading_day(local_date)
        return EffectiveTradeDateResult(
            effective_trade_date=target,
            market_session_uncertain=time_precision != "exact",
            reasoning=f"publicado as {local_time.time()} apos o fechamento ({market_close_local}): proximo pregao",
        )

    # Antes ou durante o pregao (inclusive pre-mercado): o proprio dia ja
    # pode reagir. fase1.md 39 nao exige diferenciar pre-abertura de
    # durante-o-pregao -- os dois tem o mesmo primeiro pregao possivel.
    return EffectiveTradeDateResult(
        effective_trade_date=local_date,
        market_session_uncertain=time_precision != "exact",
        reasoning=f"publicado as {local_time.time()}, antes/durante o pregao ({market_open_local}-{market_close_local})",
    )
