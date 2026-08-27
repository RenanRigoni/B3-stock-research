"""Testes das transformacoes puras FRE -> share_count_history (fase2_plan.md 3).

Dados calcados nos arquivos FRE reais de 2024/2013 inspecionados antes do
parser existir (PETR4/VALE3/ITUB4).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal

from stock_research.transforms.share_count import (
    build_share_count_rows,
    dedupe_key,
    latest_available,
    select_capital_integralizado,
)

BRT = timezone(timedelta(hours=-3))


def _cap_row(tipo: str, on: str, pn: str, total: str, aprov: str = "2024-04-02") -> dict:
    return {
        "CNPJ_Companhia": "33.000.167/0001-01",
        "Data_Referencia": "2024-12-31",
        "Versao": "28",
        "Tipo_Capital": tipo,
        "Data_Autorizacao_Aprovacao": aprov,
        "Quantidade_Acoes_Ordinarias": on,
        "Quantidade_Acoes_Preferenciais": pn,
        "Quantidade_Total_Acoes": total,
    }


def _index_row(dt_receb: str = "2024-07-25") -> dict:
    return {
        "CNPJ_CIA": "33.000.167/0001-01",
        "DT_REFER": "2024-12-31",
        "VERSAO": "28",
        "CD_CVM": "009512",
        "DT_RECEB": dt_receb,
        "LINK_DOC": "http://example/doc",
    }


def _distribuicao_row() -> dict:
    return {
        "CNPJ_Companhia": "33.000.167/0001-01",
        "Data_Referencia": "2024-12-31",
        "Versao": "28",
        "Quantidade_Acoes_Ordinarias_Circulacao": "3483155534",
        "Quantidade_Acoes_Preferenciais_Circulacao": "4410955873",
        "Quantidade_Total_Acoes_Circulacao": "7894111407",
    }


class TestSelectCapitalIntegralizado:
    def test_uma_linha_integralizado_e_ok(self):
        rows = [
            _cap_row("Capital Emitido", "7442231382", "5446501379", "12888732761"),
            _cap_row("Capital Subscrito", "7442231382", "5446501379", "12888732761"),
            _cap_row("Capital Integralizado", "7442231382", "5446501379", "12888732761"),
        ]
        sel = select_capital_integralizado(rows)
        assert sel.quality_flag == "ok"
        assert sel.row["Quantidade_Acoes_Ordinarias"] == "7442231382"

    def test_sem_nenhum_tipo_de_capital_e_missing_input(self):
        rows = [_cap_row("Capital Autorizado", "1", "2", "3")]
        sel = select_capital_integralizado(rows)
        assert sel.row is None
        assert sel.quality_flag == "missing_input"

    def test_fallback_para_subscrito_quando_sem_integralizado(self):
        rows = [
            _cap_row("Capital Emitido", "10", "0", "10"),
            _cap_row("Capital Subscrito", "10", "0", "10"),
        ]
        sel = select_capital_integralizado(rows)
        assert sel.quality_flag == "estimated"
        assert "Subscrito" in (sel.quality_reason or "")

    def test_fallback_para_emitido_quando_so_ha_emitido(self):
        # Vale 2023-2024: capital_social so traz 'Capital Emitido'.
        rows = [_cap_row("Capital Emitido", "4539007568", "12", "4539007580")]
        sel = select_capital_integralizado(rows)
        assert sel.quality_flag == "estimated"
        assert sel.row["Quantidade_Acoes_Ordinarias"] == "4539007568"

    def test_duas_integralizado_mesmas_quantidades_escolhe_aprovacao_recente(self):
        rows = [
            _cap_row("Capital Integralizado", "744", "560", "1304", aprov="2013-04-29"),
            _cap_row("Capital Integralizado", "744", "560", "1304", aprov="2014-04-02"),
        ]
        sel = select_capital_integralizado(rows)
        assert sel.quality_flag == "ok"
        assert sel.row["Data_Autorizacao_Aprovacao"] == "2014-04-02"

    def test_duas_integralizado_quantidades_divergentes_e_inconsistent(self):
        rows = [
            _cap_row("Capital Integralizado", "744", "560", "1304", aprov="2013-04-29"),
            _cap_row("Capital Integralizado", "999", "560", "1559", aprov="2014-04-02"),
        ]
        sel = select_capital_integralizado(rows)
        assert sel.quality_flag == "inconsistent"
        assert sel.row["Data_Autorizacao_Aprovacao"] == "2014-04-02"


class TestBuildShareCountRows:
    def test_happy_path_tres_classes(self):
        result = build_share_count_rows(
            reference_date_raw="2024-12-31",
            version_raw="28",
            metadata_row=_index_row("2024-07-25"),
            capital_rows=[
                _cap_row("Capital Emitido", "7442231382", "5446501379", "12888732761"),
                _cap_row("Capital Integralizado", "7442231382", "5446501379", "12888732761"),
            ],
            distribuicao_row=_distribuicao_row(),
            source_file="data/raw/cvm/fre/fre_cia_aberta_2024.zip",
            run_id=1,
        )
        assert not result.warnings
        by_class = {r["share_class"]: r for r in result.rows}
        assert set(by_class) == {"ON", "PN", "TOTAL"}

        on = by_class["ON"]
        assert on["shares_issued"] == Decimal("7442231382")
        assert on["free_float_shares"] == Decimal("3483155534")
        assert on["treasury_shares"] is None
        assert on["shares_outstanding"] is None
        assert on["quality_flag"] == "ok"
        # DT_RECEB 2024-07-25 -> available_from no fim do dia, BRT (-03:00)
        assert on["available_from"] == datetime(2024, 7, 25, 23, 59, 59, tzinfo=BRT)

        assert by_class["TOTAL"]["shares_issued"] == Decimal("12888732761")
        assert by_class["PN"]["free_float_shares"] == Decimal("4410955873")

    def test_sem_documento_no_indice_available_from_nulo_e_avisa(self):
        result = build_share_count_rows(
            reference_date_raw="2024-12-31",
            version_raw="28",
            metadata_row=None,
            capital_rows=[_cap_row("Capital Integralizado", "10", "0", "10")],
            distribuicao_row=None,
            source_file="x",
            run_id=None,
        )
        assert any("sem documento no indice" in w for w in result.warnings)
        assert all(r["available_from"] is None for r in result.rows)

    def test_sem_distribuicao_free_float_none(self):
        result = build_share_count_rows(
            reference_date_raw="2024-12-31",
            version_raw="28",
            metadata_row=_index_row(),
            capital_rows=[_cap_row("Capital Integralizado", "10", "0", "10")],
            distribuicao_row=None,
            source_file="x",
            run_id=None,
        )
        assert all(r["free_float_shares"] is None for r in result.rows)

    def test_capital_ausente_marca_missing_input_por_classe(self):
        result = build_share_count_rows(
            reference_date_raw="2024-12-31",
            version_raw="28",
            metadata_row=_index_row(),
            capital_rows=[_cap_row("Capital Autorizado", "10", "0", "10")],  # nenhum tipo util
            distribuicao_row=None,
            source_file="x",
            run_id=None,
        )
        assert all(r["quality_flag"] == "missing_input" for r in result.rows)
        assert all(r["shares_issued"] is None for r in result.rows)

    def test_fallback_emitido_marca_estimated_e_preenche_issued(self):
        result = build_share_count_rows(
            reference_date_raw="2024-12-31",
            version_raw="23",
            metadata_row=_index_row(),
            capital_rows=[_cap_row("Capital Emitido", "4539007568", "12", "4539007580")],
            distribuicao_row=None,
            source_file="x",
            run_id=None,
        )
        by_class = {r["share_class"]: r for r in result.rows}
        assert by_class["ON"]["shares_issued"] == Decimal("4539007568")
        assert all(r["quality_flag"] == "estimated" for r in result.rows)

    def test_reference_date_invalida_nao_gera_linha(self):
        result = build_share_count_rows(
            reference_date_raw="lixo",
            version_raw="",
            metadata_row=None,
            capital_rows=[],
            distribuicao_row=None,
            source_file="x",
            run_id=None,
        )
        assert result.rows == []
        assert result.warnings


class TestPointInTimeHelpers:
    def test_dedupe_key_sem_company_id(self):
        row = {"share_class": "ON", "reference_date": "2024-12-31", "version": "28", "x": 1}
        assert dedupe_key(row) == ("ON", "2024-12-31", "28")

    def test_latest_available_respeita_as_of(self):
        tz = BRT
        rows = [
            {"version": "1", "available_from": datetime(2024, 5, 29, 23, 59, 59, tzinfo=tz)},
            {"version": "28", "available_from": datetime(2024, 7, 25, 23, 59, 59, tzinfo=tz)},
        ]
        as_of = datetime(2024, 6, 30, tzinfo=UTC)
        chosen = latest_available(rows, as_of=as_of)
        assert chosen["version"] == "1"

    def test_latest_available_ignora_available_from_nulo(self):
        rows = [{"version": "5", "available_from": None}]
        as_of = datetime(2030, 1, 1, tzinfo=UTC)
        assert latest_available(rows, as_of=as_of) is None
