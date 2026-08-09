"""Pipeline de coleta de noticias via GDELT DOC API (fase1.md 23-31; fase1.1
11-22 -- checkpoint persistente, status tipado por janela, multi-idioma).

Contrato de sempre (mesma forma de ``pipelines/prices.py`` e
``pipelines/fundamentals.py``): ``start_run``/``finish_run`` sempre, erro
numa empresa nunca aborta as outras (fase1.md 104), upsert idempotente por
chave natural (``provider``, ``url_hash``).

Janela de data (fase1.md 25 -- "permitir paginacao/janelas de data"): o modo
ArtList do GDELT nao tem cursor de paginacao real e tem teto de 250 artigos
por chamada. Para uma janela grande, o backfill fatia em blocos semanais --
mais chamadas, mas cada uma tem chance real de nao estourar o teto.

Checkpoint (fase1.1 12-14): cada janela x idioma tentada grava uma linha em
``news_backfill_checkpoints`` com um status tipado -- nunca so "0 artigos".
Uma falha (rate limit, timeout, erro HTTP, parse) nunca vira silenciosamente
um "sucesso vazio": so ``success_empty`` significa "perguntamos e a fonte
respondeu que nao ha nada". Isso permite retomar um backfill interrompido
sem reprocessar (e sem se enganar sobre) janelas ja resolvidas.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from stock_research.config import load_settings
from stock_research.db import (
    fetch_all,
    fetch_one,
    finish_run,
    record_finding,
    start_run,
    upsert_many,
)
from stock_research.logging import get_logger
from stock_research.sources.news.gdelt_doc import (
    GdeltHttpError,
    GdeltParseError,
    GdeltRateLimitError,
    GdeltTimeoutError,
    GdeltUnsupportedDateRangeError,
    fetch_articles,
)
from stock_research.sources.news.query_builder import build_company_query
from stock_research.transforms.news import build_article_row

logger = get_logger(__name__)

PIPELINE = "news"
CHUNK_DAYS = 7

# Janelas com esses status ja foram respondidas pela fonte de forma definitiva
# -- nunca reprocessar num resume (fase1.1 14: "nunca reiniciar anos de
# coleta"). ``unsupported_date_range`` entra aqui (nao em FAILURE_STATUSES)
# porque retry nunca muda esse resultado: a API rejeita a data, nao o pedido.
TERMINAL_STATUSES = {"success_with_results", "success_empty", "unsupported_date_range"}
# Falhas retomaveis: o proximo backfill tenta de novo, respeitando backoff.
FAILURE_STATUSES = {"rate_limited", "timeout", "http_error", "parse_error"}

# Cobertura historica real do GDELT DOC 2.0, nao o que fase1.1 §22 pedia
# (2015): confirmado empiricamente nesta fase que a API rejeita janelas de
# 2015 com "Invalid query start date" (HTTP 200, nao rate limit -- ver
# GdeltUnsupportedDateRangeError). O inicio documentado publicamente da
# cobertura da DOC API e 2017-01-01; usar isso como corte evita gastar
# chamadas (e orcamento de rate limit) em janelas que a fonte ja provou que
# vai recusar. Se uma janela pos-corte ainda for rejeitada, o tratamento
# reativo em ``_fetch_one_window`` cobre isso de qualquer forma.
MIN_SUPPORTED_START = date(2017, 1, 1)

# Backoff entre novas tentativas da MESMA janela, por numero de tentativas ja
# feitas (fase1.1 15: "nao tentar vencer o GDELT por forca bruta"). Teto de 6h
# pra um backfill de varios dias eventualmente convergir sem martelar a fonte.
_RETRY_BACKOFF_SECONDS = [60, 300, 1800, 3600, 21600]

ARTICLE_UPDATE_COLUMNS = [
    "domain", "source_name", "title", "title_normalized", "title_hash", "language",
    "country", "source_country", "published_at_utc", "time_precision", "seen_at",
    "image_url", "query_used", "raw_file", "run_id",
]

_CHECKPOINT_CONFLICT_COLUMNS = ["provider", "instrument_id", "language", "window_start", "window_end"]


def sync_news(
    *,
    ticker: str,
    start: date | None = None,
    end: date | None = None,
) -> dict[str, Any]:
    """Coleta noticias de uma empresa numa janela de datas, em todos os
    idiomas configurados (fase1.1 18), retomando janelas ja resolvidas."""
    instrument = _get_instrument(ticker)
    aliases = _strong_aliases(instrument["instrument_id"])
    if not aliases:
        raise ValueError(
            f"{ticker} nao tem aliases fortes cadastrados (rode `stock-research init`)"
        )
    languages = _languages()

    end_date = end or date.today()
    start_date = start or (end_date - timedelta(days=30))
    if start_date > end_date:
        raise ValueError(f"start ({start_date}) posterior a end ({end_date})")

    run_id = start_run(
        PIPELINE, provider="gdelt", ticker=ticker,
        params={"languages": languages, "start": str(start_date), "end": str(end_date)},
    )
    try:
        stats = _backfill_instrument(
            instrument_id=instrument["instrument_id"], aliases=aliases, languages=languages,
            start_date=start_date, end_date=end_date, run_id=run_id,
        )
        finish_run(
            run_id, status="success", records_raw=stats["fetched"],
            records_inserted=stats["inserted"], records_updated=stats["updated"],
        )
        return {"status": "success", **stats}
    except Exception as exc:
        logger.error("sync-news falhou para %s: %s", ticker, exc)
        finish_run(run_id, status="failed", error_message=str(exc))
        return {"status": "failed", "error": str(exc)}


def _backfill_instrument(
    *, instrument_id: int, aliases: list[str], languages: list[str],
    start_date: date, end_date: date, run_id: int,
) -> dict[str, int]:
    fetched = inserted = updated = links = 0
    windows_success = windows_empty = windows_skipped_terminal = 0
    windows_skipped_backoff = windows_failed = windows_out_of_range = 0

    for language in languages:
        query = build_company_query(aliases, sourcelang=language)
        for chunk_start, chunk_end in _weekly_chunks(start_date, end_date):
            checkpoint = _get_checkpoint(instrument_id, language, chunk_start, chunk_end)
            if checkpoint is not None and checkpoint["status"] in TERMINAL_STATUSES:
                windows_skipped_terminal += 1
                continue
            if checkpoint is not None and not _retry_due(checkpoint):
                windows_skipped_backoff += 1
                continue

            if chunk_end < MIN_SUPPORTED_START:
                # Janela inteira antes da cobertura conhecida do GDELT -- nao
                # gasta chamada de rede nem orcamento de rate limit numa data
                # que a fonte ja provou que rejeita (fase1.1 22).
                windows_out_of_range += 1
                _write_checkpoint(
                    instrument_id=instrument_id, language=language,
                    chunk_start=chunk_start, chunk_end=chunk_end,
                    previous_attempts=checkpoint["attempts"] if checkpoint else 0,
                    run_id=run_id,
                    outcome={
                        "status": "unsupported_date_range", "articles_fetched": 0,
                        "inserted": 0, "updated": 0, "links": 0,
                        "error": f"janela inteira anterior a {MIN_SUPPORTED_START} (cobertura minima conhecida do GDELT DOC)",
                    },
                )
                continue

            outcome = _fetch_one_window(
                query=query, chunk_start=chunk_start, chunk_end=chunk_end,
                instrument_id=instrument_id, run_id=run_id,
            )
            _write_checkpoint(
                instrument_id=instrument_id, language=language,
                chunk_start=chunk_start, chunk_end=chunk_end,
                previous_attempts=checkpoint["attempts"] if checkpoint else 0,
                run_id=run_id, outcome=outcome,
            )

            fetched += outcome["articles_fetched"]
            if outcome["status"] == "success_with_results":
                windows_success += 1
                inserted += outcome["inserted"]
                updated += outcome["updated"]
                links += outcome["links"]
            elif outcome["status"] == "success_empty":
                windows_empty += 1
            elif outcome["status"] == "unsupported_date_range":
                # Reativo: janela >= MIN_SUPPORTED_START mas a fonte recusou
                # mesmo assim -- o corte assumido pode estar errado. Registra
                # pra visibilidade, mas nao e "falha" no sentido de precisar
                # de retry (TERMINAL_STATUSES ja garante isso).
                windows_out_of_range += 1
                record_finding(
                    run_id, PIPELINE, "gdelt_unsupported_date_range", "INFO",
                    f"janela {chunk_start}..{chunk_end} ({language}) fora da cobertura do GDELT: "
                    f"{outcome['error']}",
                    entity_type="news_window", entity_id=f"{chunk_start}:{chunk_end}:{language}",
                )
            else:
                windows_failed += 1
                record_finding(
                    run_id, PIPELINE, f"gdelt_{outcome['status']}", "WARNING",
                    f"janela {chunk_start}..{chunk_end} ({language}) nao coletada: "
                    f"{outcome['status']} -- {outcome['error']}",
                    entity_type="news_window", entity_id=f"{chunk_start}:{chunk_end}:{language}",
                )

    if windows_failed:
        logger.warning(
            "%d janela(s) nao coletadas por falha (rate limit/timeout/http/parse); "
            "%d puladas por backoff, %d ja resolvidas em execucao anterior, "
            "%d fora da cobertura do GDELT",
            windows_failed, windows_skipped_backoff, windows_skipped_terminal, windows_out_of_range,
        )
    return {
        "fetched": fetched, "inserted": inserted, "updated": updated, "links": links,
        "windows_success": windows_success, "windows_empty": windows_empty,
        "windows_failed": windows_failed, "windows_skipped_backoff": windows_skipped_backoff,
        "windows_skipped_terminal": windows_skipped_terminal, "windows_out_of_range": windows_out_of_range,
    }


def _fetch_one_window(
    *, query: str, chunk_start: date, chunk_end: date, instrument_id: int, run_id: int
) -> dict[str, Any]:
    """Uma chamada + persistencia. Nunca deixa excecao subir -- o status
    tipado da chamada e o proprio resultado (fase1.1 12)."""
    try:
        response = fetch_articles(
            query,
            start=datetime.combine(chunk_start, time.min, tzinfo=UTC),
            end=datetime.combine(chunk_end, time.max, tzinfo=UTC),
        )
    except GdeltRateLimitError as exc:
        return _failed_outcome("rate_limited", exc)
    except GdeltTimeoutError as exc:
        return _failed_outcome("timeout", exc)
    except GdeltHttpError as exc:
        return _failed_outcome("http_error", exc)
    except GdeltParseError as exc:
        return _failed_outcome("parse_error", exc)
    except GdeltUnsupportedDateRangeError as exc:
        return _failed_outcome("unsupported_date_range", exc)

    articles = response.articles
    if not articles:
        return {"status": "success_empty", "articles_fetched": 0, "inserted": 0, "updated": 0, "links": 0, "error": None}

    if len(articles) >= 250:
        record_finding(
            run_id, PIPELINE, "gdelt_possible_truncation", "INFO",
            f"janela {chunk_start}..{chunk_end} retornou o teto de 250 artigos -- "
            "pode haver mais noticias nao coletadas nesse periodo",
            entity_type="news_window", entity_id=f"{chunk_start}:{chunk_end}",
        )

    rows = _build_rows(articles, query_used=query, raw_file=str(response.raw_path), run_id=run_id)
    if not rows:
        return {"status": "success_empty", "articles_fetched": len(articles), "inserted": 0, "updated": 0, "links": 0, "error": None}

    stats = upsert_many(
        "news_articles", rows, conflict_columns=["provider", "url_hash"],
        update_columns=ARTICLE_UPDATE_COLUMNS,
    )
    link_count = _link_articles_to_company(rows, instrument_id=instrument_id)
    return {
        "status": "success_with_results", "articles_fetched": len(articles),
        "inserted": stats["inserted"], "updated": stats["updated"], "links": link_count, "error": None,
    }


def _failed_outcome(status: str, exc: Exception) -> dict[str, Any]:
    return {"status": status, "articles_fetched": 0, "inserted": 0, "updated": 0, "links": 0, "error": str(exc)[:500]}


def _get_checkpoint(instrument_id: int, language: str, chunk_start: date, chunk_end: date) -> dict[str, Any] | None:
    return fetch_one(
        "select status, attempts, next_retry_at from public.news_backfill_checkpoints "
        "where provider = %s and instrument_id = %s and language = %s "
        "and window_start = %s and window_end = %s",
        ["gdelt", instrument_id, language, chunk_start, chunk_end],
    )


def _retry_due(checkpoint: dict[str, Any]) -> bool:
    next_retry_at = checkpoint.get("next_retry_at")
    if next_retry_at is None:
        return True
    now = datetime.now(UTC)
    if isinstance(next_retry_at, str):
        next_retry_at = datetime.fromisoformat(next_retry_at.replace("Z", "+00:00"))
    if next_retry_at.tzinfo is None:
        next_retry_at = next_retry_at.replace(tzinfo=UTC)
    return now >= next_retry_at


def _write_checkpoint(
    *, instrument_id: int, language: str, chunk_start: date, chunk_end: date,
    previous_attempts: int, run_id: int, outcome: dict[str, Any],
) -> None:
    now = datetime.now(UTC)
    attempts = previous_attempts + 1
    next_retry_at = None
    if outcome["status"] in FAILURE_STATUSES:
        backoff_idx = min(attempts - 1, len(_RETRY_BACKOFF_SECONDS) - 1)
        next_retry_at = now + timedelta(seconds=_RETRY_BACKOFF_SECONDS[backoff_idx])

    upsert_many(
        "news_backfill_checkpoints",
        [{
            "provider": "gdelt",
            "instrument_id": instrument_id,
            "language": language,
            "window_start": chunk_start,
            "window_end": chunk_end,
            "status": outcome["status"],
            "articles_fetched": outcome["articles_fetched"],
            "attempts": attempts,
            "last_attempt_at": now,
            "next_retry_at": next_retry_at,
            "error_message": outcome["error"],
            "run_id": run_id,
        }],
        conflict_columns=_CHECKPOINT_CONFLICT_COLUMNS,
        update_columns=[
            "status", "articles_fetched", "attempts", "last_attempt_at",
            "next_retry_at", "error_message", "run_id",
        ],
    )


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


def _languages() -> list[str]:
    settings = load_settings()
    languages = settings["news"].get("languages") or [settings["news"].get("language", "portuguese")]
    return list(languages)


def _list_sync_targets() -> list[str]:
    rows = fetch_all(
        "select ticker from public.instruments where active = true and is_benchmark = false order by ticker"
    )
    return [r["ticker"] for r in rows]


def sync_news_all(*, start: date | None = None, end: date | None = None) -> dict[str, Any]:
    """Roda ``sync_news`` para todo o universo (exceto benchmark, que nao tem
    noticias associadas -- IBOV nao e uma empresa)."""
    results = {t: sync_news(ticker=t, start=start, end=end) for t in _list_sync_targets()}
    return {"results": results, "failed": [t for t, r in results.items() if r.get("status") == "failed"]}
