"""Liquidez point-in-time (Fase 3 M2).

`compute_liquidity_series` e uma funcao PURA -- recebe o calendario de pregoes
e as linhas de preco, devolve as metricas por data. Sem I/O, testavel sem
banco, mesmo espirito de `analytics.fundamentals.select_point_in_time`.

REGRAS DURAS (Opus, regra 6):

* Volume financeiro = ``close`` **BRUTO** x ``volume``. **PROIBIDO**
  ``adj_close``: e recalculado retroativamente a partir de proventos e splits
  FUTUROS -- usa-lo numa metrica historica injeta informacao do futuro e torna
  o numero irreproduzivel (a Fase 1.1 mediu 81% das linhas do PETR4 mudando
  entre duas leituras da mesma serie ajustada). Este modulo nunca le
  ``adj_close``; ha teste dedicado.
* Janelas de **20 e 60 PREGOES**, contadas por
  ``trading_calendar.trading_day_index`` -- nunca dias corridos.
* Somente ``trade_date <= as_of_date``.

MEDIA sobre a janela ESPERADA: um pregao em que o papel nao negociou conta como
volume zero. E a medida honesta de "quanto da para negociar por pregao";
dividir so pelos dias com negocio superestimaria papel ilíquido.
``trading_days_*`` x ``expected_trading_days_*`` expoem a esparsidade para quem
quiser a outra leitura.

MEDIANA de 60 alem da media: volume financeiro tem cauda pesada -- um unico
leilao infla a media de 20 dias e faz papel morto parecer liquido.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

CALCULATION_VERSION = "liquidity_v1"
SOURCE = "daily_prices"
PRICE_FIELD = "close"  # NUNCA adj_close
WINDOWS = (20, 60)


@dataclass(frozen=True)
class DailyBar:
    """Um pregao com negocio. ``close`` e o preco BRUTO."""

    trade_date: date
    close: float
    volume: float

    @property
    def financial_volume(self) -> float:
        return self.close * self.volume


def to_bars(price_rows: list[dict[str, Any]]) -> list[DailyBar]:
    """Linhas de ``daily_prices`` -> barras. Descarta linha sem ``close`` ou
    sem ``volume``; **nunca** olha ``adj_close``."""
    bars: list[DailyBar] = []
    for r in price_rows:
        close, volume = r.get("close"), r.get("volume")
        if close is None or volume is None:
            continue
        bars.append(
            DailyBar(
                trade_date=_as_date(r["trade_date"]),
                close=float(close),
                volume=float(volume),
            )
        )
    bars.sort(key=lambda b: b.trade_date)
    return bars


def _window_stats(
    bars_by_date: dict[date, DailyBar], window_dates: list[date]
) -> tuple[float, float, int, list[float]]:
    """(volume medio, volume financeiro medio, pregoes com negocio, serie
    financeira) sobre a janela ESPERADA."""
    expected = len(window_dates)
    if expected == 0:
        return 0.0, 0.0, 0, []
    total_volume = 0.0
    total_financial = 0.0
    traded = 0
    financial_series: list[float] = []
    for d in window_dates:
        bar = bars_by_date.get(d)
        if bar is None or bar.volume <= 0:
            financial_series.append(0.0)
            continue
        traded += 1
        total_volume += bar.volume
        total_financial += bar.financial_volume
        financial_series.append(bar.financial_volume)
    return total_volume / expected, total_financial / expected, traded, financial_series


def compute_liquidity_series(
    bars: list[DailyBar],
    calendar: list[date],
    *,
    as_of_dates: list[date] | None = None,
) -> list[dict[str, Any]]:
    """Funcao pura. ``calendar`` = pregoes ordenados (``trading_calendar``).

    Para cada ``as_of``, a janela de N pregoes sao os N ultimos pregoes do
    calendario com ``trade_date <= as_of`` -- point-in-time por construcao.
    """
    bars_by_date = {b.trade_date: b for b in bars}
    calendar = sorted(calendar)
    index_of = {d: i for i, d in enumerate(calendar)}
    targets = sorted(as_of_dates) if as_of_dates is not None else list(calendar)

    out: list[dict[str, Any]] = []
    for as_of in targets:
        pos = index_of.get(as_of)
        if pos is None:
            # Nao e pregao: usa o ultimo pregao <= as_of.
            pos = _last_index_at_or_before(calendar, as_of)
            if pos is None:
                continue

        row: dict[str, Any] = {
            "as_of_date": as_of,
            "source": SOURCE,
            "price_field": PRICE_FIELD,
            "calculation_version": CALCULATION_VERSION,
        }
        quality_bits: list[str] = []
        for w in WINDOWS:
            start = max(0, pos - w + 1)
            window_dates = calendar[start : pos + 1]
            avg_vol, avg_fin, traded, fin_series = _window_stats(bars_by_date, window_dates)
            row[f"avg_volume_{w}"] = _round(avg_vol)
            row[f"avg_financial_volume_{w}"] = _round(avg_fin)
            row[f"trading_days_{w}"] = traded
            row[f"expected_trading_days_{w}"] = len(window_dates)
            if len(window_dates) < w:
                quality_bits.append(f"janela de {w} truncada em {len(window_dates)} pregoes")
            if w == 60:
                row["median_financial_volume_60"] = (
                    _round(statistics.median(fin_series)) if fin_series else None
                )

        row["quality_flag"] = "estimated" if quality_bits else "ok"
        row["quality_reason"] = "; ".join(quality_bits) or None
        out.append(row)
    return out


def _last_index_at_or_before(calendar: list[date], as_of: date) -> int | None:
    lo, hi, best = 0, len(calendar) - 1, None
    while lo <= hi:
        mid = (lo + hi) // 2
        if calendar[mid] <= as_of:
            best = mid
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _round(value: float) -> Decimal:
    return Decimal(f"{value:.4f}")


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
