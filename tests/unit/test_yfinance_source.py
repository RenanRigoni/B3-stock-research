"""Conversao ticker -> simbolo Yahoo (fase1.md 9, 75). Offline, sem rede."""

from __future__ import annotations

import pytest

from stock_research.sources.prices.yfinance_source import ticker_to_yahoo_symbol


class TestTickerParaYahoo:
    @pytest.mark.parametrize(
        ("ticker", "expected"),
        [
            ("PETR4", "PETR4.SA"),
            ("petr4", "PETR4.SA"),
            ("VALE3", "VALE3.SA"),
            ("ITUB4", "ITUB4.SA"),
        ],
    )
    def test_acao_b3_ganha_sufixo_sa(self, ticker, expected):
        assert ticker_to_yahoo_symbol(ticker) == expected

    def test_indice_com_circunflexo_passa_direto(self):
        assert ticker_to_yahoo_symbol("^BVSP") == "^BVSP"

    def test_simbolo_ja_sufixado_nao_duplica_sufixo(self):
        assert ticker_to_yahoo_symbol("PETR4.SA") == "PETR4.SA"

    def test_exchange_diferente_de_b3_nao_recebe_sufixo(self):
        assert ticker_to_yahoo_symbol("AAPL", exchange="NASDAQ") == "AAPL"
