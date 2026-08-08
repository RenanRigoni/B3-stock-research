"""Pipeline de coleta de noticias via GDELT DOC API (fase1.md 23-31).

Contrato de sempre (mesma forma de ``pipelines/prices.py`` e
``pipelines/fundamentals.py``): ``start_run``/``finish_run`` sempre, erro
numa empresa nunca aborta as outras (fase1.md 104), upsert idempotente por
chave natural (``provider``, ``url_hash``).

Janela de data (fase1.md 25 -- "permitir paginacao/janelas de data"): o modo
ArtList do GDELT nao tem cursor de paginacao real e tem teto de 250 artigos
por chamada. Para uma janela grande, o backfill fatia em blocos semanais --
mais chamadas, mas cada uma tem chance real de nao estourar o teto.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from stock_research.config import load_settings
from stock_research.db import fetch_all, finish_run, record_finding, start_run, upsert_many
from stock_research.logging import get_logger
from stock_research.sources.news.gdelt_doc import GdeltRateLimitError, fetch_articles
from stock_research.sources.news.query_builder import build_company_query
from stock_research.transforms.news import build_article_row

logger = get_logger(__name__)

PIPELINE = "news"
CHUNK_DAYS = 7

ARTICLE_UPDATE_COLUMNS = [
    "domain", "source_name", "title", "title_normalized", "title_hash", "language",
    "country", "source_country", "published_at_utc", "time_precision", "seen_at",
    "image_url", "query_used", "raw_file", "run_id",
]


def sync_news(
    *,
    ticker: str,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """Coleta noticias de uma empresa numa janela de datas."""
    instrument = _get_instrument(ticker)
    aliases = _strong_aliases(instrument["instrument_id"])
    if not aliases:
        raise ValueError(
            f"{ticker} nao tem aliases fortes cadastrados (rode `stock-research init`)"
        )
    query = build_company_query(aliases)

    end_date = end or date.today()
    start_date = start or (end_date - timedelta(days=30))
    if start_date > end_date:
        raise ValueError(f"start ({start_date}) posterior a end ({end_date})")

    run_id = start_run(
        PIPELINE, provider="gdelt", ticker=ticker,
        params={"query": query, "start": str(start_date), "end": str(end_date)},
    )
    try:
        stats = _fetch_and_store_window(
            instrument_id=instrument["instrument_id"], query=query,
            start_date=start_date, end_date=end_date, run_id=run_id,
        )
        finish_run(
            run_id, status="success", records_raw=stats["fetched"],
            records_inserted=stats["inserted"], records_updated=stats["updated"],
        )
        return {"status": "success", "query": query, **stats}
    except Exception as exc:
        logger.error("sync-news falhou para %s: %s", ticker, exc)
        finish_run(run_id, status="failed", error_message=str(exc))
        return {"status": "failed", "error": str(exc), "query": query}


def _fetch_and_store_window(
    *, instrument_id: int, query: str, start_date: date, end_date: date, run_id: int
) -> dict[str, int]:
    fetched = inserted = updated = links = 0
    rate_limited_chunks = 0

    for chunk_start, chunk_end in _weekly_chunks(start_date, end_date):
        try:
            response = fetch_articles(
                query,
                start=datetime.combine(chunk_start, time.min, tzinfo=UTC),
                end=datetime.combine(chunk_end, time.max, tzinfo=UTC),
            )
        except GdeltRateLimitError as exc:
            # Uma janela rate-limited nao pode derrubar o backfill inteiro --
            # registra e segue pras proximas (fase1.md 104).
            rate_limited_chunks += 1
            record_finding(
                run_id, PIPELINE, "gdelt_rate_limit", "WARNING",
                f"janela {chunk_start}..{chunk_end} nao coletada apos retries: {exc}",
                entity_type="news_window", entity_id=f"{chunk_start}:{chunk_end}",
            )
            continue

        fetched += len(response.articles)
        if not response.articles:
            continue
        if len(response.articles) >= 250:
            record_finding(
                run_id, PIPELINE, "gdelt_possible_truncation", "INFO",
                f"janela {chunk_start}..{chunk_end} retornou o teto de 250 artigos -- "
                "pode haver mais noticias nao coletadas nesse periodo",
                entity_type="news_window", entity_id=f"{chunk_start}:{chunk_end}",
            )

        rows = _build_rows(response.articles, query_used=query, raw_file=str(response.raw_path), run_id=run_id)
        if not rows:
            continue
        stats = upsert_many(
            "news_articles", rows, conflict_columns=["provider", "url_hash"],
            update_columns=ARTICLE_UPDATE_COLUMNS,
        )
        inserted += stats["inserted"]
        updated += stats["updated"]
        links += _link_articles_to_company(rows, instrument_id=instrument_id)

    if rate_limited_chunks:
        logger.warning(
            "%d janela(s) de %d nao foram coletadas por rate limit persistente do GDELT",
            rate_limited_chunks, _count_chunks(start_date, end_date),
        )
    return {"fetched": fetched, "inserted": inserted, "updated": updated, "links": links}


def _build_rows(
    articles: list[dict[str, Any]], *, query_used: str, raw_file: str, run_id: int
) -> list[dict[str, Any]]:
    rows = []
    for article in articles:
        row = build_article_row(article, query_used=query_used, raw_file=raw_file, run_id=run_id)
        if row is not None:
            rows.append(row)
    return rows


def _link_articles_to_company(rows: list[dict[str, Any]], *, instrument_id: int) -> int:
    """Liga cada artigo coletado a empresa que gerou a busca.

    ``match_method='query'``: o artigo apareceu porque a query dessa empresa
    o trouxe -- e um sinal de relevancia fraco (a query pode casar por
    contexto amplo), por isso ``review_status='pending_review'`` e
    ``relevance_score`` fica para o classificador do Milestone 8 calcular de
    verdade a partir do titulo. Nao inventar um score aqui (fase1.md 123).
    """
    rows_with_url = [r for r in rows if r.get("canonical_url") or r.get("url_hash")]
    if not rows_with_url:
        return 0
    url_hashes = [r["url_hash"] for r in rows_with_url]
    article_ids = _article_ids_by_url_hash(url_hashes)

    link_rows = [
        {
            "article_id": article_ids[r["url_hash"]],
            "instrument_id": instrument_id,
            "match_method": "query",
            "relevance_score": None,
            "match_terms": None,
            "is_primary_company": True,
            "review_status": "pending_review",
        }
        for r in rows_with_url
        if r["url_hash"] in article_ids
    ]
    if not link_rows:
        return 0
    stats = upsert_many(
        "news_company_links", link_rows, conflict_columns=["article_id", "instrument_id"],
        update_columns=[],  # nao sobrescrever relevancia/revisao ja calculada por outro milestone
    )
    return stats["total"]


def _article_ids_by_url_hash(url_hashes: list[str]) -> dict[str, int]:
    if not url_hashes:
        return {}
    placeholders = ", ".join(["%s"] * len(url_hashes))
    rows = fetch_all(
        f"select article_id, url_hash from public.news_articles where url_hash in ({placeholders})",
        url_hashes,
    )
    return {r["url_hash"]: r["article_id"] for r in rows}


def _weekly_chunks(start_date: date, end_date: date) -> list[tuple[date, date]]:
    chunks = []
    cursor = start_date
    while cursor <= end_date:
        chunk_end = min(cursor + timedelta(days=CHUNK_DAYS - 1), end_date)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


def _count_chunks(start_date: date, end_date: date) -> int:
    return len(_weekly_chunks(start_date, end_date))


def _get_instrument(ticker: str) -> dict[str, Any]:
    row = fetch_all(
        "select instrument_id, ticker from public.instruments where ticker = %s", [ticker.upper()]
    )
    if not row:
        raise ValueError(f"instrumento nao cadastrado: {ticker} (rode `stock-research init`)")
    return row[0]


def _strong_aliases(instrument_id: int) -> list[str]:
    rows = fetch_all(
        "select alias from public.company_aliases where instrument_id = %s and is_strong = true "
        "order by alias_kind",
        [instrument_id],
    )
    return [r["alias"] for r in rows]


def _list_sync_targets() -> list[str]:
    rows = fetch_all(
        "select ticker from public.instruments where active = true and is_benchmark = false order by ticker"
    )
    return [r["ticker"] for r in rows]


def sync_news_all(*, start: date | None = None, end: date | None = None) -> dict[str, Any]:
    """Roda ``sync_news`` para todo o universo (exceto benchmark, que nao tem
    noticias associadas -- IBOV nao e uma empresa)."""
    settings = load_settings()  # valida config carregavel antes de gastar chamadas
    del settings
    results = {t: sync_news(ticker=t, start=start, end=end) for t in _list_sync_targets()}
    return {"results": results, "failed": [t for t, r in results.items() if r.get("status") == "failed"]}
