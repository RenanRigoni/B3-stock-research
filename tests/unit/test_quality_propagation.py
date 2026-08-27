"""Propagação de qualidade no valuation (fase2_plan §36).

Regra: número que veio de PREMISSA (delta_WC assumido 0, capex de linha
combinada, custo de dívida no piso, payout default) nunca pode sair com
``quality_flag='ok'`` no ``valuation_snapshot``.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from stock_research.analytics.dcf import compute_dcf, compute_dcf_scenarios
from stock_research.analytics.residual_income import compute_residual_income
from stock_research.analytics.wacc import compute_wacc
from stock_research.pipelines import valuation_dcf as vd

DCF_BASE = dict(
    fcff_start=90_000_000_000.0,
    wacc=0.15,
    terminal_growth=0.045,
    net_debt=340_000_000_000.0,
    shares=12_888_732_761.0,
    market_price_per_share=40.0,
)

WACC_BASE = dict(
    risk_free_nominal_brl=0.1237,
    beta=1.03,
    mature_market_erp=0.0423,
    country_risk_premium=0.0324,
    pretax_cost_of_debt=0.1237,
    tax_rate=0.27,
    market_cap=550_000_000_000.0,
    gross_debt=360_000_000_000.0,
)


class TestMergeQuality:
    def test_pior_flag_vence(self):
        flag, reason = vd._merge_quality(("ok", "a"), ("estimated", "b"))
        assert flag == "estimated"
        assert reason == "b"

    def test_missing_input_domina_estimated(self):
        flag, _ = vd._merge_quality(("estimated", "b"), ("missing_input", "c"))
        assert flag == "missing_input"

    def test_tudo_ok_nao_gera_motivo(self):
        assert vd._merge_quality(("ok", "a"), ("ok", "b")) == ("ok", None)

    def test_concatena_motivos_degradantes(self):
        _, reason = vd._merge_quality(("estimated", "x"), ("estimated", "y"))
        assert reason == "x; y"


class TestDcfPropaga:
    def test_dcf_com_insumo_estimated_nao_sai_ok(self):
        r = compute_dcf(
            forecast_growth=0.04,
            input_quality_flag="estimated",
            input_quality_reason="deltaWC ASSUMIDO 0",
            **DCF_BASE,
        )
        assert r["fair_value_per_share"] is not None  # número não muda
        assert r["quality_flag"] == "estimated"
        assert "deltaWC" in r["quality_reason"]

    def test_insumo_ok_continua_ok(self):
        assert compute_dcf(forecast_growth=0.04, **DCF_BASE)["quality_flag"] == "ok"

    def test_todos_os_cenarios_herdam_o_flag(self):
        scen = {"pessimista": {"forecast_growth": 0.0}, "base": {"forecast_growth": 0.04}}
        out = compute_dcf_scenarios(
            scenarios=scen,
            input_quality_flag="estimated",
            input_quality_reason="premissa",
            **DCF_BASE,
        )
        assert all(v["quality_flag"] == "estimated" for v in out.values())


class TestWaccPropaga:
    def test_cost_of_debt_estimated_degrada_o_wacc(self):
        r = compute_wacc(
            cost_of_debt_quality_flag="estimated",
            cost_of_debt_quality_reason="piso do risk-free",
            **WACC_BASE,
        )
        assert r["wacc"] is not None
        assert r["quality_flag"] == "estimated"
        assert "piso" in r["quality_reason"]

    def test_sem_degradacao_continua_ok(self):
        assert compute_wacc(**WACC_BASE)["quality_flag"] == "ok"


class TestResidualIncomePropaga:
    def test_payout_assumido_degrada(self):
        r = compute_residual_income(
            equity_start=200_000_000_000.0,
            net_income_start=40_000_000_000.0,
            coe=0.20,
            net_income_growth=0.05,
            terminal_growth=0.045,
            payout_ratio=0.5,
            shares=10_000_000_000.0,
            market_price_per_share=35.0,
            input_quality_flag="estimated",
            input_quality_reason="payout_ratio ASSUMIDO 0,5",
        )
        assert r["fair_value_per_share"] is not None
        assert r["quality_flag"] == "estimated"


# --- custo de dívida: zero declarado não é observação (§36) -----------------


def _dre(code: str, desc: str, value_bi: float) -> dict:
    return {
        "statement_type": "DRE",
        "account_code": code,
        "account_description": desc,
        "value": value_bi * 1e6,
        "scale": 1000,
        "document_id": 1,
    }


_GROSS_DEBT = 100e9
_RF = 0.05  # baixo de propósito: não aciona o piso, para isolar o caminho da conta


class TestCostOfDebtQuality:
    def _run(self, monkeypatch, facts):
        monkeypatch.setattr(vd, "fetch_all", lambda *a, **k: facts)
        return vd._financial_expense_over_debt(1, date(2026, 8, 27), _GROSS_DEBT, _RF)

    def test_juros_puros_sao_ok(self, monkeypatch):
        facts = [
            _dre("3.06.02", "Despesas Financeiras", -10.0),
            _dre("3.06.02.01", "Despesas financeiras", -8.0),
        ]
        rate, reason, flag = self._run(monkeypatch, facts)
        assert flag == "ok"
        assert abs(rate - 0.08) < 1e-9
        assert "3.06.02.01" in reason

    def test_juros_puros_zerados_caem_no_nivel_2_como_estimated(self, monkeypatch):
        # caso real da VALE3: linha declarada com valor zero
        facts = [
            _dre("3.06.02", "Despesas Financeiras", -8.0),
            _dre("3.06.02.01", "Despesas financeiras", 0.0),
        ]
        rate, reason, flag = self._run(monkeypatch, facts)
        assert flag == "estimated"
        assert abs(rate - 0.08) < 1e-9  # usa o nível 2, não zero
        assert "valor zero" in reason

    def test_tudo_zerado_e_missing_input_nunca_zero(self, monkeypatch):
        facts = [
            _dre("3.06.02", "Despesas Financeiras", 0.0),
            _dre("3.06.02.01", "Despesas financeiras", 0.0),
        ]
        rate, reason, flag = self._run(monkeypatch, facts)
        assert rate is None
        assert flag == "missing_input"
        assert "zeradas" in reason

    def test_piso_do_risk_free_marca_estimated(self, monkeypatch):
        facts = [_dre("3.06.02.01", "Despesas financeiras", -1.0)]  # 1% << risk-free
        rate, reason, flag = self._run(monkeypatch, facts)
        assert flag == "estimated"
        assert rate == _RF
        assert "SUBSTITUÍDO" in reason


# --- _fcff_avg: a regra obrigatória, com o banco stubado --------------------


def _row(year: int, value: float, flag: str = "ok"):
    return {
        "reference_date": date(year, 12, 31),
        "metric_value": value,
        "quality_flag": flag,
        "available_from": datetime(year + 1, 3, 31, tzinfo=UTC),
    }


class _Stub:
    """Substitui ``_latest_annual`` por um dicionário métrica -> linhas."""

    def __init__(self, data: dict[str, list[dict]]):
        self.data = data

    def __call__(self, instrument_id, metric, calc_version, as_of):
        return self.data.get(metric, [])


def _core() -> dict[str, list[dict]]:
    return {
        "nopat": [_row(y, 100.0) for y in (2025, 2024, 2023)],
        "da": [_row(y, 60.0) for y in (2025, 2024, 2023)],
        "capex": [_row(y, -50.0) for y in (2025, 2024, 2023)],
    }


class TestFcffAvgQuality:
    def _run(self, monkeypatch, data):
        monkeypatch.setattr(vd, "_latest_annual", _Stub(data))
        return vd._fcff_avg(1, date(2026, 8, 27))

    def test_com_working_capital_completo_sai_ok(self, monkeypatch):
        data = {**_core(), "working_capital": [_row(y, 10.0) for y in range(2022, 2026)]}
        value, years, reason, flag = self._run(monkeypatch, data)
        assert flag == "ok"
        assert years == [2025, 2024, 2023]
        assert value == 110.0  # 100 + 60 - 50, deltaWC = 0 observado
        assert "ASSUMIDO" not in reason

    def test_working_capital_ausente_e_estimated_com_motivo_explicito(self, monkeypatch):
        value, _years, reason, flag = self._run(monkeypatch, _core())
        assert flag == "estimated"
        assert value == 110.0  # número igual: deltaWC=0
        assert "ASSUMIDO 0" in reason
        assert "suposição, não dado observado" in reason
        assert "[2023, 2024, 2025]" in reason

    def test_working_capital_parcial_lista_so_os_anos_sem_ajuste(self, monkeypatch):
        # WC só em 2024/2025 -> 2025 tem delta (usa 2024), 2024 e 2023 não têm
        data = {**_core(), "working_capital": [_row(y, 10.0) for y in (2025, 2024)]}
        _v, _y, reason, flag = self._run(monkeypatch, data)
        assert flag == "estimated"
        assert "[2023, 2024]" in reason

    def test_capex_estimated_propaga_para_o_fcff(self, monkeypatch):
        data = {
            **_core(),
            "capex": [_row(2025, -50.0, "estimated"), _row(2024, -50.0), _row(2023, -50.0)],
            "working_capital": [_row(y, 10.0) for y in range(2022, 2026)],
        }
        _v, _y, reason, flag = self._run(monkeypatch, data)
        assert flag == "estimated"
        assert "capex 2025" in reason

    def test_poucos_anos_e_missing_input(self, monkeypatch):
        data = {k: v[:1] for k, v in _core().items()}
        value, _y, _r, flag = self._run(monkeypatch, data)
        assert value is None
        assert flag == "missing_input"
