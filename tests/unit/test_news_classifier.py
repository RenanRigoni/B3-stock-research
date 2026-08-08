"""Testes de ``transforms/news_classifier.py`` (fase1.md 33-37). Funcao pura.

Usa ``config/news_taxonomy.yaml`` real (nao uma taxonomia de teste) --
garante que o classificador funciona contra a mesma configuracao que o
pipeline vai carregar em producao, e serve de teste de regressao pra
taxonomia em si (se alguem editar as keywords e uma categoria parar de
casar exemplos obvios, o teste pega).
"""

from __future__ import annotations

from stock_research.config import load_taxonomy
from stock_research.transforms.news_classifier import classify, to_analysis_row

TAXONOMY = load_taxonomy()


class TestClassifyCategoria:
    def test_titulo_de_balanco_vira_earnings(self):
        result = classify("Petrobras divulga balanco trimestral com lucro liquido recorde", TAXONOMY)

        assert result.category == "earnings"
        assert result.scope == "company"

    def test_titulo_de_dividendo_vira_dividend(self):
        result = classify("Petrobras anuncia pagamento de dividendo extraordinario", TAXONOMY)

        assert result.category == "dividend"

    def test_titulo_de_troca_de_ceo_vira_ceo_change(self):
        result = classify("Empresa anuncia novo presidente da companhia apos saida do anterior", TAXONOMY)

        assert result.category == "ceo_change"

    def test_titulo_de_selic_vira_interest_rate_macro(self):
        result = classify("Copom eleva taxa de juros basica em 0,5 ponto percentual", TAXONOMY)

        assert result.category == "interest_rate"
        assert result.scope == "macro"
        assert result.is_macro is True
        assert result.is_company_specific is False

    def test_titulo_de_minerio_vira_commodity_price_setor(self):
        result = classify("Preco do minerio de ferro dispara na bolsa de Dalian", TAXONOMY)

        assert result.category == "commodity_price"
        assert result.scope == "sector"
        assert result.is_sector is True

    def test_titulo_sem_termo_conhecido_fica_sem_categoria(self):
        # Nunca cair em "other" por default -- nao classificado != outros.
        result = classify("xyzabc situacao completamente fora do vocabulario conhecido", TAXONOMY)

        assert result.category is None
        assert result.scope == "unknown"

    def test_explanation_traz_os_termos_que_casaram(self):
        result = classify("Petrobras divulga balanco trimestral", TAXONOMY)

        assert result.explanation["matched_terms"], "explanation precisa mostrar por que classificou assim"


class TestClassifySentiment:
    def test_titulo_positivo(self):
        result = classify("Empresa supera expectativas e lucro cresce no trimestre", TAXONOMY)

        assert result.sentiment == "positive"
        assert result.sentiment_score is not None and result.sentiment_score > 0

    def test_titulo_negativo(self):
        result = classify("Empresa registra prejuizo e acoes despencam apos resultado", TAXONOMY)

        assert result.sentiment == "negative"
        assert result.sentiment_score is not None and result.sentiment_score < 0

    def test_titulo_neutro_sem_termos_de_sentimento(self):
        result = classify("Empresa divulga cronograma de eventos do proximo trimestre", TAXONOMY)

        assert result.sentiment == "neutral"
        assert result.sentiment_score == 0.0

    def test_titulo_com_termos_dos_dois_lados_vira_mixed(self):
        # "lucro sobe, mas divida cresce" nao e nem positivo nem negativo --
        # fase1.md 33: sentimento nao pode ser reduzido a um veredito unico
        # quando o titulo genuinamente carrega os dois sinais.
        result = classify("Lucro cresce mas empresa corta investimentos apos prejuizo em outra unidade", TAXONOMY)

        assert result.sentiment == "mixed"

    def test_sentiment_e_impact_sao_campos_diferentes(self):
        # fase1.md 33, exemplo literal do documento.
        result = classify("Petrobras anuncia investimento de R$ 100 bilhoes", TAXONOMY)

        row = to_analysis_row(result, article_id=1, instrument_id=1, relevance_score=0.9, novelty_score=1.0)
        assert row["impact_score"] is None  # nunca inferido do titulo sozinho


class TestClassifyRumorEOfficial:
    def test_marcador_de_rumor_e_detectado(self):
        result = classify("Empresa estuda a possibilidade de fusao, segundo fontes", TAXONOMY)

        assert result.is_rumor is True

    def test_titulo_sem_marcador_de_rumor(self):
        result = classify("Empresa confirma fusao com concorrente em comunicado oficial", TAXONOMY)

        assert result.is_rumor is False

    def test_marcador_de_fonte_oficial_no_titulo(self):
        result = classify("Fato relevante: empresa comunica aquisicao de participacao", TAXONOMY)

        assert result.is_official_source is True

    def test_dominio_de_ri_conta_como_fonte_oficial(self):
        result = classify("Resultados do terceiro trimestre", TAXONOMY, domain="ri.petrobras.com.br")

        assert result.is_official_source is True

    def test_dominio_generico_nao_conta_como_fonte_oficial(self):
        result = classify("Resultados do terceiro trimestre", TAXONOMY, domain="g1.globo.com")

        assert result.is_official_source is False


class TestClassifyEdgeCases:
    def test_titulo_vazio(self):
        result = classify("", TAXONOMY)

        assert result.category is None
        assert result.sentiment == "unknown"

    def test_titulo_none(self):
        result = classify(None, TAXONOMY)

        assert result.category is None

    def test_case_insensitive(self):
        result = classify("PETROBRAS DIVULGA BALANCO TRIMESTRAL", TAXONOMY)

        assert result.category == "earnings"

    def test_acento_nao_impede_casamento(self):
        result = classify("Ação sobe após balanço trimestral positivo", TAXONOMY)

        assert result.category == "earnings"

    def test_versao_da_taxonomia_e_preservada_no_resultado(self):
        result = classify("Empresa divulga resultado", TAXONOMY)

        assert result.analysis_version == TAXONOMY["version"]


class TestToAnalysisRow:
    def test_campos_obrigatorios_presentes(self):
        classification = classify("Petrobras divulga balanco trimestral", TAXONOMY)
        row = to_analysis_row(classification, article_id=42, instrument_id=7, relevance_score=0.9, novelty_score=1.0)

        assert row["article_id"] == 42
        assert row["instrument_id"] == 7
        assert row["analysis_method"] == "heuristic"
        assert row["analysis_model"] is None
        assert row["novelty_score"] == 1.0
