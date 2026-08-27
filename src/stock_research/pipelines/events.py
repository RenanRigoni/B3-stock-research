"""``build-events``: agrupa noticias relevantes em eventos economicos
(fase1.md 38-41, 93-96, Milestone 9).

Um evento nao e um artigo -- e um FATO. Dezenas de materias sobre o mesmo
balanco viram UM evento (o cluster de dedup do Milestone 7 ja fez esse
trabalho); este pipeline promove cada cluster (ou artigo avulso fora de
cluster) com relevancia suficiente a uma linha de ``events``, calcula
``effective_trade_date`` (a parte critica, ``transforms/events.py``) e
marca sobreposicao entre eventos do mesmo instrumento no mesmo pregao
(fase1.md 93: balanco e troca de CEO no mesmo dia nao podem ter a reacao
atribuida a um so).

So noticias com relevancia >= media (``news.relevance.medium``) viram
evento -- promover ruido de baixa relevancia (fase1.md 36) contaminaria o
event study inteiro no Milestone 10. Artigo de baixa relevancia continua
gravado em ``news_company_links``, so nao produz ``events``.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from stock_research.config import load_settings
from stock_research.db import (
    execute,
    fetch_all,
    fetch_all_paginated,
    finish_run,
    start_run,
    upsert_many,
)
from stock_research.logging import get_logger
from stock_research.transforms.events import compute_effective_trade_date

logger = get_logger(__name__)

PIPELINE = "events"
CLUSTERING_VERSION = "event_clustering_v1"


class _DbCalendar:
    """``CalendarLookup`` (transforms/events.py) sobre o trading_calendar real."""

    def __init__(self, exchange: str) -> None:
        self._exchange = exchange
        rows = fetch_all(
            "select trade_date, is_trading_day, next_trading_day "
            "from public.trading_calendar where exchange = %s",
            [exchange],
        )
        self._by_date = {r["trade_date"]: r for r in rows}

    def is_trading_day(self, d: date) -> bool:
        row = self._by_date.get(d)
        return bool(row["is_trading_day"]) if row else False

    def next_trading_day(self, d: date) -> date | None:
        row = self._by_date.get(d)
        return row["next_trading_day"] if row else None


def build_events(ticker: str) -> dict[str, Any]:
    instrument = _get_instrument(ticker)
    run_id = start_run(PIPELINE, ticker=ticker)
    try:
        stats = _build_events_for_instrument(instrument["instrument_id"], instrument["exchange"])
        finish_run(run_id, status="success", records_raw=stats["candidates"], records_inserted=stats["events"])
        return {"status": "success", **stats}
    except Exception as exc:
        logger.error("build-events falhou para %s: %s", ticker, exc)
        finish_run(run_id, status="failed", error_message=str(exc))
        return {"status": "failed", "error": str(exc)}


def _build_events_for_instrument(instrument_id: int, exchange: str) -> dict[str, int]:
    settings = load_settings()
    min_relevance = float(settings["news"]["relevance"]["medium"])
    market_open = _parse_hhmm(settings["events"]["market_open_local"])
    market_close = _parse_hhmm(settings["events"]["market_close_local"])

    candidates = _event_candidates(instrument_id, min_relevance)
    if not candidates:
        return {"candidates": 0, "events": 0, "confounded": 0}
    logger.info("build-events: %d candidato(s) de evento", len(candidates))

    calendar = _DbCalendar(exchange)
    event_rows = []
    article_links: list[tuple[str, list[int], int]] = []  # (source_id, article_ids, primary_article_id)

    for candidate in candidates:
        result = compute_effective_trade_date(
            event_date=candidate["published_at_utc"].date() if candidate["published_at_utc"] else candidate["fallback_date"],
            event_time_local=candidate["published_at_utc"],
            time_precision=candidate["time_precision"] or "unknown",
            calendar=calendar,
            market_open_local=market_open,
            market_close_local=market_close,
        )
        row = {
            "instrument_id": instrument_id,
            "event_type": candidate["category"] or "other",
            "event_subtype": None,
            "event_title": candidate["title"],
            "event_time_utc": candidate["published_at_utc"],
            "event_time_local": None,
            "event_date": candidate["published_at_utc"].date() if candidate["published_at_utc"] else candidate["fallback_date"],
            "effective_trade_date": result.effective_trade_date,
            "time_precision": candidate["time_precision"] or "unknown",
            "market_session_uncertain": result.market_session_uncertain,
            "scope": candidate["scope"] or "unknown",
            "source_type": "news",
            "source_id": candidate["source_id"],
            "relevance_score": candidate["relevance_score"],
            "sentiment": candidate["sentiment"],
            "confidence": candidate["relevance_score"],
            "news_explanation_status": "resolved",
            "clustering_version": CLUSTERING_VERSION,
        }
        event_rows.append(row)
        article_links.append((candidate["source_id"], candidate["article_ids"], candidate["primary_article_id"]))

    stats = upsert_many(
        "events", event_rows,
        conflict_columns=["instrument_id", "source_type", "source_id", "clustering_version"],
        update_columns=[
            "event_type", "event_title", "event_time_utc", "event_date", "effective_trade_date",
            "time_precision", "market_session_uncertain", "scope", "relevance_score", "sentiment", "confidence",
        ],
    )
    logger.info("build-events: %d evento(s) gravado(s)", stats["total"])

    # Um upsert_many so pra todos os vinculos, nao um por evento: com milhares
    # de eventos (PETR4 na Fase 1.1) um round-trip HTTP por evento so pra
    # gravar event_articles virou o mesmo gargalo ja corrigido em
    # analyze-news (_count_unique_domains por cluster) -- mesma causa raiz,
    # lugar diferente.
    event_ids = _event_ids_by_source(instrument_id, [a[0] for a in article_links])
    event_article_rows = [
        {"event_id": event_id, "article_id": aid, "relationship": "reports", "is_primary": aid == primary_article_id}
        for source_id, article_ids, primary_article_id in article_links
        if (event_id := event_ids.get(source_id)) is not None
        for aid in article_ids
    ]
    upsert_many(
        "event_articles", event_article_rows, conflict_columns=["event_id", "article_id"],
        update_columns=["relationship", "is_primary"],
    )
    logger.info("build-events: %d vinculo(s) evento-artigo gravado(s)", len(event_article_rows))

    confounded = _mark_confounded_events(instrument_id)
    logger.info("build-events: %d evento(s) confundido(s) marcado(s)", confounded)

    return {"candidates": len(candidates), "events": stats["total"], "confounded": confounded}


def _event_candidates(instrument_id: int, min_relevance: float) -> list[dict[str, Any]]:
    """Um candidato por cluster (canonico representa o grupo) + artigos
    relevantes fora de qualquer cluster. ``source_id`` e a chave natural do
    evento: ``cluster:<id>`` ou ``article:<id>``, estavel entre execucoes."""
    rows = fetch_all_paginated(
        "select a.article_id, a.title, a.published_at_utc, a.time_precision, "
        "       a.duplicate_cluster_id, "
        "       n.category, n.sentiment, "
        "       l.relevance_score "
        "from public.news_articles a "
        "join public.news_company_links l using (article_id) "
        "left join public.news_analysis n on n.article_id = a.article_id and n.instrument_id = l.instrument_id "
        "where l.instrument_id = %s and l.relevance_score >= %s and a.article_id > %s "
        "order by a.article_id "
        "limit %s",
        [instrument_id, min_relevance],
    )
    if not rows:
        return []

    by_cluster: dict[int, list[dict[str, Any]]] = {}
    standalone: list[dict[str, Any]] = []
    for row in rows:
        cluster_id = row.get("duplicate_cluster_id")
        if cluster_id is None:
            standalone.append(row)
        else:
            by_cluster.setdefault(cluster_id, []).append(row)

    candidates = []
    for cluster_id, members in by_cluster.items():
        primary = max(members, key=lambda r: (r["relevance_score"] or 0, r["article_id"]))
        candidates.append(_to_candidate(primary, source_id=f"cluster:{cluster_id}", article_ids=[m["article_id"] for m in members]))
    for row in standalone:
        candidates.append(_to_candidate(row, source_id=f"article:{row['article_id']}", article_ids=[row["article_id"]]))

    return candidates


def _to_candidate(row: dict[str, Any], *, source_id: str, article_ids: list[int]) -> dict[str, Any]:
    published = row.get("published_at_utc")
    return {
        "source_id": source_id,
        "article_ids": article_ids,
        "primary_article_id": row["article_id"],
        "title": row["title"],
        "published_at_utc": published,
        "fallback_date": published.date() if published else date.today(),
        "time_precision": row.get("time_precision"),
        "category": row.get("category"),
        "scope": _scope_for_category(row.get("category")),
        "sentiment": row.get("sentiment"),
        "relevance_score": row.get("relevance_score"),
    }


def _scope_for_category(category: str | None) -> str:
    if not category:
        return "unknown"
    from stock_research.config import load_taxonomy

    categories = load_taxonomy().get("categories") or {}
    return categories.get(category, {}).get("scope", "unknown")


def _event_ids_by_source(instrument_id: int, source_ids: list[str]) -> dict[str, int]:
    if not source_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(source_ids))
    rows = fetch_all(
        f"select event_id, source_id from public.events "
        f"where instrument_id = %s and source_type = 'news' and clustering_version = %s "
        f"and source_id in ({placeholders})",
        [instrument_id, CLUSTERING_VERSION, *source_ids],
    )
    return {r["source_id"]: r["event_id"] for r in rows}


def _mark_confounded_events(instrument_id: int) -> int:
    """fase1.md 93: dois eventos do mesmo instrumento no mesmo
    ``effective_trade_date`` contaminam a leitura um do outro -- marca os
    dois, nao so o segundo."""
    rows = fetch_all(
        "select event_id, effective_trade_date, "
        "       count(*) over (partition by effective_trade_date) as n "
        "from public.events "
        "where instrument_id = %s and effective_trade_date is not null",
        [instrument_id],
    )
    confounded = 0
    updates: list[tuple[int, int, bool]] = []
    for row in rows:
        overlap = int(row["n"]) - 1
        updates.append((row["event_id"], overlap, overlap > 0))
        if overlap > 0:
            confounded += 1
    _apply_confounded_flags(updates)
    return confounded


_CONFOUNDED_BATCH_SIZE = 1000


def _apply_confounded_flags(updates: list[tuple[int, int, bool]]) -> None:
    """``updates``: ``(event_id, overlapping_event_count, is_confounded)``. Um
    UPDATE por lote via ``VALUES``, nao um por evento -- mesmo gargalo ja
    corrigido em analyze-news (round-trip HTTP por item em vez de lote)."""
    for start in range(0, len(updates), _CONFOUNDED_BATCH_SIZE):
        chunk = updates[start : start + _CONFOUNDED_BATCH_SIZE]
        values_sql = ", ".join("(%s::bigint, %s::int, %s::boolean)" for _ in chunk)
        params = [value for row in chunk for value in row]
        execute(
            "update public.events as t "
            "set overlapping_event_count = v.overlap, is_confounded = v.confounded "
            f"from (values {values_sql}) as v(event_id, overlap, confounded) "
            "where t.event_id = v.event_id",
            params,
        )


def _parse_hhmm(text: str) -> Any:
    from datetime import time as time_cls

    hour, minute = (int(p) for p in text.split(":"))
    return time_cls(hour, minute)


def _get_instrument(ticker: str) -> dict[str, Any]:
    rows = fetch_all(
        "select instrument_id, exchange from public.instruments where ticker = %s", [ticker.upper()]
    )
    if not rows:
        raise ValueError(f"instrumento nao cadastrado: {ticker} (rode `stock-research init`)")
    return rows[0]
