"""Backfill historico de precos para instrumentos resolvidos (Fase 3 M2.1).

Fluxo EXPLICITO -- ``sync-prices --all`` fica proibido para instrumento
historico. So entra o instrumento com ``resolution_status in {resolved,
seeded}``, ticker valido, ``company_id`` e ``instrument_id``. Nunca toca
``instruments.active``.

Bloco 2 desta rodada: **so o caminho ``--dry-run``** -- gera o batch file
commitavel e as linhas de ledger do que SERIA pedido. Nenhuma chamada de rede.
O caminho de execucao real e o Bloco 3.
"""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path
from typing import Any

from stock_research.analytics.price_backfill import (
    BackfillCandidate,
    select_backfill_candidates,
)
from stock_research.config import project_root
from stock_research.db import fetch_all, finish_run, insert_returning, start_run, upsert_many
from stock_research.logging import get_logger
from stock_research.sources.prices.yfinance_source import ticker_to_yahoo_symbol

logger = get_logger(__name__)

PIPELINE = "price_backfill"
PROVIDER = "yfinance"
BATCHES_DIR = project_root() / "batches"

_LIFECYCLE_QUERY = (
    "select instrument_id, company_id, ticker, share_class, valid_from, valid_to, "
    "       listing_start, listing_end, source, source_reference_year_first "
    "from public.instrument_lifecycle where instrument_id is not null"
)


def _as_date(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _load_price_windows(instrument_ids: list[int]) -> dict[int, dict[str, Any]]:
    if not instrument_ids:
        return {}
    rows = fetch_all(
        "select instrument_id, price_valid_from, price_valid_to, from_precision "
        "from public.instrument_price_window"
    )
    keep = set(instrument_ids)
    return {int(r["instrument_id"]): r for r in rows if int(r["instrument_id"]) in keep}


def _resolve_candidates(
    *,
    select: str | None,
    instrument_ids: list[int] | None,
    batch_file: Path | None,
    as_of: date,
    limit: int | None,
    offset: int,
) -> list[BackfillCandidate]:
    lifecycle = fetch_all(_LIFECYCLE_QUERY)
    all_candidates = select_backfill_candidates(lifecycle, as_of)
    by_iid = {c.instrument_id: c for c in all_candidates}

    if batch_file is not None:
        wanted = _read_batch_file(batch_file)
        missing = [i for i in wanted if i not in by_iid]
        if missing:
            raise ValueError(
                f"batch file tem instrument_id nao elegivel (nao resolved/seeded ou "
                f"sem ticker/company): {missing[:10]}"
            )
        return [by_iid[i] for i in wanted]

    if instrument_ids is not None:
        missing = [i for i in instrument_ids if i not in by_iid]
        if missing:
            raise ValueError(f"instrument_id nao elegivel: {missing}")
        return [by_iid[i] for i in instrument_ids]

    if select == "resolved":
        chosen = all_candidates[offset : offset + limit if limit is not None else None]
        return chosen

    raise ValueError("informe --select resolved | --batch-file | --instrument-id")


def _read_batch_file(path: Path) -> list[int]:
    ids: list[int] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            ids.append(int(line))
    return ids


def _write_batch_file(label: str, candidates: list[BackfillCandidate], as_of: date) -> tuple[Path, str]:
    BATCHES_DIR.mkdir(parents=True, exist_ok=True)
    path = BATCHES_DIR / f"{label}.txt"
    lines = [
        f"# Fase 3 M2.1 -- lote de backfill de precos: {label}",
        f"# as_of={as_of.isoformat()}  n={len(candidates)}  gerado por sync-historical-prices --dry-run",
        "# um instrument_id por linha; congelado no repo ANTES da primeira chamada de rede.",
        "# formato: <instrument_id>  # <ticker> <share_class> resolution=<...>",
    ]
    for c in candidates:
        lines.append(f"{c.instrument_id}  # {c.ticker} {c.share_class} resolution={c.resolution}")
    text = "\n".join(lines) + "\n"
    path.write_text(text, encoding="utf-8")
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    return path, digest


def run_backfill(
    *,
    label: str,
    select: str | None = None,
    instrument_ids: list[int] | None = None,
    batch_file: str | None = None,
    as_of: str | None = None,
    limit: int | None = None,
    offset: int = 0,
    dry_run: bool = True,
) -> dict[str, Any]:
    """Orquestra o backfill. Bloco 2: so ``dry_run=True``."""
    as_of_date = date.fromisoformat(as_of) if as_of else date.today()
    bf_path = Path(batch_file) if batch_file else None

    candidates = _resolve_candidates(
        select=select,
        instrument_ids=instrument_ids,
        batch_file=bf_path,
        as_of=as_of_date,
        limit=limit,
        offset=offset,
    )
    windows = _load_price_windows([c.instrument_id for c in candidates])

    if not dry_run:
        raise NotImplementedError(
            "execucao real do backfill e o Bloco 3 (piloto 0). "
            "Rode com --dry-run para gerar o batch file e o ledger."
        )

    # dry-run: grava (ou reusa) o batch file e registra o que SERIA pedido.
    # --batch-file: reusa o arquivo existente (congelado). --select / --instrument-id:
    # escreve batches/<label>.txt para revisao e commit ANTES da primeira rede.
    if bf_path is not None:
        out_path, digest = bf_path, _sha256_file(bf_path)
    else:
        out_path, digest = _write_batch_file(label, candidates, as_of_date)

    run_id = start_run(
        PIPELINE,
        provider=PROVIDER,
        params={"label": label, "select": select, "as_of": str(as_of_date), "dry_run": True},
    )
    backfill_run = insert_returning(
        "price_backfill_runs",
        {
            "batch_label": label,
            "batch_file": str(out_path.relative_to(project_root())),
            "batch_file_sha256": digest,
            "provider": PROVIDER,
            "params": {"select": select, "as_of": str(as_of_date), "limit": limit, "offset": offset},
            "dry_run": True,
            "requested": len(candidates),
            "attempted": 0,
            "status": "success",
            "run_id": run_id,
        },
    )
    bfr_id = int(backfill_run["backfill_run_id"])

    attempts: list[dict[str, Any]] = []
    for c in candidates:
        w = windows.get(c.instrument_id, {})
        pvf = _as_date(w.get("price_valid_from"))
        pvt = _as_date(w.get("price_valid_to"))
        attempts.append(
            {
                "backfill_run_id": bfr_id,
                "instrument_id": c.instrument_id,
                "ticker": c.ticker,
                "company_id": c.company_id,
                "provider": PROVIDER,
                "provider_symbol": ticker_to_yahoo_symbol(c.ticker),
                "lifecycle_valid_from": c.lifecycle_valid_from,
                "lifecycle_valid_to": c.lifecycle_valid_to,
                "price_window_from": pvf,
                "price_window_to": pvt,
                "price_window_precision": w.get("from_precision"),
                "requested_start": pvf,
                "requested_end": pvt or as_of_date,
                "status": "dry_run",
                "quality_flag": "ok",
            }
        )
    for chunk in _chunks(attempts, 500):
        upsert_many(
            "price_backfill_attempts",
            chunk,
            conflict_columns=["backfill_run_id", "instrument_id"],
            update_columns=[
                "provider_symbol",
                "price_window_from",
                "price_window_to",
                "price_window_precision",
                "requested_start",
                "requested_end",
                "status",
            ],
        )

    finish_run(run_id, status="success", records_inserted=len(attempts))
    missing_window = [c.ticker for c in candidates if c.instrument_id not in windows]
    return {
        "status": "success",
        "dry_run": True,
        "label": label,
        "batch_file": str(out_path.relative_to(project_root())),
        "batch_file_sha256": digest,
        "backfill_run_id": bfr_id,
        "candidates": len(candidates),
        "without_price_window": missing_window,
        "seeded": sum(1 for c in candidates if c.resolution == "seeded"),
    }


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _chunks(rows: list[dict[str, Any]], size: int) -> list[list[dict[str, Any]]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]
