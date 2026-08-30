"""Backfill historico de precos para instrumentos resolvidos (Fase 3 M2.1).

Fluxo EXPLICITO -- ``sync-prices --all`` fica proibido para instrumento
historico. So entra o instrumento com ``resolution_status in {resolved,
seeded}``, ticker valido, ``company_id`` e ``instrument_id``. Nunca toca
``instruments.active``.

Reusa a arquitetura de preco da Fase 1: ``daily_prices``, ``trading_calendar``,
``corporate_actions`` (nenhum sistema de preco novo). Linha do provedor FORA da
janela canonica (``instrument_price_window``) nunca entra em ``daily_prices`` --
fica so no bruto/ledger, contada como ``ticker_identity_not_proven``.

``--dry-run`` (Bloco 2): gera o batch file commitavel + o ledger do que seria
pedido, sem rede. ``--execute`` (Bloco 3+): baixa, particiona pela janela,
grava so o que esta dentro, roda os 8 checks. CRITICAL para a expansao.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

from stock_research.analytics.price_backfill import (
    BackfillCandidate,
    select_backfill_candidates,
)
from stock_research.analytics.price_window import PriceWindow, partition_by_window
from stock_research.config import data_dir, load_settings, project_root
from stock_research.db import (
    fetch_all,
    finish_run,
    insert_returning,
    start_run,
    upsert_many,
)
from stock_research.logging import get_logger
from stock_research.pipelines.price_checks import (
    CheckFinding,
    run_backfill_checks,
    series_fingerprint,
    summarize,
)
from stock_research.sources.prices.yfinance_source import (
    YFinancePriceSource,
    ticker_to_yahoo_symbol,
)
from stock_research.transforms.prices import to_corporate_action_rows, to_daily_price_rows
from stock_research.utils.ratelimit import throttle

logger = get_logger(__name__)

PIPELINE = "price_backfill"
PROVIDER = "yfinance"
SOURCE = "yfinance"
BATCHES_DIR = project_root() / "batches"

_LIFECYCLE_QUERY = (
    "select instrument_id, company_id, ticker, share_class, valid_from, valid_to, "
    "       listing_start, listing_end, source, source_reference_year_first "
    "from public.instrument_lifecycle where instrument_id is not null"
)

_ATTEMPT_UPDATE = [
    "provider_symbol",
    "lifecycle_valid_from",
    "lifecycle_valid_to",
    "price_window_from",
    "price_window_to",
    "price_window_precision",
    "requested_start",
    "requested_end",
    "returned_first_date",
    "returned_last_date",
    "row_count",
    "rows_written",
    "rows_out_of_window",
    "expected_trading_days",
    "gap_count",
    "max_gap_days",
    "attempt_count",
    "status",
    "quality_flag",
    "quality_reason",
    "checks",
    "error_message",
    "updated_at",
]


def _as_date(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


# ---------------------------------------------------------------------------
# Resolucao de candidatos + batch file
# ---------------------------------------------------------------------------


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
        return all_candidates[offset : offset + limit if limit is not None else None]

    raise ValueError("informe --select resolved | --batch-file | --instrument-id")


def _read_batch_file(path: Path) -> list[int]:
    ids: list[int] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        clean = line.split("#", 1)[0].strip()
        if clean:
            ids.append(int(clean))
    return ids


def _write_batch_file(
    label: str, candidates: list[BackfillCandidate], as_of: date
) -> tuple[Path, str]:
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
    return path, hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---------------------------------------------------------------------------
# Orquestracao
# ---------------------------------------------------------------------------


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
    as_of_date = date.fromisoformat(as_of) if as_of else date.today()
    bf_path = Path(batch_file).resolve() if batch_file else None

    candidates = _resolve_candidates(
        select=select,
        instrument_ids=instrument_ids,
        batch_file=bf_path,
        as_of=as_of_date,
        limit=limit,
        offset=offset,
    )
    windows = _load_price_windows([c.instrument_id for c in candidates])

    if bf_path is not None:
        out_path, digest = bf_path, _sha256_file(bf_path)
    else:
        out_path, digest = _write_batch_file(label, candidates, as_of_date)

    if dry_run:
        return _dry_run(label, candidates, windows, as_of_date, out_path, digest)
    return _execute(label, candidates, windows, as_of_date, out_path, digest)


# ---------------------------------------------------------------------------
# dry-run
# ---------------------------------------------------------------------------


def _dry_run(
    label: str,
    candidates: list[BackfillCandidate],
    windows: dict[int, dict[str, Any]],
    as_of_date: date,
    out_path: Path,
    digest: str,
) -> dict[str, Any]:
    run_id = start_run(
        PIPELINE,
        provider=PROVIDER,
        params={"label": label, "as_of": str(as_of_date), "dry_run": True},
    )
    bfr_id = _insert_run(label, out_path, digest, len(candidates), run_id, dry_run=True)

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
    _upsert_attempts(attempts, columns=["provider_symbol", "price_window_from", "price_window_to",
                                        "price_window_precision", "requested_start", "requested_end", "status"])
    finish_run(run_id, status="success", records_inserted=len(attempts))
    return {
        "status": "success",
        "dry_run": True,
        "label": label,
        "batch_file": str(out_path.relative_to(project_root())),
        "batch_file_sha256": digest,
        "backfill_run_id": bfr_id,
        "candidates": len(candidates),
        "without_price_window": [c.ticker for c in candidates if c.instrument_id not in windows],
        "seeded": sum(1 for c in candidates if c.resolution == "seeded"),
    }


# ---------------------------------------------------------------------------
# execucao real (Bloco 3+)
# ---------------------------------------------------------------------------


def _execute(
    label: str,
    candidates: list[BackfillCandidate],
    windows: dict[int, dict[str, Any]],
    as_of_date: date,
    out_path: Path,
    digest: str,
) -> dict[str, Any]:
    cfg = load_settings().get("price_backfill", {})
    rps = float(cfg.get("requests_per_second", 0.5))
    abort_after = int(cfg.get("consecutive_429_abort", 5))
    chunk = int(cfg.get("upsert_chunk", 1000))

    calendar = _trading_calendar()
    calendar_index = {d: i for i, d in enumerate(calendar)}
    company_end = _company_valid_to()
    fingerprint_owner = _seed_fingerprints()

    source = YFinancePriceSource()
    run_id = start_run(
        PIPELINE, provider=PROVIDER, params={"label": label, "as_of": str(as_of_date)}
    )
    bfr_id = _insert_run(label, out_path, digest, len(candidates), run_id, dry_run=False)

    totals = {
        "attempted": 0,
        "succeeded": 0,
        "empty_series": 0,
        "symbol_not_found": 0,
        "failed": 0,
        "skipped": 0,
        "rows_written": 0,
        "rows_out_of_window": 0,
        "critical_findings": 0,
    }
    per_instrument: list[dict[str, Any]] = []
    consecutive_429 = 0
    aborted = False

    for c in candidates:
        w = windows.get(c.instrument_id)
        window = _window_from_row(w)
        attempt = _base_attempt(bfr_id, c, w, window, as_of_date)

        # --- fora de escopo: nao chama a rede ---------------------------
        skip = _out_of_scope(window)
        if skip is not None:
            attempt.update(status="skipped_out_of_scope", quality_flag="ok", quality_reason=skip)
            totals["skipped"] += 1
            _upsert_attempts([attempt], columns=_ATTEMPT_UPDATE)
            per_instrument.append({"ticker": c.ticker, "status": "skipped_out_of_scope", "reason": skip})
            continue

        totals["attempted"] += 1
        symbol = ticker_to_yahoo_symbol(c.ticker)
        req_start = window.price_valid_from
        req_end = min(window.price_valid_to or as_of_date, as_of_date)
        assert req_start is not None

        throttle(PIPELINE, rps)
        try:
            fetched = source.fetch_daily_history(symbol, req_start, req_end + timedelta(days=1))
            consecutive_429 = 0
        except Exception as exc:
            msg = str(exc)
            if "429" in msg or "Too Many Requests" in msg:
                consecutive_429 += 1
            attempt.update(
                status="failed", attempt_count=1, quality_flag="missing_input",
                error_message=msg[:500],
            )
            totals["failed"] += 1
            _upsert_attempts([attempt], columns=_ATTEMPT_UPDATE)
            per_instrument.append({"ticker": c.ticker, "status": "failed", "error": msg[:160]})
            if consecutive_429 >= abort_after:
                aborted = True
                logger.error("circuit breaker: %d respostas 429 seguidas -- abortando lote", consecutive_429)
                break
            continue

        frame = fetched.frame
        raw_count = 0 if frame is None or frame.empty else len(frame)

        # --- serie vazia: sem retry, classifica -----------------------
        if raw_count == 0:
            kind, reason = _classify_empty(symbol)
            qflag = "provider_no_data_delisted" if (
                kind == "symbol_not_found" and c.lifecycle_valid_to is not None
            ) else "incomplete"
            findings = run_backfill_checks(
                ticker=c.ticker, resolution=c.resolution, raw_row_count=0,
                written_dates=[], written_closes=[], price_valid_from=window.price_valid_from,
                price_valid_to=window.price_valid_to, company_valid_to=company_end.get(c.company_id),
                calendar_index=calendar_index, calendar_max=(calendar[-1] if calendar else None),
                expected_trading_days_60=None,
                fingerprint=None, fingerprint_owner=fingerprint_owner,
            )
            attempt.update(
                status=kind, row_count=0, rows_written=0, quality_flag=qflag,
                quality_reason=reason, checks=summarize(findings),
            )
            totals[kind] += 1
            _bump_critical(totals, findings)
            _upsert_attempts([attempt], columns=_ATTEMPT_UPDATE)
            per_instrument.append({"ticker": c.ticker, "status": kind, "reason": reason})
            continue

        # --- serie com dados: normaliza, particiona, grava ------------
        raw_path = _save_raw(frame, c.ticker)
        price_rows = to_daily_price_rows(
            frame, instrument_id=c.instrument_id, source=SOURCE, source_symbol=symbol,
            currency="BRL", raw_file=str(raw_path.relative_to(project_root())), run_id=run_id,
        )
        raw_dates = sorted(r["trade_date"] for r in price_rows)
        inside, outside = partition_by_window(price_rows, window)
        inside.sort(key=lambda r: r["trade_date"])

        written = 0
        for part in _chunks(inside, chunk):
            written += upsert_many(
                "daily_prices", part,
                conflict_columns=["instrument_id", "trade_date", "source"],
            )["total"]
        action_rows = to_corporate_action_rows(frame, instrument_id=c.instrument_id, source=SOURCE, run_id=run_id)
        if action_rows:
            for part in _chunks(action_rows, chunk):
                upsert_many(
                    "corporate_actions", part,
                    conflict_columns=["instrument_id", "action_date", "action_type", "source", "value"],
                )

        w_dates = [r["trade_date"] for r in inside]
        w_closes = [r.get("close") for r in inside]
        fp = series_fingerprint(w_dates, [c for c in w_closes if c is not None])
        exp_60 = _expected_trading_days(calendar, window.price_valid_from, w_dates[-1] if w_dates else req_end, 60)
        findings = run_backfill_checks(
            ticker=c.ticker, resolution=c.resolution, raw_row_count=raw_count,
            written_dates=w_dates, written_closes=[x if x is not None else 0.0 for x in w_closes],
            price_valid_from=window.price_valid_from, price_valid_to=window.price_valid_to,
            company_valid_to=company_end.get(c.company_id), calendar_index=calendar_index,
            calendar_max=(calendar[-1] if calendar else None),
            expected_trading_days_60=exp_60, fingerprint=fp, fingerprint_owner=fingerprint_owner,
        )
        if fp is not None and fp not in fingerprint_owner:
            fingerprint_owner[fp] = c.ticker

        gap_count, max_gap = _internal_gaps(w_dates, calendar_index)
        attempt.update(
            status="resolved",
            provider_symbol=symbol,
            returned_first_date=raw_dates[0] if raw_dates else None,
            returned_last_date=raw_dates[-1] if raw_dates else None,
            row_count=raw_count,
            rows_written=written,
            rows_out_of_window=len(outside),
            expected_trading_days=exp_60,
            gap_count=gap_count,
            max_gap_days=max_gap,
            quality_flag="estimated" if window.from_precision == "year" else "ok",
            quality_reason=_reason_for(window, outside),
            checks=summarize(findings),
        )
        totals["succeeded"] += 1
        totals["rows_written"] += written
        totals["rows_out_of_window"] += len(outside)
        _bump_critical(totals, findings)
        _upsert_attempts([attempt], columns=_ATTEMPT_UPDATE)
        per_instrument.append(
            {
                "ticker": c.ticker, "status": "resolved", "raw": raw_count, "written": written,
                "out_of_window": len(outside),
                "written_range": [str(w_dates[0]), str(w_dates[-1])] if w_dates else None,
                "critical": [f.name for f in findings if f.severity == "CRITICAL"],
                "warn": [f.name for f in findings if f.severity == "WARN"],
            }
        )

    status = "aborted" if aborted else ("failed" if totals["failed"] and not totals["succeeded"] else "success")
    _finish_run_row(bfr_id, totals, status)
    finish_run(run_id, status="success" if status != "aborted" else "failed",
               records_inserted=totals["rows_written"])

    return {
        "status": status,
        "dry_run": False,
        "label": label,
        "backfill_run_id": bfr_id,
        "candidates": len(candidates),
        "totals": totals,
        "by_instrument": per_instrument,
        "critical_total": totals["critical_findings"],
        "aborted": aborted,
    }


# ---------------------------------------------------------------------------
# helpers de execucao
# ---------------------------------------------------------------------------


def _window_from_row(w: dict[str, Any] | None) -> PriceWindow:
    if not w:
        return PriceWindow(None, None, "unknown", "open", {})
    return PriceWindow(
        price_valid_from=_as_date(w.get("price_valid_from")),
        price_valid_to=_as_date(w.get("price_valid_to")),
        from_precision=w.get("from_precision", "unknown"),
        to_precision="open",
        basis={},
    )


def _out_of_scope(window: PriceWindow) -> str | None:
    today = date.today()
    if window.price_valid_from is None:
        return "sem instrument_price_window -- rode compute-price-windows"
    if window.price_valid_from > today:
        return f"price_valid_from {window.price_valid_from} no futuro"
    if window.price_valid_to is not None and window.price_valid_to <= window.price_valid_from:
        return (
            f"janela degenerada [{window.price_valid_from}, {window.price_valid_to}] "
            "-- intervalo de lifecycle colapsado; nada a baixar"
        )
    return None


def _base_attempt(
    bfr_id: int, c: BackfillCandidate, w: dict[str, Any] | None, window: PriceWindow, as_of: date
) -> dict[str, Any]:
    return {
        "backfill_run_id": bfr_id,
        "instrument_id": c.instrument_id,
        "ticker": c.ticker,
        "company_id": c.company_id,
        "provider": PROVIDER,
        "provider_symbol": ticker_to_yahoo_symbol(c.ticker),
        "lifecycle_valid_from": c.lifecycle_valid_from,
        "lifecycle_valid_to": c.lifecycle_valid_to,
        "price_window_from": window.price_valid_from,
        "price_window_to": window.price_valid_to,
        "price_window_precision": (w or {}).get("from_precision"),
        "requested_start": window.price_valid_from,
        "requested_end": min(window.price_valid_to or as_of, as_of),
        "attempt_count": 1,
        "row_count": 0,
        "rows_written": 0,
        "rows_out_of_window": 0,
        "gap_count": 0,
        "max_gap_days": 0,
    }


def _classify_empty(symbol: str) -> tuple[str, str]:
    """Discriminador MEDIDO (nao presumido) entre simbolo inexistente e serie
    vazia na janela. Faz uma sonda leve de 1 mes + metadata."""
    try:
        import yfinance as yf

        t = yf.Ticker(symbol)
        try:
            probe = t.history(period="1mo")
        except Exception:
            probe = None
        if probe is not None and not probe.empty:
            return "empty_series", "provedor tem o simbolo (sonda 1mo nao-vazia); nada na janela pedida"
        try:
            md = t.get_history_metadata()
        except Exception:
            md = None
        if md:
            return "empty_series", "simbolo valido (metadata presente), sem linhas na janela"
        return "symbol_not_found", "provedor nao reconhece o simbolo (sonda e metadata vazias)"
    except Exception as exc:
        return "symbol_not_found", f"probe falhou: {exc}"[:200]


def _reason_for(window: PriceWindow, outside: list[tuple[dict[str, Any], str]]) -> str | None:
    bits = []
    if window.from_precision == "year":
        bits.append("price_valid_from com precisao anual (source_reference_year_first)")
    before = sum(1 for _, r in outside if r == "ticker_identity_not_proven")
    after = sum(1 for _, r in outside if r == "after_ticker_window")
    if before:
        bits.append(f"{before} linha(s) do provedor antes da janela descartada(s) (ticker_identity_not_proven)")
    if after:
        bits.append(f"{after} linha(s) apos price_valid_to descartada(s)")
    return "; ".join(bits) or None


def _trading_calendar(exchange: str = "B3") -> list[date]:
    rows = fetch_all(
        "select trade_date from public.trading_calendar "
        "where exchange = %s and is_trading_day = true order by trading_day_index",
        [exchange],
    )
    return [_as_date(r["trade_date"]) for r in rows if _as_date(r["trade_date"]) is not None]  # type: ignore[misc]


def _company_valid_to() -> dict[int, date | None]:
    out: dict[int, date | None] = {}
    open_ended: set[int] = set()
    for r in fetch_all("select company_id, valid_to from public.company_lifecycle"):
        cid = int(r["company_id"])
        vt = _as_date(r["valid_to"])
        if vt is None:
            open_ended.add(cid)
        elif cid not in out or (out[cid] is not None and vt > out[cid]):  # type: ignore[operator]
            out[cid] = vt
    for cid in open_ended:
        out[cid] = None
    return out


def _seed_fingerprints() -> dict[str, str]:
    """Assinaturas das series JA em ``daily_prices`` -- para o check de serie
    duplicada pegar colisao contra a Fase 1 tambem."""
    owner: dict[str, str] = {}
    rows = fetch_all(
        "select distinct instrument_id from public.daily_prices"
    )
    for r in rows:
        iid = int(r["instrument_id"])
        series = fetch_all(
            "select p.trade_date, p.close, i.ticker from public.daily_prices p "
            "join public.instruments i on i.instrument_id = p.instrument_id "
            "where p.instrument_id = %s order by p.trade_date limit 100",
            [iid],
        )
        if not series:
            continue
        dates = [_as_date(s["trade_date"]) for s in series]
        closes = [float(s["close"]) for s in series if s["close"] is not None]
        fp = series_fingerprint([d for d in dates if d is not None], closes)
        if fp is not None:
            owner[fp] = str(series[0]["ticker"])
    return owner


def _expected_trading_days(
    calendar: list[date], start: date | None, end: date, window: int
) -> int | None:
    if start is None:
        return None
    lo = max(start, calendar[0]) if calendar else start
    in_range = [d for d in calendar if lo <= d <= end]
    return min(window, len(in_range)) if in_range else 0


def _internal_gaps(dates: list[date], calendar_index: dict[date, int]) -> tuple[int, int]:
    idxs = sorted(calendar_index[d] for d in dates if d in calendar_index)
    if len(idxs) < 2:
        return 0, 0
    gaps = [b - a - 1 for a, b in pairwise(idxs) if b - a > 1]
    return len(gaps), (max(gaps) if gaps else 0)


def _save_raw(frame: Any, ticker: str) -> Path:
    root = data_dir() / "raw" / "prices" / ticker.upper()
    root.mkdir(parents=True, exist_ok=True)
    payload = frame.to_parquet(index=False)
    digest = hashlib.sha256(payload).hexdigest()
    path = root / f"m21_{date.today().isoformat()}_{digest[:12]}.parquet"
    if not path.exists():
        path.write_bytes(payload)
    return path


def _bump_critical(totals: dict[str, int], findings: list[CheckFinding]) -> None:
    totals["critical_findings"] += sum(1 for f in findings if f.severity == "CRITICAL")


# ---------------------------------------------------------------------------
# ledger
# ---------------------------------------------------------------------------


def _insert_run(
    label: str, path: Path, digest: str, requested: int, run_id: int, *, dry_run: bool
) -> int:
    row = insert_returning(
        "price_backfill_runs",
        {
            "batch_label": label,
            "batch_file": str(path.relative_to(project_root())),
            "batch_file_sha256": digest,
            "provider": PROVIDER,
            "params": {"dry_run": dry_run},
            "dry_run": dry_run,
            "requested": requested,
            "status": "success" if dry_run else "running",
            "run_id": run_id,
        },
    )
    return int(row["backfill_run_id"])


def _finish_run_row(bfr_id: int, totals: dict[str, int], status: str) -> None:
    from stock_research.db import execute

    execute(
        "update public.price_backfill_runs set finished_at = now(), status = %s, "
        "attempted = %s, succeeded = %s, empty_series = %s, symbol_not_found = %s, "
        "failed = %s, rows_written = %s, rows_out_of_window = %s, critical_findings = %s "
        "where backfill_run_id = %s",
        [
            status, totals["attempted"], totals["succeeded"], totals["empty_series"],
            totals["symbol_not_found"], totals["failed"], totals["rows_written"],
            totals["rows_out_of_window"], totals["critical_findings"], bfr_id,
        ],
    )


def _upsert_attempts(rows: list[dict[str, Any]], *, columns: list[str]) -> None:
    for part in _chunks(rows, 500):
        upsert_many(
            "price_backfill_attempts",
            part,
            conflict_columns=["backfill_run_id", "instrument_id"],
            update_columns=columns,
        )


def _chunks(rows: list[Any], size: int) -> list[list[Any]]:
    return [rows[i : i + size] for i in range(0, len(rows), size)]
