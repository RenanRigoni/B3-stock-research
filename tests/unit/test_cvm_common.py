"""Testes do parser generico de CSV da CVM (fase1.md 45).

Usa os fixtures em ``tests/fixtures/cvm/*.zip`` -- recortes reais dos ZIPs
``dfp_cia_aberta_2024.zip``/``itr_cia_aberta_2024.zip`` baixados de
https://dados.cvm.gov.br/ e filtrados para PETR4/VALE3/ITUB4, preservando
encoding (cp1252) e separador (``;``) originais. Nao sao dados inventados.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from stock_research.sources.fundamentals.base import CvmSchemaError
from stock_research.sources.fundamentals.cvm_common import (
    REQUIRED_COLUMNS_METADATA,
    REQUIRED_COLUMNS_STATEMENT,
    detect_delimiter,
    detect_encoding,
    is_metadata_filename,
    iter_csv_rows,
    load_metadata_index,
    parse_statement_filename,
    sniff_zip_member,
    validate_columns,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "cvm"
DFP_ZIP = FIXTURES / "dfp_cia_aberta_2024.zip"
ITR_ZIP = FIXTURES / "itr_cia_aberta_2024.zip"

PETR4_CNPJ = "33.000.167/0001-01"
ITUB4_CNPJ = "60.872.504/0001-23"


class TestDetectEncoding:
    def test_ascii_puro_e_utf8(self):
        assert detect_encoding(b"CNPJ_CIA;DT_REFER\n123;2024-01-01") == "utf-8"

    def test_acento_em_cp1252_nao_decodifica_como_utf8_estrito(self):
        # 0xe7 sozinho (sem continuation byte) e invalido em UTF-8, valido em cp1252 ("ç").
        sample = "GRUPO_DFP;Demonstra".encode("ascii") + b"\xe7\xe3o"
        assert detect_encoding(sample) == "cp1252"


class TestDetectDelimiter:
    def test_separador_ponto_e_virgula(self):
        assert detect_delimiter("CNPJ_CIA;DT_REFER;VERSAO") == ";"

    def test_fallback_para_ponto_e_virgula_quando_sniffer_falha(self):
        # Uma unica coluna, sem separador algum: sniffer nao consegue decidir.
        assert detect_delimiter("SOMENTE_UMA_COLUNA") == ";"


class TestParseStatementFilename:
    def test_reconhece_dre_consolidado(self):
        info = parse_statement_filename("dfp_cia_aberta_DRE_con_2024.csv")
        assert info is not None
        assert info.statement_type == "DRE"
        assert info.is_consolidated is True
        assert info.year == 2024

    def test_reconhece_bpp_individual(self):
        info = parse_statement_filename("itr_cia_aberta_BPP_ind_2024.csv")
        assert info is not None
        assert info.statement_type == "BPP"
        assert info.is_consolidated is False

    def test_composicao_capital_nao_e_arquivo_de_demonstracao(self):
        assert parse_statement_filename("dfp_cia_aberta_composicao_capital_2024.csv") is None

    def test_parecer_nao_e_arquivo_de_demonstracao(self):
        assert parse_statement_filename("dfp_cia_aberta_parecer_2024.csv") is None

    def test_metadados_nao_e_arquivo_de_demonstracao(self):
        assert parse_statement_filename("dfp_cia_aberta_2024.csv") is None


class TestIsMetadataFilename:
    def test_arquivo_de_metadados(self):
        assert is_metadata_filename("dfp_cia_aberta_2024.csv") is True

    def test_arquivo_de_demonstracao_nao_e_metadados(self):
        assert is_metadata_filename("dfp_cia_aberta_DRE_con_2024.csv") is False


class TestValidateColumns:
    def test_colunas_extras_sao_toleradas(self):
        # fase1.md 45: tolerar colunas novas.
        validate_columns({"A", "B", "C", "NOVA_COLUNA_2025"}, {"A", "B"}, context="teste")

    def test_coluna_obrigatoria_ausente_falha_explicitamente(self):
        with pytest.raises(CvmSchemaError, match="colunas obrigatorias ausentes"):
            validate_columns({"A"}, {"A", "B"}, context="teste")


@pytest.mark.skipif(not DFP_ZIP.exists(), reason="fixture DFP ausente")
class TestFixtureZipReal:
    def test_metadados_tem_as_colunas_minimas(self):
        with zipfile.ZipFile(DFP_ZIP) as zf:
            _, _, columns = sniff_zip_member(zf, "dfp_cia_aberta_2024.csv")
        validate_columns(set(columns), REQUIRED_COLUMNS_METADATA, context="metadados")

    def test_demonstracao_tem_as_colunas_minimas(self):
        with zipfile.ZipFile(DFP_ZIP) as zf:
            _, _, columns = sniff_zip_member(zf, "dfp_cia_aberta_DRE_con_2024.csv")
        validate_columns(set(columns), REQUIRED_COLUMNS_STATEMENT, context="DRE")

    def test_iter_csv_rows_em_streaming_devolve_linhas_da_petrobras(self):
        with zipfile.ZipFile(DFP_ZIP) as zf:
            rows = list(iter_csv_rows(zf, "dfp_cia_aberta_DRE_con_2024.csv"))
        petr4_rows = [r for r in rows if r["CNPJ_CIA"] == PETR4_CNPJ]
        assert len(petr4_rows) > 0
        assert all(r["DENOM_CIA"] for r in petr4_rows)

    def test_metadata_index_indexa_por_cnpj_data_versao(self):
        with zipfile.ZipFile(DFP_ZIP) as zf:
            index = load_metadata_index(zf, "dfp_cia_aberta_2024.csv")
        key = (PETR4_CNPJ, "2024-12-31", "1")
        assert key in index
        assert index[key]["DT_RECEB"]

    def test_itub4_e_banco_com_estrutura_bpa_diferente_de_petr4(self):
        """Achado real (fase1.md 45): plano de contas da CVM e elastico --
        ITUB4 (banco) nao usa "Ativo Circulante"/"Ativo Nao Circulante" como
        PETR4/VALE3 no nivel 1 do BPA. Documenta por que o mapeamento de
        metricas usa descricao normalizada, nunca posicao de CD_CONTA."""
        with zipfile.ZipFile(DFP_ZIP) as zf:
            rows = list(iter_csv_rows(zf, "dfp_cia_aberta_BPA_con_2024.csv"))
        itub4_level1 = {
            r["DS_CONTA"] for r in rows
            if r["CNPJ_CIA"] == ITUB4_CNPJ and r["CD_CONTA"].count(".") == 1 and r["ORDEM_EXERC"] == "ÚLTIMO"
        }
        petr4_level1 = {
            r["DS_CONTA"] for r in rows
            if r["CNPJ_CIA"] == PETR4_CNPJ and r["CD_CONTA"].count(".") == 1 and r["ORDEM_EXERC"] == "ÚLTIMO"
        }
        assert "Ativo Circulante" in petr4_level1
        assert "Ativo Circulante" not in itub4_level1


@pytest.mark.skipif(not ITR_ZIP.exists(), reason="fixture ITR ausente")
class TestItrTrimestreIsoladoVsAcumulado:
    def test_dre_traz_isolado_e_acumulado_no_mesmo_arquivo(self):
        """Achado real validado contra o ZIP (fase1.md 44): para Q3, a CVM
        entrega tanto o trimestre isolado (jul-set) quanto o acumulado
        (jan-set) na mesma DT_REFER, diferenciados por DT_INI_EXERC."""
        with zipfile.ZipFile(ITR_ZIP) as zf:
            rows = list(iter_csv_rows(zf, "itr_cia_aberta_DRE_con_2024.csv"))
        q3_petr4 = {
            (r["DT_INI_EXERC"], r["DT_FIM_EXERC"])
            for r in rows
            if r["CNPJ_CIA"] == PETR4_CNPJ and r["DT_REFER"] == "2024-09-30" and r["ORDEM_EXERC"] == "ÚLTIMO"
        }
        assert ("2024-01-01", "2024-09-30") in q3_petr4  # acumulado 9M
        assert ("2024-07-01", "2024-09-30") in q3_petr4  # isolado Q3

    def test_dfc_so_traz_acumulado_no_itr(self):
        """Contraste com o teste acima: DFC (fluxo de caixa) so vem
        acumulado no ITR -- isolar o trimestre exige subtracao explicita
        (fase1.md 44), nao vem pronto da CVM como no DRE."""
        with zipfile.ZipFile(ITR_ZIP) as zf:
            rows = list(iter_csv_rows(zf, "itr_cia_aberta_DFC_MI_con_2024.csv"))
        q3_petr4_starts = {
            r["DT_INI_EXERC"]
            for r in rows
            if r["CNPJ_CIA"] == PETR4_CNPJ and r["DT_REFER"] == "2024-09-30" and r["ORDEM_EXERC"] == "ÚLTIMO"
        }
        assert q3_petr4_starts == {"2024-01-01"}
