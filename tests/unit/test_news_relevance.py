"""Testes de ``transforms/news_relevance.py`` (fase1.md 36). Funcao pura."""

from __future__ import annotations

from stock_research.transforms.news_relevance import score_relevance

PETR4_ALIASES = [
    ("Petrobras", True),
    ("Petroleo Brasileiro", True),
    ("PETR4", True),
    ("Petrobas", False),
    ("PETR3", False),
]

VALE3_ALIASES = [
    ("Vale S.A.", True),
    ("VALE3", True),
    ("Vale", False),  # alias fraco de proposito (fase1.md 26)
]


class TestScoreRelevance:
    def test_alias_forte_no_titulo_da_score_alto(self):
        result = score_relevance("Petrobras anuncia investimento bilionario", PETR4_ALIASES)

        assert result.score >= 0.75
        assert result.band == "high"
        assert "Petrobras" in result.matched_terms
        assert result.is_primary_company is True

    def test_sem_nenhum_alias_no_titulo_score_zero(self):
        result = score_relevance("Dolar recua com payroll nos EUA", PETR4_ALIASES)

        assert result.score == 0.0
        assert result.band == "low"
        assert result.matched_terms == []

    def test_alias_fraco_isolado_nao_alcanca_faixa_alta(self):
        # "Vale" sozinho no titulo -- ambiguo por definicao, nunca deve virar
        # "alta confianca" sozinho.
        result = score_relevance("Vale a pena investir em acoes agora?", VALE3_ALIASES)

        assert result.band != "high"

    def test_alias_forte_da_vale_no_titulo_da_score_alto(self):
        result = score_relevance("Vale S.A. registra lucro recorde no trimestre", VALE3_ALIASES)

        assert result.band == "high"
        assert result.is_primary_company is True

    def test_titulo_vazio_score_zero(self):
        result = score_relevance("", PETR4_ALIASES)

        assert result.score == 0.0

    def test_titulo_none_score_zero(self):
        result = score_relevance(None, PETR4_ALIASES)

        assert result.score == 0.0

    def test_lista_de_aliases_vazia_score_zero(self):
        result = score_relevance("Petrobras anuncia resultado", [])

        assert result.score == 0.0

    def test_casamento_e_por_palavra_inteira_nao_substring(self):
        # "Vale" nao pode casar dentro de "inviavel" ou "avaliacao".
        result = score_relevance("Empresa considerada inviavel apos avaliacao financeira", VALE3_ALIASES)

        assert result.score == 0.0
        assert result.matched_terms == []

    def test_multiplos_aliases_fortes_aumentam_o_score(self):
        so_nome = score_relevance("Petrobras divulga balanco", PETR4_ALIASES)
        nome_e_ticker = score_relevance("Petrobras (PETR4) divulga balanco", PETR4_ALIASES)

        assert nome_e_ticker.score > so_nome.score
        assert set(nome_e_ticker.matched_terms) >= {"Petrobras", "PETR4"}

    def test_score_nunca_ultrapassa_um(self):
        aliases = [(f"Termo{i}", True) for i in range(10)]
        title = " ".join(f"Termo{i}" for i in range(10))

        result = score_relevance(title, aliases)

        assert result.score <= 1.0

    def test_dominio_do_proprio_ri_da_sinal_fraco_sem_titulo(self):
        result = score_relevance(
            "Comunicado ao mercado sobre resultados", PETR4_ALIASES, domain="ri.petrobras.com.br"
        )

        assert 0.0 < result.score < 0.50
        assert result.is_primary_company is False

    def test_dominio_generico_sem_titulo_score_zero(self):
        result = score_relevance("Comunicado ao mercado sobre resultados", PETR4_ALIASES, domain="g1.globo.com")

        assert result.score == 0.0

    def test_case_insensitive(self):
        result = score_relevance("PETROBRAS ANUNCIA LUCRO", PETR4_ALIASES)

        assert result.band == "high"

    def test_acento_nao_impede_casamento(self):
        aliases = [("Petróleo Brasileiro", True)]
        result = score_relevance("Petroleo Brasileiro tem alta no pregao", aliases)

        assert result.band == "high"
