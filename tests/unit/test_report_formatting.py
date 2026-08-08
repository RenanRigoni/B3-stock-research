"""Testes dos helpers puros de formatacao de ``pipelines/report.py``."""

from __future__ import annotations

from stock_research.pipelines.report import _pct, _return_row


class TestPct:
    def test_valor_none_vira_traco(self):
        assert _pct(None) == "-"

    def test_formata_positivo(self):
        assert _pct(0.0726) == "7.26%"

    def test_formata_negativo(self):
        assert _pct(-0.0914) == "-9.14%"

    def test_aceita_decimal(self):
        from decimal import Decimal

        assert _pct(Decimal("0.05")) == "5.00%"


class TestReturnRow:
    def test_monta_linha_com_valores_ausentes(self):
        row = _return_row({"trade_date": "2026-01-01", "return_1d_adjusted": None, "benchmark_return_1d": None, "excess_return_1d": None})

        assert row == "| 2026-01-01 | - | - | - |"

    def test_monta_linha_com_valores_presentes(self):
        row = _return_row(
            {"trade_date": "2026-01-01", "return_1d_adjusted": 0.05, "benchmark_return_1d": 0.01, "excess_return_1d": 0.04}
        )

        assert row == "| 2026-01-01 | 5.00% | 1.00% | 4.00% |"
