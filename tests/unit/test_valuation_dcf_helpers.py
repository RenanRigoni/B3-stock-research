"""Testes dos helpers puros de ``pipelines/valuation_dcf`` (sem banco)."""

from __future__ import annotations

from datetime import date

from stock_research.pipelines import valuation_dcf as vd


class TestErpSnapshotAsOf:
    def test_pega_snapshot_com_available_from_no_passado(self):
        snap = vd._erp_snapshot_as_of(date(2026, 8, 27))
        assert snap is not None
        assert snap["country_risk_premium"] == 0.032410
        assert snap["mature_market_erp"] == 0.0423

    def test_antes_do_available_from_nao_ha_snapshot(self):
        # o único snapshot do config tem available_from 2026-02-16
        assert vd._erp_snapshot_as_of(date(2025, 12, 31)) is None

    def test_as_date_aceita_str_e_date(self):
        assert vd._as_date("2026-02-16") == date(2026, 2, 16)
        assert vd._as_date(date(2026, 2, 16)) == date(2026, 2, 16)


class TestWaccConfig:
    def test_config_tem_cenarios_e_terminal_growth(self):
        cfg = vd._wacc_config()
        assert cfg["dcf"]["forecast_years"] == 5
        assert set(cfg["dcf"]["scenarios"]) == {"pessimista", "base", "otimista"}
        assert cfg["dcf"]["terminal_growth_nominal"] > 0
