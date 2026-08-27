"""Testes de ``analytics/residual_income`` e ``analytics/ddm`` (fase2_plan.md 10)."""

from __future__ import annotations

from stock_research.analytics.ddm import compute_ddm
from stock_research.analytics.residual_income import compute_residual_income, cost_of_equity


class TestCostOfEquity:
    def test_soma_dos_componentes(self):
        coe = cost_of_equity(
            risk_free_nominal_brl=0.1167,
            beta=1.0,
            mature_market_erp=0.0423,
            country_risk_premium=0.03241,
        )
        assert abs(coe - (0.1167 + 0.0423 + 0.03241)) < 1e-12

    def test_componente_faltando_e_none(self):
        assert (
            cost_of_equity(
                risk_free_nominal_brl=0.1,
                beta=None,
                mature_market_erp=0.04,
                country_risk_premium=0.03,
            )
            is None
        )


BANK = dict(
    equity_start=220_000_000_000.0,
    net_income_start=42_000_000_000.0,
    coe=0.19,
    net_income_growth=0.08,
    terminal_growth=0.045,
    payout_ratio=0.6,
    shares=9_800_000_000.0,
    market_price_per_share=40.0,
)


class TestResidualIncome:
    def test_fluxo_consistente(self):
        r = compute_residual_income(**BANK)
        assert r["quality_flag"] == "ok"
        assert len(r["ri_by_year"]) == 5
        # equity_value = equity_start + PV(RI) + PV(terminal)
        assert abs(r["equity_value"] - (220e9 + r["pv_residual_income"] + r["pv_terminal"])) < 1
        assert abs(r["fair_value_per_share"] - r["equity_value"] / 9_800_000_000.0) < 1e-6

    def test_roe_igual_ao_coe_da_valor_proximo_do_patrimonio(self):
        # se NI/Equity == coe e sem crescimento, RI ~ 0 -> fair value ~ equity/shares
        r = compute_residual_income(
            **{
                **BANK,
                "net_income_start": 0.19 * 220e9,
                "net_income_growth": 0.0,
                "terminal_growth": 0.0,
                "payout_ratio": 1.0,
            }
        )
        assert abs(r["fair_value_per_share"] - 220e9 / 9_800_000_000.0) < 0.5

    def test_equity_negativo_e_not_applicable(self):
        r = compute_residual_income(**{**BANK, "equity_start": -1.0})
        assert r["quality_flag"] == "not_applicable"

    def test_coe_nao_acima_do_g_e_not_applicable(self):
        r = compute_residual_income(**{**BANK, "coe": 0.046})
        assert r["quality_flag"] == "not_applicable"

    def test_insumo_faltando_e_missing_input(self):
        r = compute_residual_income(**{**BANK, "coe": None})
        assert r["quality_flag"] == "missing_input"


class TestDDM:
    def test_fluxo_consistente(self):
        r = compute_ddm(
            dividend_ttm_per_share=3.0,
            coe=0.19,
            dividend_growth=0.05,
            terminal_growth=0.045,
            market_price_per_share=40.0,
        )
        assert r["quality_flag"] == "ok"
        assert len(r["dividend_by_year"]) == 5
        assert abs(r["fair_value_per_share"] - (r["pv_explicit"] + r["pv_terminal"])) < 1e-9
        assert (
            abs(
                r["margin_of_safety"]
                - (r["fair_value_per_share"] - 40.0) / r["fair_value_per_share"]
            )
            < 1e-9
        )

    def test_sem_proventos_e_not_applicable(self):
        r = compute_ddm(
            dividend_ttm_per_share=0.0,
            coe=0.19,
            dividend_growth=0.05,
            terminal_growth=0.045,
            market_price_per_share=40.0,
        )
        assert r["quality_flag"] == "not_applicable"

    def test_coe_baixo_e_not_applicable(self):
        r = compute_ddm(
            dividend_ttm_per_share=3.0,
            coe=0.046,
            dividend_growth=0.02,
            terminal_growth=0.045,
            market_price_per_share=40.0,
        )
        assert r["quality_flag"] == "not_applicable"
