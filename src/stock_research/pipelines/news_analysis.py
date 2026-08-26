"""``analyze-news``: deduplicacao por similaridade + relevancia de empresa
(fase1.md 29-31, 36-37, Milestone 7).

Duas etapas independentes sobre os artigos ja coletados por ``sync-news``:

    1. clustering (``transforms/news_dedup.py``): agrupa republicacoes,
       marca artigo canonico, preenche ``news_clusters``;
    2. relevancia (``transforms/news_relevance.py``): recalcula
       ``news_company_links.relevance_score`` a partir do titulo real (o
       vinculo criado por ``sync-news`` tinha ``relevance_score=None`` de
       proposito -- nunca inventar score sem ter o titulo em maos).

Nenhuma das duas depende de rede. Rodar de novo e idempotente: clusters e
scores sao recalculados do zero a cada chamada, upsert por chave natural.
"""

from __future__ import annotations

from typing import Any

from stock_research.config import load_settings
from stock_research.db import execute, fetch_all, finish_run, start_run, upsert_many
from stock_research.logging import get_logger
from stock_research.transforms.news_dedup import build_clusters
from stock_research.transforms.news_relevance import score_relevance

logger = get_logger(__name__)

PIPELINE = "news_analysis"


def analyze_news(ticker: str) -> dict[str, Any]:
    instrument = _get_instrument(ticker)
    run_id = start_run(PIPELINE, ticker=ticker)
    try:
        dedup_stats = _dedupe_articles_for_ticker(instrument["instrument_id"])
        relevance_stats = _rescore_relevance(instrument["instrument_id"])
        finish_run(
            run_id, status="success",
            records_raw=dedup_stats["articles_considered"],
            records_updated=dedup_stats["clustered"] + relevance_stats["rescored"],
        )
        return {"status": "success", **dedup_stats, **relevance_stats}
    except Exception as exc:
        logger.error("analyze-news falhou para %s: %s", ticker, exc)
        finish_run(run_id, status="failed", error_message=str(exc))
        return {"status": "failed", "error": str(exc)}


# ---------------------------------------------------------------------------
# Etapa 1: clustering (fase1.md 30-31, 37)
# ---------------------------------------------------------------------------


def _dedupe_articles_for_ticker(instrument_id: int) -> dict[str, int]:
    settings = load_settings()["news"]
    articles = fetch_all(
        "select a.article_id, a.title, a.title_normalized, a.title_hash, a.published_at_utc "
        "from public.news_articles a "
        "join public.news_company_links l using (article_id) "
        "where l.instrument_id = %s",
        [instrument_id],
    )
    if not articles:
        return {"articles_considered": 0, "clusters": 0, "clustered": 0}

    clusters = build_clusters(
        articles,
        similarity_threshold=float(settings["title_similarity_threshold"]),
        window_hours=float(settings["dedup_window_hours"]),
    )
    if not clusters:
        return {"articles_considered": len(articles), "clusters": 0, "clustered": 0}

    cluster_rows = [
        {
            "canonical_article_id": c.canonical_article_id,
            "representative_title": c.representative_title,
            "article_count": len(c.article_ids),
            "unique_domains": _count_unique_domains(c.article_ids),
            "first_seen": c.first_seen,
            "last_seen": c.last_seen,
            "dedup_method": "title_hash+rapidfuzz",
            "dedup_version": "dedup_v1",
        }
        for c in clusters
    ]

    # Reset antes de reaplicar: a composicao de um cluster pode mudar entre
    # execucoes (artigo novo chega, thresholds mudam de config). Zerar
    # primeiro garante que um artigo que SAIU de um cluster nao fique com um
    # duplicate_cluster_id obsoleto -- sem isso, so as atribuicoes NOVAS
    # seriam corrigidas, nunca as removidas (idempotencia pelo estado final,
    # nao so pela ausencia de duplicata).
    _reset_cluster_assignments(instrument_id)

    # upsert por canonical_article_id (chave natural -- ver migration
    # news_clusters_canonical_article_key): reexecutar atualiza o mesmo
    # cluster em vez de criar outro. Upsert aqui e seguro porque
    # canonical_article_id nao e a coluna identity da tabela.
    upsert_many(
        "news_clusters", cluster_rows, conflict_columns=["canonical_article_id"],
        update_columns=[
            "representative_title", "article_count", "unique_domains",
            "first_seen", "last_seen", "dedup_method", "dedup_version",
        ],
    )
    cluster_ids = _cluster_ids_by_canonical([c.canonical_article_id for c in clusters])

    # UPDATE puro, nao upsert: article_id e GENERATED ALWAYS AS IDENTITY --
    # Postgres rejeita qualquer INSERT com valor explicito nessa coluna, e
    # upsert_many monta um INSERT mesmo quando o destino e sempre um
    # ON CONFLICT DO UPDATE (mesma causa-raiz do bug ja corrigido em
    # pipelines/fundamentals.py:_sync_instrument_identifiers -- a linha aqui
    # sempre ja existe, foi lida do proprio banco duas linhas acima).
    #
    # Em lote via UPDATE ... FROM (VALUES ...): um UPDATE por artigo virou
    # gargalo real na Fase 1.1 (milhares de round-trips HTTP pro backend REST
    # so pra clusters de republicacao de PETR4/VALE3/ITUB4 -- minutos de
    # latencia de rede pura por uma operacao que e uma unica instrucao SQL).
    assignments = [
        (article_id, cluster_ids[cluster.canonical_article_id], article_id == cluster.canonical_article_id)
        for cluster in clusters
        for article_id in cluster.article_ids
    ]
    _apply_cluster_assignments(assignments)

    return {
        "articles_considered": len(articles),
        "clusters": len(clusters),
        "clustered": sum(len(c.article_ids) for c in clusters),
    }


_UPDATE_BATCH_SIZE = 1000


def _apply_cluster_assignments(assignments: list[tuple[int, int, bool]]) -> None:
    """``assignments``: ``(article_id, cluster_id, is_canonical)``. Um UPDATE
    por lote via ``VALUES``, nao um por linha (ver comentario no chamador)."""
    for start in range(0, len(assignments), _UPDATE_BATCH_SIZE):
        chunk = assignments[start : start + _UPDATE_BATCH_SIZE]
        values_sql = ", ".join("(%s::bigint, %s::bigint, %s::boolean)" for _ in chunk)
        params = [value for row in chunk for value in row]
        execute(
            "update public.news_articles as t "
            "set duplicate_cluster_id = v.cluster_id, is_cluster_canonical = v.is_canonical "
            f"from (values {values_sql}) as v(article_id, cluster_id, is_canonical) "
            "where t.article_id = v.article_id",
            params,
        )


def _reset_cluster_assignments(instrument_id: int) -> None:
    # Subquery em vez de literal `IN (id1, id2, ...)`: uma lista com dezenas
    # de milhares de IDs (PETR4 tem 166 mil artigos) estourava o parser SQL
    # do Postgres via exec_sql (500 Internal Server Error, provavel limite de
    # profundidade de recursao pra expressoes IN muito longas). A mesma
    # condicao do SELECT que populou `articles` acima, sem materializar IDs.
    execute(
        "update public.news_articles set duplicate_cluster_id = null, is_cluster_canonical = false "
        "where article_id in ("
        "  select article_id from public.news_company_links where instrument_id = %s"
        ")",
        [instrument_id],
    )


def _cluster_ids_by_canonical(canonical_article_ids: list[int]) -> dict[int, int]:
    if not canonical_article_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(canonical_article_ids))
    rows = fetch_all(
        f"select cluster_id, canonical_article_id from public.news_clusters "
        f"where canonical_article_id in ({placeholders})",
        canonical_article_ids,
    )
    return {r["canonical_article_id"]: r["cluster_id"] for r in rows}


def _count_unique_domains(article_ids: list[int]) -> int:
    if not article_ids:
        return 0
    placeholders = ", ".join(["%s"] * len(article_ids))
    rows = fetch_all(
        f"select count(distinct domain) as n from public.news_articles where article_id in ({placeholders})",
        article_ids,
    )
    return int(rows[0]["n"]) if rows else 0


# ---------------------------------------------------------------------------
# Etapa 2: relevancia (fase1.md 36)
# ---------------------------------------------------------------------------


def _rescore_relevance(instrument_id: int) -> dict[str, int]:
    aliases = fetch_all(
        "select alias, is_strong from public.company_aliases where instrument_id = %s",
        [instrument_id],
    )
    alias_pairs = [(r["alias"], r["is_strong"]) for r in aliases]

    links = fetch_all(
        "select l.article_id, a.title, a.domain "
        "from public.news_company_links l join public.news_articles a using (article_id) "
        "where l.instrument_id = %s",
        [instrument_id],
    )
    if not links:
        return {"rescored": 0}

    updates = []
    for link in links:
        result = score_relevance(link["title"], alias_pairs, domain=link.get("domain"))
        updates.append(
            {
                "article_id": link["article_id"],
                "instrument_id": instrument_id,
                "match_method": "title_alias" if result.matched_terms else "query",
                "relevance_score": result.score,
                "match_terms": result.matched_terms or None,
                "is_primary_company": result.is_primary_company,
                "review_status": "auto" if result.band != "low" else "pending_review",
            }
        )
    stats = upsert_many(
        "news_company_links", updates, conflict_columns=["article_id", "instrument_id"],
        update_columns=["match_method", "relevance_score", "match_terms", "is_primary_company", "review_status"],
    )
    return {"rescored": stats["total"]}


def _get_instrument(ticker: str) -> dict[str, Any]:
    rows = fetch_all("select instrument_id from public.instruments where ticker = %s", [ticker.upper()])
    if not rows:
        raise ValueError(f"instrumento nao cadastrado: {ticker} (rode `stock-research init`)")
    return rows[0]
