"""Testes de ``transforms/news.py`` -- canonicalizacao de URL, normalizacao
de titulo, parsing de data do GDELT e montagem de linha (fase1.md 28-30).

Usa a fixture real capturada da API (``tests/fixtures/gdelt/doc_artlist_sample.json``)
em vez de dados inventados -- os dois artigos "Cade" nela sao um caso real de
quase-duplicata (mesmo dominio, IDs de noticia diferentes, titulo quase
identico), util tambem para os testes de dedup do Milestone 7.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from stock_research.transforms.news import (
    build_article_row,
    canonicalize_url,
    normalize_title,
    parse_gdelt_seendate,
    title_hash,
    url_hash,
)

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "gdelt" / "doc_artlist_sample.json"


def _load_fixture_articles() -> list[dict]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))["articles"]


class TestCanonicalizeUrl:
    def test_http_e_https_colapsam(self):
        assert canonicalize_url("http://Example.com/a") == canonicalize_url("https://example.com/a")

    def test_www_e_removido(self):
        assert canonicalize_url("https://www.example.com/a") == canonicalize_url("https://example.com/a")

    def test_utm_params_sao_removidos(self):
        with_utm = "https://example.com/a?utm_source=x&utm_medium=y&id=1"
        without_utm = "https://example.com/a?id=1"

        assert canonicalize_url(with_utm) == canonicalize_url(without_utm)

    def test_fragmento_e_removido(self):
        assert canonicalize_url("https://example.com/a#section2") == canonicalize_url("https://example.com/a")

    def test_barra_final_e_removida(self):
        assert canonicalize_url("https://example.com/a/") == canonicalize_url("https://example.com/a")

    def test_parametro_de_conteudo_e_preservado(self):
        # So parametros de tracking somem; parametros que mudam o recurso
        # (paginacao, id) tem que sobreviver, senao artigos diferentes colapsam.
        assert canonicalize_url("https://example.com/a?page=2") != canonicalize_url("https://example.com/a?page=3")

    def test_dominio_vira_minusculo(self):
        assert canonicalize_url("https://EXAMPLE.com/a") == canonicalize_url("https://example.com/a")


class TestUrlHash:
    def test_urls_equivalentes_tem_mesmo_hash(self):
        a = url_hash(canonicalize_url("https://www.example.com/a/?utm_source=x"))
        b = url_hash(canonicalize_url("http://example.com/a"))

        assert a == b

    def test_urls_diferentes_tem_hash_diferente(self):
        assert url_hash(canonicalize_url("https://example.com/a")) != url_hash(canonicalize_url("https://example.com/b"))


class TestNormalizeTitle:
    def test_minusculas_e_sem_acento(self):
        assert normalize_title("Petróleo é Notícia") == "petroleo e noticia"

    def test_pontuacao_vira_espaco(self):
        assert normalize_title("Cade: decisão! importante?") == "cade decisao importante"

    def test_espacos_multiplos_colapsam(self):
        assert normalize_title("a   b\tc\nd") == "a b c d"

    def test_titulos_quase_identicos_normalizam_igual(self):
        # Caso real da fixture: mesmo fato, um titulo com data no final.
        a = normalize_title("Cade vê disparar notificações e proposta de elevar limites de faturamento ganha força")
        b = normalize_title(
            "Cade vê disparar notificações e proposta de elevar limite ganha força - 08 / 08 / 2026"
        )
        # Nao sao identicos (um tem "limites"/"limite" diferente e data) --
        # mas o normalizado precisa preservar a diferenca real de conteudo
        # para a similaridade (RapidFuzz, Milestone 7) ter o que comparar.
        assert a != b
        assert "cade" in a and "cade" in b


class TestParseGdeltSeendate:
    def test_formato_real_da_api(self):
        result = parse_gdelt_seendate("20260808T141500Z")

        assert result == datetime(2026, 8, 8, 14, 15, 0, tzinfo=UTC)

    def test_vazio_e_none(self):
        assert parse_gdelt_seendate(None) is None
        assert parse_gdelt_seendate("") is None

    def test_formato_invalido_e_none_nao_excecao(self):
        assert parse_gdelt_seendate("data invalida") is None
        assert parse_gdelt_seendate("2026-08-08T14:15:00Z") is None  # ISO padrao, nao o formato do GDELT


class TestBuildArticleRow:
    def test_artigo_sem_url_e_descartado(self):
        row = build_article_row({"title": "sem url"}, query_used="q", raw_file="f", run_id=1)

        assert row is None

    def test_campos_basicos_da_fixture_real(self):
        articles = _load_fixture_articles()
        row = build_article_row(articles[0], query_used='"Petrobras"', raw_file="raw/f.json", run_id=1)

        assert row is not None
        assert row["provider"] == "gdelt"
        assert row["domain"] == "dgabc.com.br"
        assert row["language"] == "Portuguese"
        assert row["time_precision"] == "hour"
        assert row["published_at_utc"] == datetime(2026, 8, 8, 14, 15, 0, tzinfo=UTC)
        assert row["query_used"] == '"Petrobras"'
        assert row["raw_file"] == "raw/f.json"

    def test_tone_fica_none_artlist_nao_devolve(self):
        # fase1.md 28: campo opcional ausente na fonte fica ausente, nao
        # inventado. ArtList (o modo usado) nao devolve tone.
        articles = _load_fixture_articles()
        row = build_article_row(articles[0], query_used="q", raw_file="f", run_id=1)

        assert row["tone"] is None

    def test_todos_os_artigos_da_fixture_produzem_linha_valida(self):
        articles = _load_fixture_articles()
        rows = [build_article_row(a, query_used="q", raw_file="f", run_id=1) for a in articles]

        assert all(r is not None for r in rows)
        assert all(r["canonical_url"] for r in rows)
        assert all(r["url_hash"] for r in rows)

    def test_quase_duplicatas_da_fixture_tem_url_hash_diferente_mas_titulo_normalizado_parecido(self):
        # As duas materias "Cade" na fixture sao paginas distintas (URLs
        # diferentes) do mesmo fato -- dedup por URL nao resolve esse caso,
        # e exatamente por isso a camada 2 (titulo) e a camada 3
        # (similaridade, Milestone 7) existem.
        articles = _load_fixture_articles()
        cade_articles = [a for a in articles if "Cade" in a["title"]]
        assert len(cade_articles) == 2

        rows = [build_article_row(a, query_used="q", raw_file="f", run_id=1) for a in cade_articles]
        assert rows[0]["url_hash"] != rows[1]["url_hash"]
        assert rows[0]["title_hash"] != rows[1]["title_hash"]  # titulos diferem (data, "limite"/"limites")


class TestTitleHash:
    def test_titulo_vazio_e_none(self):
        assert title_hash("") is None

    def test_mesmo_titulo_normalizado_mesmo_hash(self):
        assert title_hash(normalize_title("Notícia Teste")) == title_hash(normalize_title("noticia teste"))
