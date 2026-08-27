"""Fallback de capex para a linha combinada da VALE3 (fase2_plan §35)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from stock_research.analytics.fundamentals_metrics import compute_metrics_for_facts

AF = datetime(2026, 3, 6, tzinfo=UTC)


def _f(stmt, code, desc, value, *, period_start=date(2025, 1, 1)):
    return {
        "fact_id": 1,
        "document_id": 7,
        "document_type": "DFP",
        "statement_type": stmt,
        "reference_date": date(2025, 12, 31),
        "period_start": period_start,
        "period_end": date(2025, 12, 31),
        "version": "1",
        "account_code": code,
        "account_description": desc,
        "value": Decimal(value),
        "scale": 1000,
        "fiscal_year_order": "ULTIMO",
        "available_from": AF,
    }


def _base():
    return [
        _f("DRE", "3.01", "Receita de Venda de Bens e/ou Serviços", 200_000_000),
        _f("DRE", "3.11", "Lucro/Prejuízo Consolidado do Período", 20_000_000),
        _f("DFC_MI", "6.01", "Caixa Líquido Atividades Operacionais", 60_000_000),
    ]


def _run(facts):
    rows = compute_metrics_for_facts(facts, instrument_id=3, financial_company=False)
    return {r["metric_name"]: r for r in rows}


def test_linha_pura_de_imobilizado_e_ok():
    facts = [*_base(), _f("DFC_MI", "6.02.05", "Adições ao Imobilizado", -30_000_000)]
    m = _run(facts)
    assert m["capex"]["quality_flag"] == "ok"
    assert m["free_cash_flow"]["quality_flag"] == "ok"


def test_typo_da_cvm_ainda_e_capex_ok():
    facts = [*_base(), _f("DFC_MI", "6.02.05", "Adilções ao imobilizado", -30_000_000)]
    m = _run(facts)
    assert m["capex"]["quality_flag"] == "ok"
    assert m["capex"]["metric_value"] == Decimal(-30_000_000 * 1000)


def test_linha_combinada_e_estimated_e_propaga_pro_fcf():
    facts = [
        *_base(),
        _f("DFC_MI", "6.02.04", "Adições ao Imobilizado e investimentos", -35_000_000),
    ]
    m = _run(facts)
    assert m["capex"]["quality_flag"] == "estimated"
    assert "combinada" in m["capex"]["quality_reason"]
    assert m["free_cash_flow"]["quality_flag"] == "estimated"


def test_sem_nenhuma_linha_de_capex_e_missing():
    m = _run(_base())
    assert m["capex"]["quality_flag"] == "missing_input"
    assert m["free_cash_flow"]["quality_flag"] == "missing_input"
