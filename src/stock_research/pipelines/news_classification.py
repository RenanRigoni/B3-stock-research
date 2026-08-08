"""``analyze-news`` etapa 3: classificacao heuristica + novelty score
(fase1.md 33-37, Milestone 8).

Roda depois do clustering (Milestone 7): novelty depende de saber qual
artigo e canonico dentro do cluster. Grava uma linha por
``(article_id, instrument_id, analysis_method, analysis_version)`` --
reprocessar com uma versao nova da heuristica nunca mistura com a antiga
(fase1.md 87).
"""

from __future__ import annotations

from typing import Any

from stock_research.config import load_taxonomy
from stock_research.db import fetch_all, finish_run, start_run, upsert_many
from stock_research.logging import get_logger
from stock_research.transforms.news_classifier import classify, to_analysis_row

logger = get_logger(__name__)

PIPELINE = "news_classification"

ANALYSIS_UPDATE_COLUMNS = [
    "category", "subcategory", "sentiment", "sentiment_score", "relevance_score",
    "novelty_score", "impact_score", "is_company_specific", "is_macro", "is_sector",
    "is_rumor", "is_official_source", "analysis_model", "explanation", "analyzed_at",
]


def classify_news(ticker: str) -> dict[str, Any]:
    instrument = _get_instrument(ticker)
    run_id = start_run(PIPELINE, ticker=ticker)
    try:
        stats = _classify_for_instrument(instrument["instrument_id"])
        finish_run(run_id, status="success", records_raw=stats["considered"], records_inserted=stats["classified"])
        return {"status": "success", **stats}
    except Exception as exc:
        logger.error("classify-news falhou para %s: %s", ticker, exc)
        finish_run(run_id, status="failed", error_message=str(exc))
        return {"status": "failed", "error": str(exc)}


def _classify_for_instrument(instrument_id: int) -> dict[str, int]:
    taxonomy = load_taxonomy()
    rows = fetch_all(
        "select a.article_id, a.title, a.domain, a.duplicate_cluster_id, a.is_cluster_canonical, "
        "       a.published_at_utc, l.relevance_score "
        "from public.news_articles a join public.news_company_links l using (article_id) "
        "where l.instrument_id = %s",
        [instrument_id],
    )
    if not rows:
        return {"considered": 0, "classified": 0}

    novelty_by_article = _novelty_scores(rows)

    analysis_rows = []
    for row in rows:
        classification = classify(row["title"], taxonomy, domain=row.get("domain"))
        analysis_rows.append(
            to_analysis_row(
                classification,
                article_id=row["article_id"],
                instrument_id=instrument_id,
                relevance_score=row.get("relevance_score"),
                novelty_score=novelty_by_article.get(row["article_id"], 1.0),
            )
        )

    stats = upsert_many(
        "news_analysis", analysis_rows,
        conflict_columns=["article_id", "instrument_id", "analysis_method", "analysis_version"],
        update_columns=ANALYSIS_UPDATE_COLUMNS,
    )
    return {"considered": len(rows), "classified": stats["total"]}


def _novelty_scores(rows: list[dict[str, Any]]) -> dict[int, float]:
    """Primeiro artigo do cluster (canonico) tem novidade maxima; cada
    republicacao subsequente vale menos (fase1.md 37). Artigo fora de
    qualquer cluster e novo por definicao -- nenhuma duplicata foi
    encontrada para ele.
    """
    by_cluster: dict[int, list[dict[str, Any]]] = {}
    scores: dict[int, float] = {}

    for row in rows:
        cluster_id = row.get("duplicate_cluster_id")
        if cluster_id is None:
            scores[row["article_id"]] = 1.0
        else:
            by_cluster.setdefault(cluster_id, []).append(row)

    for members in by_cluster.values():
        ordered = sorted(
            members,
            key=lambda r: (r["published_at_utc"] is None, r["published_at_utc"], r["article_id"]),
        )
        for rank, member in enumerate(ordered):
            # 1a = 1.0, 2a = 0.5, 3a = 0.33, ... decaimento harmonico simples,
            # documentado e reproduzivel (nao uma curva arbitraria por cluster).
            scores[member["article_id"]] = round(1.0 / (rank + 1), 3)

    return scores


def _get_instrument(ticker: str) -> dict[str, Any]:
    rows = fetch_all("select instrument_id from public.instruments where ticker = %s", [ticker.upper()])
    if not rows:
        raise ValueError(f"instrumento nao cadastrado: {ticker} (rode `stock-research init`)")
    return rows[0]
