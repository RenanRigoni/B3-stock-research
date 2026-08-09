"""Pipeline de precos (fase1.md 18-19): buscar -> salvar bruto -> normalizar ->
validar -> upsert -> registrar run.

Erro em um ticker nunca aborta os outros (fase1.md 104): ``sync_prices`` e
``update_prices`` capturam por ticker e devolvem quem falhou.
"""

from __future__ import annotations

import hashlib
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

from stock_research.analytics.returns import compute_and_store_returns
from stock_research.config import data_dir, load_settings, project_root
from stock_research.db import fetch_all, fetch_one, finish_run, insert_many, start_run, upsert_many
from stock_research.logging import get_logger
from stock_research.pipelines.calendar import rebuild_trading_calendar, trading_day_offset
from stock_research.quality.checks import record_findings, run_all_price_checks
from stock_research.sources.prices.yfinance_source import YFinancePriceSource
from stock_research.transforms.prices import to_corporate_action_rows, to_daily_price_rows

logger = get_logger(__name__)

PIPELINE = "prices"
_TRACKED_CHANGE_FIELDS = ("close", "adj_close", "volume")


# ---------------------------------------------------------------------------
# Backfill (fase1.md 18)
# ---------------------------------------------------------------------------


def sync_prices(
    *,
    ticker: str | None = None,
    all_tickers: bool = False,
    start: str | None = None,
    end: str | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """Backfill de precos. ``force`` fica reservado para quando existir cache de
    request -- hoje toda chamada busca a fonte de novo, entao nao ha o que ignorar.
    """
    settings = load_settings()
    tickers = _list_sync_targets() if all_tickers else [ticker.upper()]  # type: ignore[union-attr]

    start_date = date.fromisoformat(start) if start else date.fromisoformat(settings["prices"]["default_start"])
    end_date = date.fromisoformat(end) if end else date.today()

    results = {t: _run_sync_one(t, start_date, end_date, force=force) for t in tickers}
    return {"results": results, "failed": [t for t, r in results.items() if r.get("status") == "failed"]}


def _run_sync_one(ticker: str, start_date: date, end_date: date, *, force: bool) -> dict[str, Any]:
    try:
        return _sync_one(ticker, start_date, end_date, force=force)
    except Exception as exc:
        logger.error("sync-prices falhou para %s: %s", ticker, exc)
        return {"status": "failed", "error": str(exc)}


def _sync_one(ticker: str, start_date: date, end_date: date, *, force: bool) -> dict[str, Any]:
    instrument = _get_instrument(ticker)
    source = YFinancePriceSource()
    run_id = start_run(
        PIPELINE, provider=source.name, ticker=ticker,
        params={"start": str(start_date), "end": str(end_date), "force": force},
    )
    try:
        fetched = source.fetch_daily_history(instrument["yahoo_symbol"], start_date, end_date + timedelta(days=1))
        if fetched.frame.empty:
            logger.warning("sem dados retornados para %s entre %s e %s", ticker, start_date, end_date)
            finish_run(run_id, status="success", records_raw=0)
            return {"status": "success", "rows": 0}

        raw_path, digest = _save_raw_file(fetched.frame, ticker, source.name)
        _register_raw_file(raw_path, digest, source.name, instrument["yahoo_symbol"], start_date, end_date, run_id)

        stats = _normalize_and_write(instrument, source.name, fetched.frame, raw_path, run_id, settings=load_settings())
        if instrument["is_benchmark"]:
            rebuild_trading_calendar(instrument["exchange"])
        returns_stats = compute_and_store_returns(ticker, run_id=run_id)

        finish_run(
            run_id, status="success", records_raw=stats["rows"],
            records_inserted=stats["inserted"], records_updated=stats["updated"],
        )
        return {"status": "success", **stats, "returns_rows": returns_stats.get("rows", 0)}
    except Exception as exc:
        finish_run(run_id, status="failed", error_message=str(exc))
        raise


def _normalize_and_write(
    instrument: dict[str, Any], source_name: str, frame: pd.DataFrame, raw_path: Path,
    run_id: int, *, settings: dict[str, Any],
) -> dict[str, Any]:
    price_rows = to_daily_price_rows(
        frame, instrument_id=instrument["instrument_id"], source=source_name,
        source_symbol=instrument["yahoo_symbol"], currency=instrument["currency"] or "BRL",
        raw_file=str(raw_path.relative_to(project_root())), run_id=run_id,
    )
    action_rows = to_corporate_action_rows(
        frame, instrument_id=instrument["instrument_id"], source=source_name, run_id=run_id
    )

    changes = _detect_data_changes(instrument["instrument_id"], source_name, price_rows)
    if changes:
        insert_many("data_changes", [{**c, "run_id": run_id} for c in changes])
        logger.warning("%d valor(es) historico(s) corrigido(s) pelo provedor para %s", len(changes), source_name)

    findings = run_all_price_checks(
        price_rows,
        extreme_return_threshold=settings["quality"]["extreme_return_threshold"],
        max_gap_trading_days=settings["quality"]["max_gap_trading_days"],
        expected_currency=instrument["currency"] or "BRL",
    )
    record_findings(run_id, PIPELINE, instrument["instrument_id"], findings)

    price_stats = upsert_many("daily_prices", price_rows, conflict_columns=["instrument_id", "trade_date", "source"])
    action_stats = (
        upsert_many("corporate_actions", action_rows,
                    conflict_columns=["instrument_id", "action_date", "action_type", "source", "value"])
        if action_rows else {"inserted": 0, "updated": 0, "total": 0}
    )
    return {
        "rows": len(price_rows),
        "actions": len(action_rows),
        "data_changes": len(changes),
        "quality_findings": len(findings),
        "inserted": price_stats["inserted"] + action_stats["inserted"],
        "updated": price_stats["updated"] + action_stats["updated"],
    }


# ---------------------------------------------------------------------------
# Atualizacao incremental (fase1.md 19)
# ---------------------------------------------------------------------------


def update_prices(*, ticker: str | None = None) -> dict[str, Any]:
    """So busca a janela nova + sobreposicao de ``prices.incremental_overlap_days`` pregoes."""
    settings = load_settings()
    overlap_days = int(settings["prices"]["incremental_overlap_days"])
    tickers = _list_sync_targets() if ticker is None else [ticker.upper()]

    results = {t: _run_update_one(t, overlap_days) for t in tickers}
    return {"results": results, "failed": [t for t, r in results.items() if r.get("status") == "failed"]}


def _run_update_one(ticker: str, overlap_days: int) -> dict[str, Any]:
    try:
        return _update_one(ticker, overlap_days)
    except Exception as exc:
        logger.error("update-prices falhou para %s: %s", ticker, exc)
        return {"status": "failed", "error": str(exc)}


def _update_one(ticker: str, overlap_days: int) -> dict[str, Any]:
    instrument = _get_instrument(ticker)
    settings = load_settings()
    provider = settings["prices"]["primary_provider"]

    last = fetch_one(
        "select max(trade_date) as last_date from public.daily_prices where instrument_id = %s and source = %s",
        [instrument["instrument_id"], provider],
    )
    last_date = last["last_date"] if last else None
    if last_date is None:
        start_date = date.fromisoformat(settings["prices"]["default_start"])
        logger.info("%s sem historico: fazendo backfill completo desde %s", ticker, start_date)
    else:
        start_date = _incremental_start(instrument["exchange"], last_date, overlap_days)

    return _sync_one(ticker, start_date, date.today(), force=False)


def _incremental_start(exchange: str, last_date: date, overlap_days: int) -> date:
    shifted = trading_day_offset(exchange, last_date, -overlap_days)
    if shifted is not None:
        return shifted
    # Calendario ainda nao construido (ex.: primeira execucao de update-prices
    # antes do benchmark ter sido sincronizado): aproximacao conservadora em
    # dias corridos, generosa o bastante para cobrir a maioria dos feriados.
    logger.warning(
        "trading_calendar sem cobertura para %s: aproximando sobreposicao de %d pregoes em dias corridos",
        last_date, overlap_days,
    )
    return last_date - timedelta(days=int(overlap_days * 1.6) + 1)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_instrument(ticker: str) -> dict[str, Any]:
    row = fetch_one(
        "select instrument_id, ticker, yahoo_symbol, exchange, currency, is_benchmark "
        "from public.instruments where ticker = %s",
        [ticker.upper()],
    )
    if row is None:
        raise ValueError(f"instrumento nao cadastrado: {ticker} (rode `stock-research init`)")
    if not row.get("yahoo_symbol"):
        raise ValueError(f"{ticker} nao tem yahoo_symbol em config/companies.yaml")
    return row


def _list_sync_targets() -> list[str]:
    rows = fetch_all("select ticker from public.instruments where active = true order by is_benchmark desc, ticker")
    return [r["ticker"] for r in rows]


def _save_raw_file(frame: pd.DataFrame, ticker: str, source: str) -> tuple[Path, str]:
    """Grava o dataframe bruto (ja achatado pela fonte) em parquet."""
    raw_root = data_dir() / "raw" / "prices" / ticker.upper()
    raw_root.mkdir(parents=True, exist_ok=True)
    payload = frame.to_parquet(index=False)
    digest = hashlib.sha256(payload).hexdigest()
    path = raw_root / f"{date.today().isoformat()}_{digest[:12]}.parquet"
    if not path.exists():
        path.write_bytes(payload)
    return path, digest


def _register_raw_file(
    path: Path, digest: str, provider: str, symbol: str, start: date, end: date, run_id: int
) -> None:
    upsert_many(
        "raw_files",
        [{
            "file_path": str(path.relative_to(project_root())),
            "sha256": digest,
            "provider": provider,
            "source_url": f"{provider}://{symbol}?start={start}&end={end}",
            "content_type": "application/parquet",
            "bytes": path.stat().st_size,
            "run_id": run_id,
        }],
        conflict_columns=["file_path", "sha256"],
        update_columns=[],  # mesmo arquivo com mesmo hash: nada a atualizar
    )


def _detect_data_changes(instrument_id: int, source: str, new_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compara valores recem-buscados com o que ja estava gravado (fase1.md 19, 99).

    So gera linha quando o provedor corrigiu um valor ja existente -- rodar o
    mesmo sync duas vezes sem mudanca na fonte nao produz nada aqui, porque o
    "antigo" ja seria igual ao "novo".
    """
    if not new_rows:
        return []
    dates = [r["trade_date"] for r in new_rows]
    existing = {
        r["trade_date"]: r
        for r in fetch_all(
            "select trade_date, close, adj_close, volume from public.daily_prices "
            "where instrument_id = %s and source = %s and trade_date between %s and %s",
            [instrument_id, source, min(dates), max(dates)],
        )
    }
    changes: list[dict[str, Any]] = []
    for row in new_rows:
        old = existing.get(row["trade_date"])
        if old is None:
            continue
        for field_name in _TRACKED_CHANGE_FIELDS:
            old_value, new_value = old.get(field_name), row.get(field_name)
            if _differs(old_value, new_value, relative=field_name == "adj_close"):
                changes.append({
                    "table_name": "daily_prices",
                    "entity_key": f"{instrument_id}:{row['trade_date']}",
                    "field_name": field_name,
                    "old_value": str(old_value),
                    "new_value": str(new_value),
                    "provider": source,
                    "reason": "provider_correction",
                })
    return changes


# ``adj_close`` e recalculado pelo Yahoo a cada chamada encadeando fatores de
# proventos/splits sobre toda a serie -- ruido de ponto flutuante da ordem de
# 1e-5 relativo aparece entre duas buscas identicas sem nenhuma correcao real
# ter ocorrido (observado na Fase 1.1 ao rebackfillar 2010-2026: 3367/4124
# linhas do PETR4 "mudaram" adj_close por ~0.0015% cada, todas ruido). Um
# limiar absoluto de 1e-6 tambem escala mal entre uma acao de R$ 1 e uma de
# R$ 100. ``close``/``volume`` sao valores brutos, sem essa recomputacao --
# ficam com o limiar absoluto apertado de sempre.
_ADJ_CLOSE_RELATIVE_TOLERANCE = 1e-4


def _differs(old: Any, new: Any, *, relative: bool = False) -> bool:
    if old is None or new is None:
        return old is not new
    old_f, new_f = float(old), float(new)
    if not relative:
        return abs(old_f - new_f) > 1e-6
    denom = max(abs(old_f), abs(new_f), 1e-9)
    return abs(old_f - new_f) / denom > _ADJ_CLOSE_RELATIVE_TOLERANCE
