"""Janela canonica de preco por instrumento (Fase 3 M2.1, Bloco 1).

Funcao PURA -- espelha ``analytics.liquidity`` / ``analytics.universe``: sem
I/O, testavel sem banco.

REGRA CANONICA (HANDOFF rev.2, correcao normativa 1):

    price_valid_from = max(
        company effective start,
        class effective start (listing_start),
        date(source_reference_year_first, 1, 1),
    )

Ser variante unica no FCA **nao** prova que o simbolo era o mesmo antes do
primeiro ano observado -- o Yahoo retroprojeta serie de predecessor sob o
simbolo atual. So uma FONTE INDEPENDENTE de continuidade (``continuity_from``:
seed curado, COTAHIST/ISIN validado, evidencia oficial equivalente) libera
preco anterior a esse limite.

Linhas do provedor anteriores a ``price_valid_from`` NAO entram em
``daily_prices`` canonico: ``partition_by_window`` as marca
``ticker_identity_not_proven``.

CASO B -- multiplas variantes de nome para a mesma ``(companhia, classe)``
(SSBR3 -> ALSO3 -> ALOS3): cada variante e truncada em
``date(year_first_do_sucessor - 1, 12, 31)``. ALOS3 (``year_first`` 2023) nunca
recebe linha anterior a 2023-01-01, mesmo que o provedor devolva a serie
inteira sob ``ALOS3.SA``.

``source_reference_year_first`` tem precisao ANUAL. A incerteza fica registrada
em ``from_precision='year'`` -- nunca convertida em precisao diaria inventada.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any

CALCULATION_VERSION = "price_window_v1"

FROM_DAY = "day"
FROM_YEAR = "year"
FROM_UNKNOWN = "unknown"

TO_DAY = "day"
TO_YEAR = "year"
TO_OPEN = "open"

# Motivos para linha do provedor FORA da janela canonica -- nunca gravada em
# daily_prices, so no bruto/ledger.
OUT_BEFORE = "ticker_identity_not_proven"
OUT_AFTER = "after_ticker_window"


@dataclass(frozen=True)
class PriceWindow:
    """Limites canonicos [price_valid_from, price_valid_to] (inclusivos)."""

    price_valid_from: date | None
    price_valid_to: date | None
    from_precision: str
    to_precision: str
    basis: dict[str, Any]

    def contains(self, day: date) -> bool:
        if self.price_valid_from is not None and day < self.price_valid_from:
            return False
        return not (self.price_valid_to is not None and day > self.price_valid_to)


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def compute_price_window(
    *,
    year_first: int | None,
    company_start: date | None,
    company_end: date | None,
    class_start: date | None,
    class_end: date | None,
    successor_year_first: int | None = None,
    continuity_from: date | None = None,
    today: date,
) -> PriceWindow:
    """Uma janela canonica. Todos os argumentos ja resolvidos pelo chamador.

    ``successor_year_first`` -- se ha uma variante de nome POSTERIOR para a
    mesma ``(companhia, classe)``, o menor ``year_first`` dela (caso B). ``None``
    quando esta e a ultima (ou unica) variante.

    ``continuity_from`` -- data provada por fonte independente (excecao de
    continuidade). Quando presente e anterior ao limite canonico, ela vence e a
    precisao inferior vira ``day``.
    """
    # --- limite inferior -----------------------------------------------------
    from_candidates: dict[str, str | None] = {
        "company_start": _iso(company_start),
        "class_start": _iso(class_start),
        "ticker_year_first": f"{year_first:04d}-01-01" if year_first is not None else None,
        "continuity": _iso(continuity_from),
    }

    canonical_parts: list[tuple[date, str, str]] = []
    if company_start is not None:
        canonical_parts.append((company_start, "company_start", FROM_DAY))
    if class_start is not None:
        canonical_parts.append((class_start, "class_start", FROM_DAY))
    if year_first is not None:
        canonical_parts.append((date(year_first, 1, 1), "ticker_year_first", FROM_YEAR))

    if canonical_parts:
        canonical_from, canonical_bind, canonical_prec = max(
            canonical_parts, key=lambda t: t[0]
        )
    else:
        canonical_from, canonical_bind, canonical_prec = None, None, FROM_UNKNOWN

    if continuity_from is not None and (
        canonical_from is None or continuity_from < canonical_from
    ):
        price_from: date | None = continuity_from
        from_bind = "continuity"
        from_prec = FROM_DAY
    else:
        price_from = canonical_from
        from_bind = canonical_bind or "none"
        from_prec = canonical_prec

    # --- limite superior ---------------------------------------------------
    successor_trunc = (
        date(successor_year_first - 1, 12, 31) if successor_year_first is not None else None
    )
    to_candidates: dict[str, str | None] = {
        "class_end": _iso(class_end),
        "company_end": _iso(company_end),
        "successor_truncation": _iso(successor_trunc),
        "today": _iso(today),
    }

    upper_parts: list[tuple[date, str, str]] = [(today, "today", TO_OPEN)]
    if class_end is not None:
        upper_parts.append((class_end, "class_end", TO_DAY))
    if company_end is not None:
        upper_parts.append((company_end, "company_end", TO_DAY))
    if successor_trunc is not None:
        upper_parts.append((successor_trunc, "successor_truncation", TO_YEAR))

    price_to, to_bind, to_prec = min(upper_parts, key=lambda t: t[0])

    # --- intervalo degenerado -------------------------------------------
    collapsed = False
    if price_from is not None and price_to is not None and price_to < price_from:
        price_to = price_from
        collapsed = True

    basis: dict[str, Any] = {
        "rule": "canonical_v1",
        "from": {"candidates": from_candidates, "binding": from_bind},
        "to": {"candidates": to_candidates, "binding": to_bind},
    }
    if collapsed:
        basis["collapsed"] = True
    if successor_year_first is not None:
        basis["case_b"] = {"successor_year_first": successor_year_first}

    return PriceWindow(
        price_valid_from=price_from,
        price_valid_to=price_to,
        from_precision=from_prec,
        to_precision=to_prec,
        basis=basis,
    )


def partition_by_window(
    rows: list[dict[str, Any]],
    window: PriceWindow,
    *,
    date_key: str = "trade_date",
) -> tuple[list[dict[str, Any]], list[tuple[dict[str, Any], str]]]:
    """Separa linhas do provedor em (dentro da janela, fora + motivo).

    As linhas fora **nunca** entram em ``daily_prices`` canonico. O chamador
    (pipeline de backfill) as conta e mantem so no bruto/ledger.
    """
    inside: list[dict[str, Any]] = []
    outside: list[tuple[dict[str, Any], str]] = []
    for row in rows:
        day = _as_date(row[date_key])
        if window.price_valid_from is not None and day < window.price_valid_from:
            outside.append((row, OUT_BEFORE))
        elif window.price_valid_to is not None and day > window.price_valid_to:
            outside.append((row, OUT_AFTER))
        else:
            inside.append(row)
    return inside, outside


def order_name_variants(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ordena variantes de nome de uma ``(companhia, classe)`` por
    ``source_reference_year_first`` e anota ``successor_year_first``.

    Variantes com o MESMO ``year_first`` sao paralelas (BRKM5/BRKM6), nao uma
    sucessao -- nenhuma trunca a outra; recebem ``parallel_variants_same_year``.
    """
    ordered = sorted(rows, key=lambda r: (r.get("source_reference_year_first") or 0, r.get("ticker") or ""))
    years: list[int] = sorted(
        {int(y) for r in ordered if (y := r.get("source_reference_year_first")) is not None}
    )
    out: list[dict[str, Any]] = []
    for r in ordered:
        yf = r.get("source_reference_year_first")
        successors = [y for y in years if yf is not None and y > yf]
        same_year = sum(1 for x in ordered if x.get("source_reference_year_first") == yf) > 1
        out.append(
            {
                **r,
                "successor_year_first": successors[0] if successors else None,
                "parallel_variants_same_year": same_year,
            }
        )
    return out


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
