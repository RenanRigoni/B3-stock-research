"""Testes de ``transforms/news_dedup.py`` -- clustering por similaridade
(fase1.md 30-31, 37). Funcao pura, sem banco.

Usa a fixture real do GDELT: os dois artigos "Cade" (dominios diferentes,
titulos quase identicos) sao o caso real de republicacao que a camada 3 de
dedup existe para pegar -- as camadas 1 (URL) e 2 (titulo exato) nao
resolveriam esse par, porque URL e titulo diferem (um tem data no final,
"limites" x "limite").
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from stock_research.transforms.news import build_article_row
from stock_research.transforms.news_dedup import build_clusters

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "gdelt" / "doc_artlist_sample.json"

SIMILARITY_THRESHOLD = 0.88
WINDOW_HOURS = 72.0


def _article(article_id: int, title: str, *, published_at, title_hash: str | None = None) -> dict:
    return {
        "article_id": article_id,
        "title": title,
        "title_normalized": title.lower(),
        "title_hash": title_hash,
        "published_at_utc": published_at,
    }


class TestBuildClusters:
    def test_titulos_identicos_formam_cluster(self):
        now = datetime(2026, 1, 1, tzinfo=UTC)
        articles = [
            _article(1, "mesmo titulo aqui", published_at=now, title_hash="h1"),
            _article(2, "mesmo titulo aqui", published_at=now, title_hash="h1"),
        ]

        clusters = build_clusters(articles, similarity_threshold=SIMILARITY_THRESHOLD, window_hours=WINDOW_HOURS)

        assert len(clusters) == 1
        assert set(clusters[0].article_ids) == {1, 2}

    def test_titulos_completamente_diferentes_nao_formam_cluster(self):
        now = datetime(2026, 1, 1, tzinfo=UTC)
        articles = [
            _article(1, "petrobras anuncia investimento bilionario", published_at=now),
            _article(2, "dolar recua com payroll nos eua", published_at=now),
        ]

        clusters = build_clusters(articles, similarity_threshold=SIMILARITY_THRESHOLD, window_hours=WINDOW_HOURS)

        assert clusters == []

    def test_fora_da_janela_de_tempo_nao_agrupa_mesmo_com_titulo_identico(self):
        t0 = datetime(2026, 1, 1, tzinfo=UTC)
        t1 = t0 + timedelta(hours=WINDOW_HOURS + 1)
        articles = [
            _article(1, "mesmo titulo aqui", published_at=t0, title_hash="h1"),
            _article(2, "mesmo titulo aqui", published_at=t1, title_hash="h1"),
        ]

        clusters = build_clusters(articles, similarity_threshold=SIMILARITY_THRESHOLD, window_hours=WINDOW_HOURS)

        assert clusters == []

    def test_sem_timestamp_nao_agrupa_por_seguranca(self):
        # Sem data confiavel dos dois lados, nao arriscar juntar por tempo --
        # mesmo com titulo identico.
        articles = [
            _article(1, "mesmo titulo aqui", published_at=None, title_hash="h1"),
            _article(2, "mesmo titulo aqui", published_at=None, title_hash="h1"),
        ]

        clusters = build_clusters(articles, similarity_threshold=SIMILARITY_THRESHOLD, window_hours=WINDOW_HOURS)

        assert clusters == []

    def test_artigo_sozinho_nunca_vira_cluster(self):
        articles = [_article(1, "noticia unica", published_at=datetime(2026, 1, 1, tzinfo=UTC))]

        clusters = build_clusters(articles, similarity_threshold=SIMILARITY_THRESHOLD, window_hours=WINDOW_HOURS)

        assert clusters == []

    def test_canonico_e_o_primeiro_publicado(self):
        t0 = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
        t1 = t0 + timedelta(hours=2)
        articles = [
            _article(1, "mesma noticia", published_at=t1, title_hash="h1"),  # republicado depois
            _article(2, "mesma noticia", published_at=t0, title_hash="h1"),  # original
        ]

        clusters = build_clusters(articles, similarity_threshold=SIMILARITY_THRESHOLD, window_hours=WINDOW_HOURS)

        assert clusters[0].canonical_article_id == 2

    def test_transitividade_agrupa_tres_artigos_via_uniao(self):
        # A ~ B (similar) e B ~ C (similar), mas A e C sozinhos podem estar
        # abaixo do threshold -- union-find ainda assim junta os tres pelo
        # elo comum B.
        now = datetime(2026, 1, 1, tzinfo=UTC)
        articles = [
            _article(1, "cade ve disparar notificacoes e proposta de elevar limites", published_at=now),
            _article(2, "cade ve disparar notificacoes e proposta de elevar limite", published_at=now),
            _article(3, "cade ve disparar notificacoes e proposta de elevar limite ganha forca", published_at=now),
        ]

        clusters = build_clusters(articles, similarity_threshold=0.85, window_hours=WINDOW_HOURS)

        assert len(clusters) == 1
        assert set(clusters[0].article_ids) == {1, 2, 3}

    def test_menos_de_dois_artigos_nao_processa(self):
        assert build_clusters([], similarity_threshold=SIMILARITY_THRESHOLD, window_hours=WINDOW_HOURS) == []
        assert (
            build_clusters(
                [_article(1, "a", published_at=datetime(2026, 1, 1, tzinfo=UTC))],
                similarity_threshold=SIMILARITY_THRESHOLD, window_hours=WINDOW_HOURS,
            )
            == []
        )


class TestClusteringComFixtureReal:
    def test_par_cade_da_fixture_forma_cluster(self):
        """Os dois artigos 'Cade' da fixture real do GDELT: dominios
        diferentes, titulos quase identicos, mesmo seendate -- exatamente o
        caso que a camada 3 existe pra pegar."""
        raw_articles = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["articles"]
        rows = []
        for i, raw in enumerate(raw_articles):
            row = build_article_row(raw, query_used="q", raw_file="f", run_id=1)
            if row:
                row["article_id"] = i
                rows.append(row)

        cade_rows = [r for r in rows if "cade" in (r["title_normalized"] or "")]
        assert len(cade_rows) == 2, "fixture deveria ter exatamente 2 materias sobre Cade"

        clusters = build_clusters(cade_rows, similarity_threshold=SIMILARITY_THRESHOLD, window_hours=WINDOW_HOURS)

        assert len(clusters) == 1
        assert len(clusters[0].article_ids) == 2
