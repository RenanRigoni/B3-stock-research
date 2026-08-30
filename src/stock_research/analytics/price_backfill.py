"""Selecao de candidatos ao backfill historico de precos (Fase 3 M2.1).

Funcao PURA. So entra no backfill o instrumento que:

    resolution_status(linha, as_of) in {resolved, seeded}
    AND is_valid_ticker(ticker)
    AND company_id is not None
    AND instrument_id is not None
    AND respeita o lifecycle do ticker (a janela canonica cuida do intervalo)

**Nunca** le ``instruments.active`` -- isso e escopo operacional dos pipelines
da Fase 1, nao identidade. Ligar ``active`` para provocar ingestao esta
proibido (HANDOFF rev.2).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

from stock_research.transforms.instrument_lifecycle import (
    IDENTIFIABLE,
    is_valid_ticker,
    resolution_status,
)


@dataclass(frozen=True)
class BackfillCandidate:
    instrument_id: int
    ticker: str
    company_id: int
    share_class: str
    lifecycle_valid_from: date | None
    lifecycle_valid_to: date | None
    resolution: str
    source: str


def select_backfill_candidates(
    lifecycle_rows: list[dict[str, Any]], as_of: date
) -> list[BackfillCandidate]:
    """Linhas de ``instrument_lifecycle`` (+ ticker) -> candidatos, deduplicados
    por ``instrument_id`` e ordenados por ele. Pura -- sem I/O."""
    seen: set[int] = set()
    out: list[BackfillCandidate] = []
    for r in lifecycle_rows:
        iid = r.get("instrument_id")
        cid = r.get("company_id")
        tk = r.get("ticker")
        if iid is None or cid is None or tk is None:
            continue
        if not is_valid_ticker(tk):
            continue
        status = resolution_status(r, as_of)
        if status not in IDENTIFIABLE:
            continue
        if int(iid) in seen:
            continue
        seen.add(int(iid))
        out.append(
            BackfillCandidate(
                instrument_id=int(iid),
                ticker=tk,
                company_id=int(cid),
                share_class=r["share_class"],
                lifecycle_valid_from=_as_date(r.get("valid_from")),
                lifecycle_valid_to=_as_date(r.get("valid_to")),
                resolution=status,
                source=r.get("source", ""),
            )
        )
    return sorted(out, key=lambda c: c.instrument_id)


def _as_date(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
