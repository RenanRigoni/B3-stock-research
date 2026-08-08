"""Regressao real: o backend REST quebrava quando duas linhas do MESMO lote
tinham a mesma chave de conflito.

Caso genuino que derrubou ``sync-news --ticker PETR4`` contra o Supabase real:
``http://www.em.com.br/...`` e ``https://www.em.com.br/...`` sao o mesmo
artigo, e ``canonicalize_url`` corretamente os colapsa no mesmo
``url_hash`` -- mas as duas linhas foram enviadas no MESMO POST, e o Postgres
rejeita ``INSERT ... ON CONFLICT DO UPDATE`` quando duas linhas do mesmo
statement colidem na mesma chave ("cannot affect row a second time"). Ja
tinha acontecido antes com DMPL (financial_statement_facts). Correcao: o
backend deduplica por ``conflict_columns`` antes de montar o request.
"""

from __future__ import annotations

from stock_research.db.rest import _dedupe_by_conflict_key


class TestDedupeByConflictKey:
    def test_linhas_sem_colisao_passam_intactas(self):
        rows = [{"id": 1, "v": "a"}, {"id": 2, "v": "b"}]

        result = _dedupe_by_conflict_key(rows, ["id"])

        assert result == rows

    def test_colisao_mantem_a_ultima_ocorrencia(self):
        rows = [{"id": 1, "v": "antiga"}, {"id": 1, "v": "nova"}]

        result = _dedupe_by_conflict_key(rows, ["id"])

        assert len(result) == 1
        assert result[0]["v"] == "nova"

    def test_caso_real_http_https_mesmo_artigo(self):
        rows = [
            {
                "provider": "gdelt",
                "url": "http://www.em.com.br/a.html",
                "url_hash": "abc123",
            },
            {
                "provider": "gdelt",
                "url": "https://www.em.com.br/a.html",
                "url_hash": "abc123",  # canonicalize_url colapsa para o mesmo hash
            },
        ]

        result = _dedupe_by_conflict_key(rows, ["provider", "url_hash"])

        assert len(result) == 1

    def test_chave_composta_so_colide_quando_todas_as_colunas_batem(self):
        rows = [
            {"provider": "gdelt", "url_hash": "x"},
            {"provider": "brapi", "url_hash": "x"},  # provider diferente: nao colide
        ]

        result = _dedupe_by_conflict_key(rows, ["provider", "url_hash"])

        assert len(result) == 2

    def test_lista_vazia(self):
        assert _dedupe_by_conflict_key([], ["id"]) == []

    def test_preserva_ordem_relativa_das_chaves_restantes(self):
        rows = [{"id": 3}, {"id": 1}, {"id": 1}, {"id": 2}]

        result = _dedupe_by_conflict_key(rows, ["id"])

        assert [r["id"] for r in result] == [3, 1, 2]
