"""Testes de ``analytics/valuation_metrics`` (fase2_plan.md 5-7, 28-29).

Fatos montados no formato real de ``financial_statement_facts`` (consolidado),
com contas e valores calcados no DFP real de PETR4/ITUB4 (§28-29).
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from stock_research.analytics.valuation_metrics import (
    compute_valuation_metrics_for_facts,
)

AF = datetime(2025, 3, 31, 23, 59, 59, tzinfo=UTC)


def _fact(statement_type: str, code: str, desc: str, value, *, order="ULTIMO", flow=True) -> dict:
    return {
        "fact_id": 1,
        "document_id": 10,
        "document_type": "DFP",
        "statement_type": statement_type,
        "reference_date": date(2024, 12, 31),
        "period_start": date(2024, 1, 1) if flow else None,
        "period_end": date(2024, 12, 31) if flow else None,
        "version": "1",
        "account_code": code,
        "account_description": desc,
        "value": Decimal(value),
        "scale": 1000,  # MIL
        "fiscal_year_order": order,
        "available_from": AF,
    }


def _petr4_like_facts() -> list[dict]:
    # valores em MIL (scale=1000); ordem de grandeza tipo PETR4 2024 (bi).
    return [
        _fact("DRE", "3.05", "Resultado Antes do Resultado Financeiro e dos Tributos", 137_000_000),
        _fact("DRE", "3.07", "Resultado Antes dos Tributos sobre o Lucro", 120_000_000),
        _fact("DRE", "3.08", "Imposto de Renda e Contribuição Social sobre o Lucro", -36_000_000),
        _fact("DFC_MI", "6.01.01.04", "Depreciação, Depleção e Amortização", 67_000_000),
        _fact("BPP", "2.03", "Patrimônio Líquido Consolidado", 380_000_000, flow=False),
        _fact("BPA", "1.01.01", "Caixa e Equivalentes de Caixa", 60_000_000, flow=False),
        _fact("BPP", "2.01.04", "Empréstimos e Financiamentos", 90_000_000, flow=False),
        _fact("BPP", "2.02.01", "Empréstimos e Financiamentos", 210_000_000, flow=False),
    ]


def _run(facts, *, financial_company=False):
    rows = compute_valuation_metrics_for_facts(
        facts, instrument_id=2, company_id=1, financial_company=financial_company
    )
    return {r["metric_name"]: r for r in rows}


class TestNonFinancialHappyPath:
    def test_ebitda_ok(self):
        m = _run(_petr4_like_facts())
        assert m["da"]["quality_flag"] == "ok"
        assert m["da"]["metric_value"] == Decimal(67_000_000 * 1000)
        assert m["ebitda"]["quality_flag"] == "ok"
        assert m["ebitda"]["metric_value"] == Decimal((137_000_000 + 67_000_000) * 1000)

    def test_effective_tax_rate(self):
        m = _run(_petr4_like_facts())
        # 36.000.000 / 120.000.000 = 0,30
        assert m["effective_tax_rate"]["metric_value"] == Decimal("0.3")
        assert m["effective_tax_rate"]["unit"] == "ratio"

    def test_nopat_e_roic(self):
        m = _run(_petr4_like_facts())
        # NOPAT = 137bi * (1 - 0,30) = 95,9bi
        assert m["nopat"]["metric_value"] == Decimal("137000000000") * Decimal("0.7")
        # invested_capital = (90+210)bi + 380bi - 60bi = 620bi
        assert m["invested_capital"]["metric_value"] == Decimal(
            str((90 + 210 + 380 - 60) * 1_000_000_000)
        )
        assert m["roic"]["quality_flag"] == "ok"
        assert (
            m["roic"]["metric_value"]
            == m["nopat"]["metric_value"] / m["invested_capital"]["metric_value"]
        )

    def test_period_type_annual_para_dfp(self):
        m = _run(_petr4_like_facts())
        assert all(r["period_type"] == "annual" for r in m.values())


class TestDAFallbackDVA:
    def test_sem_dfc_usa_dva_com_estimated(self):
        facts = [f for f in _petr4_like_facts() if f["statement_type"] != "DFC_MI"]
        facts.append(_fact("DVA", "7.04.01", "Depreciação, Amortização e Exaustão", -67_000_000))
        m = _run(facts)
        assert m["da"]["quality_flag"] == "estimated"
        assert m["da"]["metric_value"] == Decimal(67_000_000 * 1000)  # abs()
        assert m["ebitda"]["quality_flag"] == "estimated"


class TestMissingInputs:
    def test_da_ausente_bloqueia_ebitda(self):
        facts = [f for f in _petr4_like_facts() if f["statement_type"] not in ("DFC_MI", "DVA")]
        m = _run(facts)
        assert m["da"]["quality_flag"] == "missing_input"
        assert m["ebitda"]["quality_flag"] == "missing_input"
        assert m["ebitda"]["metric_value"] is None

    def test_pretax_negativo_bloqueia_tax_rate_e_nopat(self):
        facts = [f for f in _petr4_like_facts() if f["account_code"] != "3.07"]
        facts.append(_fact("DRE", "3.07", "Resultado Antes dos Tributos sobre o Lucro", -5_000_000))
        m = _run(facts)
        assert m["effective_tax_rate"]["quality_flag"] == "missing_input"
        assert m["nopat"]["quality_flag"] == "missing_input"
        assert m["roic"]["quality_flag"] == "missing_input"

    def test_invested_capital_zero_bloqueia_roic(self):
        # dívida 300bi + equity 380bi - caixa 680bi = 0
        facts = [f for f in _petr4_like_facts() if f["account_code"] != "1.01.01"]
        facts.append(
            _fact("BPA", "1.01.01", "Caixa e Equivalentes de Caixa", 680_000_000, flow=False)
        )
        m = _run(facts)
        assert m["invested_capital"]["metric_value"] == Decimal(0)
        assert m["roic"]["quality_flag"] == "missing_input"


class TestBankProfile:
    def test_banco_bloqueia_ebitda_roic_mas_calcula_tax_rate(self):
        facts = _petr4_like_facts()
        m = _run(facts, financial_company=True)
        for name in ("ebitda", "nopat", "invested_capital", "roic"):
            assert m[name]["quality_flag"] == "sector_inadequate"
            assert m[name]["metric_value"] is None
        assert m["effective_tax_rate"]["quality_flag"] == "ok"
        assert m["pretax_income"]["quality_flag"] == "ok"
