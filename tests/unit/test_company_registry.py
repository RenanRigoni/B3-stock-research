"""Testes de resolucao ticker -> CNPJ/codigo CVM (fase1.md 52).

``parse_registry`` roda contra o fixture real (recorte de
``cad_cia_aberta.csv``); ``resolve_mapping`` e testado com dados sinteticos
para cobrir os caminhos de decisao sem depender do conteudo exato do fixture.
"""

from __future__ import annotations

from pathlib import Path

from stock_research.sources.fundamentals.company_registry import (
    normalize_legal_name,
    parse_registry,
    resolve_mapping,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "cvm" / "cad_cia_aberta_sample.csv"

REGISTRY_ROWS = [
    {"CNPJ_CIA": "33.000.167/0001-01", "DENOM_SOCIAL": "PETRÓLEO BRASILEIRO S.A. - PETROBRAS",
     "CD_CVM": "009512", "SIT": "ATIVO"},
    {"CNPJ_CIA": "33.592.510/0001-54", "DENOM_SOCIAL": "VALE S.A.", "CD_CVM": "004170", "SIT": "ATIVO"},
    {"CNPJ_CIA": "11.111.111/0001-11", "DENOM_SOCIAL": "VALE S.A. EM LIQUIDACAO EXTINTA",
     "CD_CVM": "999999", "SIT": "CANCELADA"},
]


class TestNormalizeLegalName:
    def test_remove_acentos_e_pontuacao(self):
        assert normalize_legal_name("Petróleo Brasileiro S.A. - Petrobras") == "PETROLEO BRASILEIRO S A PETROBRAS"

    def test_variacoes_de_grafia_convergem(self):
        a = normalize_legal_name("PETROLEO BRASILEIRO S.A. PETROBRAS")
        b = normalize_legal_name("PETRÓLEO BRASILEIRO S.A. - PETROBRAS")
        assert a == b


class TestParseRegistryFixture:
    def test_le_o_cadastro_real_recortado(self):
        rows = parse_registry(FIXTURE)
        assert len(rows) > 0
        cnpjs = {r["CNPJ_CIA"] for r in rows}
        assert "33.000.167/0001-01" in cnpjs  # Petrobras
        assert "33.592.510/0001-54" in cnpjs  # Vale


class TestResolveMapping:
    def test_resolve_por_razao_social_quando_nao_ha_identificador_forte(self):
        companies = [{"ticker": "PETR4", "legal_name": "PETROLEO BRASILEIRO S.A. PETROBRAS", "company_name": "Petrobras"}]
        new_mapping, _notes = resolve_mapping(companies, current_mapping={}, registry_rows=REGISTRY_ROWS)
        assert new_mapping["PETR4"]["cnpj"] == "33.000.167/0001-01"
        assert new_mapping["PETR4"]["resolved_by"] == "legal_name"
        assert new_mapping["PETR4"]["confirmed"] is False

    def test_prefere_ativo_sobre_cancelada_com_nome_parecido(self):
        companies = [{"ticker": "VALE3", "legal_name": "VALE S.A.", "company_name": "Vale"}]
        new_mapping, _ = resolve_mapping(companies, current_mapping={}, registry_rows=REGISTRY_ROWS)
        assert new_mapping["VALE3"]["cnpj"] == "33.592.510/0001-54"

    def test_entrada_confirmada_nunca_e_sobrescrita(self):
        companies = [{"ticker": "PETR4", "legal_name": "NOME TOTALMENTE DIFERENTE", "company_name": "X"}]
        current = {"PETR4": {"cnpj": "00.000.000/0000-00", "cvm_code": "1", "legal_name": "MANUAL",
                              "isin": None, "resolved_by": "manual", "confirmed": True, "confirmed_at": "2024-01-01"}}
        new_mapping, notes = resolve_mapping(companies, current, REGISTRY_ROWS)
        assert new_mapping["PETR4"] == current["PETR4"]
        assert any("confirmado" in n for n in notes)

    def test_cnpj_ja_salvo_reancora_no_cadastro_sem_usar_nome(self):
        companies = [{"ticker": "PETR4", "legal_name": "QUALQUER COISA", "company_name": "X"}]
        current = {"PETR4": {"cnpj": "33.000.167/0001-01", "cvm_code": None, "legal_name": None,
                              "isin": None, "resolved_by": None, "confirmed": False, "confirmed_at": None}}
        new_mapping, _ = resolve_mapping(companies, current, REGISTRY_ROWS)
        assert new_mapping["PETR4"]["resolved_by"] == "cnpj"
        assert new_mapping["PETR4"]["cvm_code"] == "009512"

    def test_sem_match_confiavel_fica_nulo_nunca_inventa(self):
        companies = [{"ticker": "XYZW1", "legal_name": "EMPRESA COMPLETAMENTE INEXISTENTE ZZZ", "company_name": "X"}]
        new_mapping, notes = resolve_mapping(companies, current_mapping={}, registry_rows=REGISTRY_ROWS)
        assert new_mapping["XYZW1"]["cnpj"] is None
        assert new_mapping["XYZW1"]["resolved_by"] is None
        assert any("nao resolvido" in n for n in notes)

    def test_isin_nunca_e_inventado(self):
        # CVM nao publica ISIN no cadastro de companhias -- ver company_registry.py.
        companies = [{"ticker": "PETR4", "legal_name": "PETROLEO BRASILEIRO S.A. PETROBRAS", "company_name": "X"}]
        new_mapping, _ = resolve_mapping(companies, current_mapping={}, registry_rows=REGISTRY_ROWS)
        assert new_mapping["PETR4"]["isin"] is None
