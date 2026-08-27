"""Testes de ``sources/macro/tesouro`` (parse) e ``transforms/risk_free`` (regra)."""

from __future__ import annotations

from datetime import date

from stock_research.sources.macro.tesouro import (
    PrefixadoQuote,
    _parse_br_date,
    _parse_br_decimal,
)
from stock_research.transforms.risk_free import compute_risk_free, select_bond


class TestParsers:
    def test_data_br(self):
        assert _parse_br_date("02/01/2026") == date(2026, 1, 2)
        assert _parse_br_date("lixo") is None

    def test_decimal_br_vira_fracao(self):
        assert abs(_parse_br_decimal("13,72") - 0.1372) < 1e-9
        assert abs(_parse_br_decimal("1.234,50") - 12.345) < 1e-9
        assert _parse_br_decimal("") is None


def _q(tipo, mat, base, compra, venda):
    return PrefixadoQuote(tipo, date.fromisoformat(mat), date.fromisoformat(base), compra, venda)


class TestSelectBond:
    def _quotes(self):
        return [
            _q("Tesouro Prefixado", "2029-01-01", "2026-01-02", 0.131, 0.132),
            _q("Tesouro Prefixado com Juros Semestrais", "2035-01-01", "2026-01-02", 0.136, 0.137),
            _q("Tesouro Prefixado com Juros Semestrais", "2033-01-01", "2026-01-02", 0.135, 0.136),
            _q("Tesouro Prefixado", "2036-06-01", "2026-01-02", 0.138, 0.139),
        ]

    def test_escolhe_maturidade_mais_proxima_de_10a(self):
        b = select_bond(self._quotes(), date(2026, 8, 27))
        # de 2026-01-02: 2035 ~9a, 2036-06 ~10,4a -> 2036-06 é o mais próximo de 10
        assert b.maturity == date(2036, 6, 1)

    def test_ignora_cotacao_com_data_base_futura(self):
        future = [_q("Tesouro Prefixado", "2036-01-01", "2027-01-01", 0.14, 0.14)]
        assert select_bond(future, date(2026, 8, 27)) is None

    def test_pega_a_data_base_mais_recente(self):
        qs = [
            _q("Tesouro Prefixado", "2036-01-01", "2025-01-02", 0.10, 0.10),
            _q("Tesouro Prefixado", "2036-01-01", "2026-01-02", 0.13, 0.13),
        ]
        b = select_bond(qs, date(2026, 8, 27))
        assert b.base_date == date(2026, 1, 2)


class TestComputeRiskFree:
    def _quotes(self):
        return [
            _q("Tesouro Prefixado com Juros Semestrais", "2036-01-01", "2026-01-02", 0.136, 0.140),
        ]

    def test_subtrai_o_default_spread(self):
        r = compute_risk_free(self._quotes(), date(2026, 8, 27), country_default_spread=0.021275)
        # yield médio = 0,138 ; risk-free = 0,138 - 0,021275 = 0,116725
        assert r["government_yield"] == 0.138
        assert abs(r["risk_free_rate"] - 0.116725) < 1e-9
        assert r["quality_flag"] == "ok"

    def test_sem_default_spread_usa_yield_bruto_com_flag_estimated(self):
        r = compute_risk_free(self._quotes(), date(2026, 8, 27), country_default_spread=None)
        assert r["risk_free_rate"] == 0.138
        assert r["quality_flag"] == "estimated"

    def test_sem_cotacao_e_missing_input(self):
        r = compute_risk_free([], date(2026, 8, 27), country_default_spread=0.02)
        assert r["risk_free_rate"] is None
        assert r["quality_flag"] == "missing_input"
