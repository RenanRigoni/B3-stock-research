"""Bate na API real do yfinance (fase1.md 76). Nao roda por padrao -- so com
``pytest -m integration``. Exige rede.
"""

from __future__ import annotations

from datetime import date

import pytest

from stock_research.sources.prices.yfinance_source import YFinancePriceSource

pytestmark = pytest.mark.integration


class TestSemanticaDeEnd:
    def test_end_e_exclusivo(self):
        """fase1.md 10: a documentacao diz que `end` e exclusivo -- confirmar contra
        a API real em vez de assumir (fase1.md 107/'nunca assumir comportamento').
        """
        source = YFinancePriceSource()
        result = source.fetch_daily_history("PETR4.SA", date(2024, 1, 2), date(2024, 1, 10))

        dates = sorted(result.frame["trade_date"].tolist())
        assert dates, "yfinance nao devolveu nenhum pregao para a janela de teste"
        assert dates[0] >= date(2024, 1, 2)
        # 2024-01-10 e o `end` pedido: se aparecesse, `end` seria inclusivo.
        assert date(2024, 1, 10) not in dates
        assert dates[-1] < date(2024, 1, 10)

    def test_start_e_inclusivo(self):
        source = YFinancePriceSource()
        result = source.fetch_daily_history("PETR4.SA", date(2024, 1, 2), date(2024, 1, 4))
        dates = sorted(result.frame["trade_date"].tolist())
        # 2024-01-02 foi pregao (terca-feira, sem feriado conhecido).
        assert date(2024, 1, 2) in dates


class TestColunasEsperadas:
    def test_frame_tem_close_e_adj_close_separados(self):
        source = YFinancePriceSource()
        result = source.fetch_daily_history("PETR4.SA", date(2024, 1, 2), date(2024, 1, 6))
        assert "Close" in result.frame.columns
        assert "Adj Close" in result.frame.columns
        row = result.frame.iloc[0]
        assert row["Close"] != row["Adj Close"]
