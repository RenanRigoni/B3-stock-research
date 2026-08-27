"""Montagem de séries TTM (trailing twelve months) a partir de fluxos anuais +
acumulados YTD (fase2_plan.md 5).

Um valor TTM em ``(ano, trimestre)`` é a soma dos **4 trimestres isolados**
terminando ali. Trimestre isolado:

    Q1 isolado = YTD(Q1)
    Q2 isolado = YTD(Q2) - YTD(Q1)
    Q3 isolado = YTD(Q3) - YTD(Q2)
    Q4 isolado = Anual   - YTD(Q3)

``available_from`` de um ponto TTM = o MAIS RECENTE entre os
``available_from`` de todos os pacotes que entraram na conta -- é o que garante
que uma consulta point-in-time nunca use um TTM antes de o 4º trimestre ter
sido publicado (o teste de look-ahead do §5 checa exatamente isso).
``quality_flag`` = o pior entre os componentes.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

_Q_END: dict[int, tuple[int, int]] = {1: (3, 31), 2: (6, 30), 3: (9, 30), 4: (12, 31)}
_FLAG_SEVERITY = {
    "ok": 0,
    "estimated": 1,
    "inconsistent": 2,
    "incomplete": 2,
    "missing_input": 3,
    "sector_inadequate": 3,
}


@dataclass(frozen=True)
class Point:
    value: Decimal
    available_from: datetime | None
    quality_flag: str = "ok"


def combine(*pts: Point) -> tuple[datetime | None, str]:
    afs = [p.available_from for p in pts if p.available_from is not None]
    af = max(afs) if afs else None
    flag = max((p.quality_flag for p in pts), key=lambda f: _FLAG_SEVERITY.get(f, 3))
    return af, flag


def isolated_quarters(
    annual: dict[int, Point], ytd: dict[tuple[int, int], Point]
) -> dict[tuple[int, int], Point]:
    """``{(ano, trimestre): valor isolado}`` para trimestres 1..4."""
    iso: dict[tuple[int, int], Point] = {}
    for (year, q), pt in ytd.items():
        if q not in (1, 2, 3):
            continue
        if q == 1:
            iso[(year, 1)] = pt
            continue
        prev = ytd.get((year, q - 1))
        if prev is not None:
            af, flag = combine(pt, prev)
            iso[(year, q)] = Point(pt.value - prev.value, af, flag)
    for year, apt in annual.items():
        q3 = ytd.get((year, 3))
        if q3 is not None:
            af, flag = combine(apt, q3)
            iso[(year, 4)] = Point(apt.value - q3.value, af, flag)
    return iso


def _prev_quarter(year: int, q: int) -> tuple[int, int]:
    return (year, q - 1) if q > 1 else (year - 1, 4)


def ttm_series(iso: dict[tuple[int, int], Point]) -> dict[date, Point]:
    """``{data_fim_do_trimestre: valor TTM}`` -- só onde os 4 trimestres
    consecutivos que terminam ali estão todos presentes."""
    out: dict[date, Point] = {}
    for year, q in iso:
        window = [(year, q)]
        cur = (year, q)
        complete = True
        for _ in range(3):
            cur = _prev_quarter(*cur)
            if cur not in iso:
                complete = False
                break
            window.append(cur)
        if not complete:
            continue
        pts = [iso[w] for w in window]
        total = sum((p.value for p in pts), start=Decimal(0))
        af, flag = combine(*pts)
        month, day = _Q_END[q]
        out[date(year, month, day)] = Point(total, af, flag)
    return out


def assemble_ttm(annual: dict[int, Point], ytd: dict[tuple[int, int], Point]) -> dict[date, Point]:
    return ttm_series(isolated_quarters(annual, ytd))


def quarter_of(reference_date: date) -> int | None:
    """3/6/9/12 -> 1/2/3/4; qualquer outro mês -> ``None``."""
    return {3: 1, 6: 2, 9: 3, 12: 4}.get(reference_date.month)
