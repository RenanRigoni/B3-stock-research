"""Transforms puros do universo historico + validacao de schema + reversibilidade
das migrations. Cobre os itens 12, 14, 15 e 17 do Handoff v2 §15.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

import pytest

from stock_research.config import project_root
from stock_research.sources.fundamentals import cvm_fca
from stock_research.sources.fundamentals.base import CvmSchemaError
from stock_research.sources.fundamentals.cvm_common import validate_columns
from stock_research.transforms.company_lifecycle import (
    build_company_lifecycle,
    reason_category,
    registration_status_from_sit,
)
from stock_research.transforms.instrument_lifecycle import (
    build_instrument_candidate,
    classify_security,
    merge_instrument_intervals,
    normalize_market,
)

_OBS = datetime(2026, 8, 30, tzinfo=UTC)


# ===========================================================================
# 15.3 #14 -- company_lifecycle
# ===========================================================================


def _cad(**over):
    base = {
        "CNPJ_CIA": "11.111.111/0001-11",
        "DENOM_SOCIAL": "EMPRESA TESTE SA",
        "CD_CVM": "12345",
        "DT_REG": "2005-04-10",
        "DT_CONST": "2004-01-01",
        "DT_CANCEL": "",
        "MOTIVO_CANCEL": "",
        "SIT": "ATIVO",
        "SIT_EMISSOR": "EM FUNCIONAMENTO NORMAL",
        "SETOR_ATIV": "Petroleo",
    }
    base.update(over)
    return base


def test_14_registration_simple_is_one_row_valid_to_null():
    b = build_company_lifecycle(_cad(), source_observed_at=_OBS, run_id=1)
    row = b.lifecycle_row
    assert row["valid_from"] == date(2005, 4, 10)
    assert row["valid_to"] is None
    assert row["event_type"] == "registration"
    assert row["registration_status"] == "registered"
    assert row["reason"] is None and row["reason_category"] is None


def test_14_cancellation_maps_reason_and_event_type():
    b = build_company_lifecycle(
        _cad(SIT="CANCELADA", DT_CANCEL="2015-12-18", MOTIVO_CANCEL="ELISAO POR INCORPORACAO"),
        source_observed_at=_OBS,
        run_id=1,
    )
    row = b.lifecycle_row
    assert row["valid_to"] == date(2015, 12, 18)
    assert row["event_type"] == "cancellation"
    assert row["registration_status"] == "canceled"
    assert row["reason"] == "ELISAO POR INCORPORACAO"
    assert row["reason_category"] == "incorporation"


def test_14_cancel_before_reg_is_flagged_inconsistent_not_dropped():
    b = build_company_lifecycle(
        _cad(SIT="CANCELADA", DT_REG="2010-01-01", DT_CANCEL="2005-01-01",
             MOTIVO_CANCEL="CANCELAMENTO VOLUNTARIO"),
        source_observed_at=_OBS,
        run_id=1,
    )
    assert b.lifecycle_row["quality_flag"] == "inconsistent"
    assert b.lifecycle_row["valid_to"] == b.lifecycle_row["valid_from"]  # colapsado, nunca invertido


def test_14_reason_category_table():
    assert reason_category("INCORPORACAO DE ACOES") == "incorporation"
    assert reason_category("FALENCIA DECRETADA") == "bankruptcy_liquidation"
    assert reason_category("Cancelamento voluntario") == "voluntary_delisting"
    assert reason_category("ATENDIMENTO AS NORMAS DA INSTR CVM 03/78") == "regulatory"
    assert reason_category("motivo esquisito qualquer") == "other"
    assert reason_category("") is None


def test_14_registration_status_from_sit():
    assert registration_status_from_sit("ATIVO") == "registered"
    assert registration_status_from_sit("SUSPENSO(A) - DECISAO ADM") == "suspended"
    assert registration_status_from_sit("CANCELADA") == "canceled"


def test_company_without_dt_reg_falls_back_to_dt_const_estimated():
    b = build_company_lifecycle(
        _cad(DT_REG="", DT_CONST="2004-01-01"), source_observed_at=_OBS, run_id=1
    )
    assert b.lifecycle_row["valid_from"] == date(2004, 1, 1)
    assert b.lifecycle_row["quality_flag"] == "estimated"


def test_company_provenance_never_a_gate():
    b = build_company_lifecycle(_cad(), source_observed_at=_OBS, run_id=1)
    # source_available_from fica NULL (cadastro nao informa) e isso e VALIDO:
    # nao e usado como filtro em lugar nenhum.
    assert b.lifecycle_row["source_available_from"] is None
    assert b.lifecycle_row["source_observed_at"] == _OBS


# ===========================================================================
# 15.3 #12 -- ticker history (merge de intervalos da FCA)
# ===========================================================================


def _vm(**over):
    base = {
        "CNPJ_Companhia": "22.222.222/0001-22",
        "Data_Referencia": "2015-01-01",
        "Versao": "1",
        "Valor_Mobiliario": "Acoes Ordinarias",
        "Sigla_Classe_Acao_Preferencial": "",
        "Classe_Acao_Preferencial": "",
        "Codigo_Negociacao": "",
        "Mercado": "Bolsa",
        "Sigla_Entidade_Administradora": "BM&FBOVESPA",
        "Data_Inicio_Negociacao": "2005-06-01",
        "Data_Fim_Negociacao": "",
        "Data_Inicio_Listagem": "2005-06-01",
        "Data_Fim_Listagem": "",
        "Segmento": "Novo Mercado",
    }
    base.update(over)
    return base


def test_12_different_code_across_years_makes_two_intervals_old_not_rewritten():
    y2018 = build_instrument_candidate(
        _vm(Data_Referencia="2018-01-01", Codigo_Negociacao="OLDX3"),
        reference_year=2018, source_available_from=None, source_observed_at=_OBS, run_id=1,
    ).row
    y2020 = build_instrument_candidate(
        _vm(Data_Referencia="2020-01-01", Codigo_Negociacao="NEWX3"),
        reference_year=2020, source_available_from=None, source_observed_at=_OBS, run_id=1,
    ).row
    merged = merge_instrument_intervals([y2018, y2020])
    tickers = sorted(r["ticker"] for r in merged)
    assert tickers == ["NEWX3", "OLDX3"]  # o antigo NAO virou o novo


def test_12_same_code_consecutive_years_merge_into_one_interval():
    cands = [
        build_instrument_candidate(
            _vm(Data_Referencia=f"{y}-01-01", Codigo_Negociacao="SAME3"),
            reference_year=y, source_available_from=None, source_observed_at=_OBS, run_id=1,
        ).row
        for y in (2018, 2019, 2020)
    ]
    merged = merge_instrument_intervals(cands)
    assert len(merged) == 1
    assert merged[0]["source_reference_year"] == 2020  # conhecimento mais recente


def test_12_ticker_null_pre_2018_is_incomplete_not_ok():
    cand = build_instrument_candidate(
        _vm(Data_Referencia="2013-01-01", Codigo_Negociacao=""),
        reference_year=2013, source_available_from=None, source_observed_at=_OBS, run_id=1,
    )
    assert cand.row["ticker"] is None
    assert cand.row["quality_flag"] == "incomplete"


def test_classify_security_equity_only():
    assert classify_security("Acoes Ordinarias", "") == "ON"
    assert classify_security("Acoes Preferenciais", "Classe A") == "PNA"
    assert classify_security("Acoes Preferenciais", "") == "PN"
    assert classify_security("Units", "") == "UNT"
    assert classify_security("Debentures", "") is None
    assert classify_security("Nota Comercial", "") is None
    assert classify_security("Bonus de Subscricao", "") is None


def test_normalize_market():
    assert normalize_market("Bolsa") == "bolsa"
    assert normalize_market("Balcao Organizado") == "balcao_organizado"
    assert normalize_market("Balcao Nao-Organizado") == "balcao_nao_organizado"
    assert normalize_market("qualquer outra coisa") is None


def test_merge_is_deterministic_and_idempotent():
    cands = [
        build_instrument_candidate(
            _vm(Data_Referencia=f"{y}-01-01", Codigo_Negociacao="X3"),
            reference_year=y, source_available_from=None, source_observed_at=_OBS, run_id=1,
        ).row
        for y in (2019, 2018, 2020)
    ]
    a = merge_instrument_intervals(list(cands))
    b = merge_instrument_intervals(list(cands))
    assert a == b


# ===========================================================================
# 15.3 #15 -- validacao de schema (falha dura se a CVM mudar o formato)
# ===========================================================================


def test_15_fca_valor_mobiliario_schema_change_raises():
    good = set(cvm_fca.REQUIRED_COLUMNS_VALOR_MOBILIARIO)
    validate_columns(good | {"coluna_extra_toleravel"}, cvm_fca.REQUIRED_COLUMNS_VALOR_MOBILIARIO,
                     context="teste")  # extra e ok
    with pytest.raises(CvmSchemaError):
        validate_columns(good - {"Data_Inicio_Negociacao"},
                         cvm_fca.REQUIRED_COLUMNS_VALOR_MOBILIARIO, context="teste")


def test_15_fca_index_schema_change_raises():
    with pytest.raises(CvmSchemaError):
        validate_columns({"CNPJ_CIA", "DT_REFER"}, cvm_fca.REQUIRED_COLUMNS_FCA_INDEX,
                         context="teste")


# ===========================================================================
# 15.3 #17 -- migrations aditivas e reversiveis
# ===========================================================================

_MIG_DIR = project_root() / "supabase" / "migrations"
_LIFECYCLE_MIGS = [
    "20260830044424_company_lifecycle.sql",
    "20260830044442_instrument_lifecycle.sql",
]
_FASE12_TABLES = [
    "financial_statement_facts", "cvm_documents", "fundamental_metrics",
    "daily_prices", "daily_returns", "corporate_actions", "share_count_history",
    "valuation_multiples", "quality_scores", "valuation_snapshots",
    "wacc_assumptions", "news_articles", "events",
]


def _strip_sql_comments(sql: str) -> str:
    return "\n".join(line.split("--", 1)[0] for line in sql.splitlines())


def test_17_lifecycle_migrations_only_touch_the_two_new_tables():
    for name in _LIFECYCLE_MIGS:
        body = _strip_sql_comments((_MIG_DIR / name).read_text(encoding="utf-8").lower())
        for tbl in _FASE12_TABLES:
            assert not re.search(
                rf"\b(alter|drop|delete\s+from|update)\s+(table\s+)?(public\.)?{tbl}\b", body
            ), f"{name} mexe em {tbl}"
        assert (
            "create table public.company_lifecycle" in body
            or "create table public.instrument_lifecycle" in body
        )


def test_17_lifecycle_migrations_are_additive_only():
    for name in _LIFECYCLE_MIGS:
        body = _strip_sql_comments((_MIG_DIR / name).read_text(encoding="utf-8").lower())
        # nenhum DROP e nenhum ALTER em objeto pre-existente (o unico ALTER
        # permitido e `alter table public.<nova> enable row level security`).
        assert "drop " not in body
        for m in re.finditer(r"alter\s+table\s+(public\.)?(\w+)", body):
            assert m.group(2) in {"company_lifecycle", "instrument_lifecycle"}
        assert body.count("create table") == 1
