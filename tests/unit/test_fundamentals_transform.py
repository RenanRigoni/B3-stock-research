"""Testes das transformacoes puras CVM -> fato (fase1.md 46-48)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from stock_research.sources.fundamentals.cvm_common import StatementFileInfo
from stock_research.transforms.fundamentals_facts import (
    availability_from_metadata,
    build_document_row,
    build_fact_row,
    compute_source_row_hash,
    derive_isolated_quarter_value,
    end_of_day_brt,
    infer_period_type,
    normalize_fiscal_year_order,
    parse_date,
    parse_decimal,
    parse_scale,
)


class TestParsePrimitivos:
    def test_parse_date_valida(self):
        assert parse_date("2024-03-31") == date(2024, 3, 31)

    def test_parse_date_vazio_e_none(self):
        assert parse_date("") is None
        assert parse_date(None) is None

    def test_parse_date_invalida_e_none_nunca_lanca(self):
        assert parse_date("nao-e-uma-data") is None

    def test_parse_decimal_com_muitas_casas(self):
        assert parse_decimal("265438605.0000000000") == Decimal("265438605.0000000000")

    def test_parse_decimal_negativo(self):
        assert parse_decimal("-79856000.0000000000") == Decimal("-79856000.0000000000")

    def test_parse_decimal_vazio_e_none(self):
        assert parse_decimal("") is None
        assert parse_decimal(None) is None

    def test_parse_scale_mil(self):
        assert parse_scale("MIL") == 1000

    def test_parse_scale_unidade(self):
        assert parse_scale("UNIDADE") == 1

    def test_parse_scale_desconhecida_e_none_nunca_assume_1(self):
        # fase1.md 123: dado incorreto (assumir escala errada) e pior que
        # dado ausente -- nunca cair silenciosamente para 1.
        assert parse_scale("BILHAO") is None
        assert parse_scale(None) is None


class TestNormalizeFiscalYearOrder:
    def test_ultimo(self):
        assert normalize_fiscal_year_order("ÚLTIMO") == "ULTIMO"

    def test_penultimo(self):
        assert normalize_fiscal_year_order("PENÚLTIMO") == "PENULTIMO"

    def test_vazio_e_none(self):
        assert normalize_fiscal_year_order("") is None
        assert normalize_fiscal_year_order(None) is None


class TestInferPeriodType:
    def test_balanco_e_sempre_point_in_time(self):
        assert infer_period_type(
            document_type="ITR", statement_type="BPA",
            period_start=None, period_end=date(2024, 9, 30),
        ) == "point_in_time"

    def test_dfp_e_sempre_anual(self):
        assert infer_period_type(
            document_type="DFP", statement_type="DRE",
            period_start=date(2024, 1, 1), period_end=date(2024, 12, 31),
        ) == "annual"

    def test_itr_com_inicio_em_1_de_janeiro_e_acumulado(self):
        assert infer_period_type(
            document_type="ITR", statement_type="DRE",
            period_start=date(2024, 1, 1), period_end=date(2024, 9, 30),
        ) == "ytd"

    def test_itr_com_inicio_fora_de_janeiro_e_trimestre_isolado(self):
        assert infer_period_type(
            document_type="ITR", statement_type="DRE",
            period_start=date(2024, 7, 1), period_end=date(2024, 9, 30),
        ) == "quarterly"

    def test_sem_datas_e_unknown(self):
        assert infer_period_type(
            document_type="ITR", statement_type="DRE", period_start=None, period_end=None,
        ) == "unknown"


class TestComputeSourceRowHash:
    def _key(self, **overrides):
        base = dict(
            cnpj="33.000.167/0001-01", document_type="DFP", statement_type="DRE",
            is_consolidated=True, reference_date=date(2024, 12, 31), version="1",
            period_start=date(2024, 1, 1), period_end=date(2024, 12, 31), account_code="3.01",
        )
        base.update(overrides)
        return base

    def test_e_deterministico(self):
        assert compute_source_row_hash(**self._key()) == compute_source_row_hash(**self._key())

    def test_conta_diferente_gera_hash_diferente(self):
        assert compute_source_row_hash(**self._key()) != compute_source_row_hash(**self._key(account_code="3.02"))

    def test_versao_diferente_gera_hash_diferente(self):
        # Reapresentacao: versao 2 e uma linha NOVA, nunca sobrescreve a versao 1 (fase1.md 48).
        assert compute_source_row_hash(**self._key()) != compute_source_row_hash(**self._key(version="2"))

    def test_consolidado_vs_individual_gera_hash_diferente(self):
        assert compute_source_row_hash(**self._key()) != compute_source_row_hash(**self._key(is_consolidated=False))


class TestAvailabilityFromMetadata:
    def test_disponivel_a_partir_do_fim_do_dia_de_recebimento(self):
        _, available_from = availability_from_metadata({"DT_RECEB": "2025-02-19"})
        assert available_from == end_of_day_brt(date(2025, 2, 19))

    def test_sem_metadados_e_none_nunca_estimado(self):
        assert availability_from_metadata(None) == (None, None)

    def test_dt_receb_ausente_e_none_nunca_estimado(self):
        assert availability_from_metadata({"DT_RECEB": ""}) == (None, None)


class TestBuildFactRow:
    def _csv_row(self, **overrides):
        base = {
            "CNPJ_CIA": "33.000.167/0001-01", "DT_REFER": "2024-12-31", "VERSAO": "1",
            "DENOM_CIA": "PETROLEO BRASILEIRO S.A. PETROBRAS", "CD_CVM": "009512",
            "GRUPO_DFP": "DF Consolidado - Demonstracao do Resultado", "MOEDA": "REAL",
            "ESCALA_MOEDA": "MIL", "ORDEM_EXERC": "ÚLTIMO",
            "DT_INI_EXERC": "2024-01-01", "DT_FIM_EXERC": "2024-12-31",
            "CD_CONTA": "3.01", "DS_CONTA": "Receita de Venda de Bens e/ou Servicos",
            "VL_CONTA": "500000000.0000000000", "ST_CONTA_FIXA": "S",
        }
        base.update(overrides)
        return base

    def _info(self, statement_type="DRE", is_consolidated=True):
        return StatementFileInfo(
            member_name="dfp_cia_aberta_DRE_con_2024.csv", statement_type=statement_type,
            is_consolidated=is_consolidated, year=2024,
        )

    def test_linha_valida_e_convertida(self):
        result = build_fact_row(
            self._csv_row(), document_type="DFP", statement_info=self._info(),
            source_file="data/raw/cvm/dfp/x.zip", run_id=1,
            metadata_row={"DT_RECEB": "2025-02-19"},
        )
        assert result.error is None
        assert result.row["value"] == Decimal("500000000.0000000000")
        assert result.row["scale"] == 1000
        assert result.row["is_consolidated"] is True
        assert result.row["available_from"] == end_of_day_brt(date(2025, 2, 19))
        assert result.row["currency"] == "BRL"

    def test_sem_cnpj_falha_graciosamente(self):
        result = build_fact_row(
            self._csv_row(CNPJ_CIA=""), document_type="DFP", statement_info=self._info(),
            source_file="x", run_id=1, metadata_row=None,
        )
        assert result.row is None
        assert result.error

    def test_escala_desconhecida_falha_graciosamente(self):
        result = build_fact_row(
            self._csv_row(ESCALA_MOEDA="BILHAO"), document_type="DFP", statement_info=self._info(),
            source_file="x", run_id=1, metadata_row=None,
        )
        assert result.row is None
        assert "ESCALA_MOEDA" in result.error

    def test_sem_metadados_available_from_fica_none(self):
        result = build_fact_row(
            self._csv_row(), document_type="DFP", statement_info=self._info(),
            source_file="x", run_id=1, metadata_row=None,
        )
        assert result.row is not None
        assert result.row["available_from"] is None


class TestBuildDocumentRow:
    def test_linha_valida(self):
        result = build_document_row(
            {"CNPJ_CIA": "33.000.167/0001-01", "DT_REFER": "2024-12-31", "VERSAO": "1",
             "CD_CVM": "009512", "DT_RECEB": "2025-02-19", "LINK_DOC": "http://x"},
            document_type="DFP", source_file="x.zip", source_url="http://x", run_id=1,
        )
        assert result.error is None
        assert result.row["available_from"] == end_of_day_brt(date(2025, 2, 19))

    def test_metadados_incompletos_falha_graciosamente(self):
        result = build_document_row(
            {"CNPJ_CIA": "", "DT_REFER": "2024-12-31", "VERSAO": "1", "CD_CVM": "009512"},
            document_type="DFP", source_file="x.zip", source_url=None, run_id=1,
        )
        assert result.row is None


class TestDeriveIsolatedQuarterValue:
    def test_dfc_e_aditivo_subtrai_corretamente(self):
        value = derive_isolated_quarter_value(
            statement_type="DFC_MI", current_cumulative=Decimal("100"), previous_cumulative=Decimal("60")
        )
        assert value == Decimal("40")

    def test_balanco_nunca_e_isolado_por_subtracao(self):
        # BPA/BPP sao posicao, nao fluxo -- subtrair nao tem sentido contabil.
        assert derive_isolated_quarter_value(
            statement_type="BPA", current_cumulative=Decimal("100"), previous_cumulative=Decimal("60")
        ) is None

    def test_dmpl_fica_fora_do_escopo_desta_fase(self):
        assert derive_isolated_quarter_value(
            statement_type="DMPL", current_cumulative=Decimal("100"), previous_cumulative=Decimal("60")
        ) is None

    def test_sem_um_dos_lados_nunca_inventa(self):
        assert derive_isolated_quarter_value(
            statement_type="DFC_MI", current_cumulative=None, previous_cumulative=Decimal("60")
        ) is None
