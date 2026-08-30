"""Pipeline da janela canonica de preco (Fase 3 M2.1, Bloco 1).

    instrument_lifecycle + company_lifecycle + excecoes de continuidade
        -> public.instrument_price_window

**Sem rede.** So calcula limites a partir do que ja esta no banco. O backfill de
preco (blocos 3+) consome esta janela; linha do provedor fora dela nunca entra
em ``daily_prices``.

Idempotente: upsert pela PK ``instrument_id``.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from stock_research.analytics.price_window import (
    CALCULATION_VERSION,
    FROM_DAY,
    FROM_UNKNOWN,
    FROM_YEAR,
    compute_price_window,
    order_name_variants,
)
from stock_research.config import load_price_continuity_exceptions
from stock_research.db import fetch_all, finish_run, start_run, upsert_many
from stock_research.logging import get_logger

logger = get_logger(__name__)

PIPELINE = "price_window"

_UPDATE_COLUMNS = [
    "price_valid_from",
    "price_valid_to",
    "from_precision",
    "to_precision",
    "basis",
    "calculation_version",
    "computed_at",
    "run_id",
]


def _as_date(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _company_bounds() -> dict[int, tuple[date | None, date | None]]:
    """``company_id -> (effective_start, effective_end)`` do company_lifecycle.

    ``effective_end`` = ``None`` se algum intervalo segue aberto (companhia
    viva); senao o maior ``valid_to``.
    """
    rows = fetch_all(
        "select company_id, valid_from, valid_to from public.company_lifecycle"
    )
    starts: dict[int, date] = {}
    ends: dict[int, date | None] = {}
    open_ended: set[int] = set()
    for r in rows:
        cid = int(r["company_id"])
        vf = _as_date(r["valid_from"])
        vt = _as_date(r["valid_to"])
        if vf is not None and (cid not in starts or vf < starts[cid]):
            starts[cid] = vf
        if vt is None:
            open_ended.add(cid)
        elif cid not in ends or (ends[cid] is not None and vt > ends[cid]):  # type: ignore[operator]
            ends[cid] = vt
    out: dict[int, tuple[date | None, date | None]] = {}
    all_ids = set(starts) | set(ends) | open_ended
    for cid in all_ids:
        end = None if cid in open_ended else ends.get(cid)
        out[cid] = (starts.get(cid), end)
    return out


def _continuity_by_instrument() -> dict[int, date]:
    cfg = load_price_continuity_exceptions()
    out: dict[int, date] = {}
    for e in cfg.get("exceptions", []) or []:
        iid = e.get("instrument_id")
        cf = e.get("continuity_from")
        if iid is not None and cf:
            out[int(iid)] = date.fromisoformat(str(cf)[:10])
    return out


def compute_price_windows() -> dict[str, Any]:
    """Recalcula ``instrument_price_window`` para todo instrumento com linha de
    ``instrument_lifecycle`` ligada (``instrument_id IS NOT NULL``)."""
    run_id = start_run(PIPELINE, provider="internal", params={"stage": "price_window"})
    try:
        company_bounds = _company_bounds()
        continuity = _continuity_by_instrument()

        lifecycle = fetch_all(
            "select instrument_id, company_id, ticker, share_class, "
            "       valid_from, valid_to, listing_start, listing_end, "
            "       source, source_reference_year_first "
            "from public.instrument_lifecycle where instrument_id is not null"
        )

        # Agrupa por (companhia, classe) para achar sucessao de nomes.
        groups: dict[tuple[int, str], list[dict[str, Any]]] = {}
        for r in lifecycle:
            groups.setdefault((int(r["company_id"]), r["share_class"]), []).append(r)

        payload: list[dict[str, Any]] = []
        prec_from = {FROM_DAY: 0, FROM_YEAR: 0, FROM_UNKNOWN: 0}
        prec_to: dict[str, int] = {}
        case_b = 0
        parallel = 0
        for rows in groups.values():
            for r in order_name_variants(rows):
                cid = int(r["company_id"])
                cstart, cend = company_bounds.get(cid, (None, None))
                iid = int(r["instrument_id"])
                if r.get("source") == "seed_manual":
                    # Seed ja traz datas curadas -- usa-as como janela direta,
                    # sem piso de year_first (o seed E a evidencia).
                    win = compute_price_window(
                        year_first=None,
                        company_start=_as_date(r["valid_from"]),
                        company_end=None,
                        class_start=_as_date(r["listing_start"]) or _as_date(r["valid_from"]),
                        class_end=_as_date(r["listing_end"]) or _as_date(r["valid_to"]),
                        successor_year_first=r.get("successor_year_first"),
                        continuity_from=_as_date(r["valid_from"]),
                        today=date.today(),
                    )
                else:
                    win = compute_price_window(
                        year_first=r.get("source_reference_year_first"),
                        company_start=cstart,
                        company_end=cend,
                        class_start=_as_date(r["listing_start"]),
                        class_end=_as_date(r["listing_end"]),
                        successor_year_first=r.get("successor_year_first"),
                        continuity_from=continuity.get(iid),
                        today=date.today(),
                    )
                prec_from[win.from_precision] = prec_from.get(win.from_precision, 0) + 1
                prec_to[win.to_precision] = prec_to.get(win.to_precision, 0) + 1
                if r.get("successor_year_first") is not None:
                    case_b += 1
                if r.get("parallel_variants_same_year"):
                    parallel += 1
                payload.append(
                    {
                        "instrument_id": iid,
                        "price_valid_from": win.price_valid_from,
                        "price_valid_to": win.price_valid_to,
                        "from_precision": win.from_precision,
                        "to_precision": win.to_precision,
                        "basis": win.basis,
                        "calculation_version": CALCULATION_VERSION,
                        "run_id": run_id,
                    }
                )

        # Uma linha por instrument_id (o mesmo ticker pode ter varias linhas de
        # lifecycle ao longo dos anos; a janela e do instrumento).
        by_iid: dict[int, dict[str, Any]] = {}
        for row in payload:
            iid = row["instrument_id"]
            cur = by_iid.get(iid)
            if cur is None:
                by_iid[iid] = row
                continue
            # janela mais conservadora: from mais tarde, to mais cedo.
            if row["price_valid_from"] and (
                not cur["price_valid_from"] or row["price_valid_from"] > cur["price_valid_from"]
            ):
                cur["price_valid_from"] = row["price_valid_from"]
                cur["from_precision"] = row["from_precision"]
            if row["price_valid_to"] and (
                not cur["price_valid_to"] or row["price_valid_to"] < cur["price_valid_to"]
            ):
                cur["price_valid_to"] = row["price_valid_to"]
                cur["to_precision"] = row["to_precision"]

        rows_out = list(by_iid.values())
        written = 0
        for chunk in _chunks(rows_out, 500):
            written += upsert_many(
                "instrument_price_window",
                chunk,
                conflict_columns=["instrument_id"],
                update_columns=_UPDATE_COLUMNS,
            )["total"]

        discarded = _history_discarded(by_iid)

        finish_run(run_id, status="success", records_inserted=written)
        return {
            "status": "success",
            "instruments": len(rows_out),
            "written": written,
            "from_precision": prec_from,
            "to_precision": prec_to,
            "case_b_variants": case_b,
            "parallel_same_year_variants": parallel,
            "continuity_exceptions": len(continuity),
            "history_discarded_ticker_identity_not_proven": discarded,
        }
    except Exception as exc:
        logger.error("compute-price-windows falhou: %s", exc)
        finish_run(run_id, status="failed", error_message=str(exc))
        raise


def _history_discarded(by_iid: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """Para instrumentos que JA tem serie em ``daily_prices``: quantas linhas
    caem antes de ``price_valid_from`` (o custo real da correcao 1)."""
    have_prices = fetch_all(
        "select p.instrument_id, i.ticker, min(p.trade_date) f, max(p.trade_date) l, "
        "count(*) n from public.daily_prices p "
        "join public.instruments i on i.instrument_id = p.instrument_id "
        "group by p.instrument_id, i.ticker"
    )
    detail: list[dict[str, Any]] = []
    total_before = 0
    for r in have_prices:
        iid = int(r["instrument_id"])
        win = by_iid.get(iid)
        if win is None or win["price_valid_from"] is None:
            continue
        pvf = win["price_valid_from"]
        cnt = fetch_all(
            "select count(*) c from public.daily_prices "
            "where instrument_id = %s and trade_date < %s",
            [iid, pvf],
        )[0]["c"]
        if int(cnt):
            total_before += int(cnt)
            detail.append(
                {
                    "ticker": r["ticker"],
                    "instrument_id": iid,
                    "price_valid_from": str(pvf),
                    "rows_before": int(cnt),
                    "series_first": str(_as_date(r["f"])),
                }
            )
    return {"total_rows_before_window": total_before, "by_instrument": detail}


def _chunks(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]
