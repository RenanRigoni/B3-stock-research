"""Calculo de retornos diarios (fase1.md 14-16, 75). Puro, offline."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from stock_research.analytics.returns import compute_daily_returns_frame


@pytest.fixture
def price_frame() -> pd.DataFrame:
    dates = [date(2024, 1, d) for d in (2, 3, 4, 5, 8)]  # pula fim de semana
    return pd.DataFrame(
        {
            "trade_date": dates,
            "close": [38.0, 38.5, 37.0, 37.0, 39.0],
            "adj_close": [26.5, 26.8, 25.7, 25.7, 27.1],
            "volume": [1000, 1200, 1500, 0, 1800],
        }
    )


@pytest.fixture
def benchmark_frame() -> pd.DataFrame:
    dates = [date(2024, 1, d) for d in (2, 3, 4, 5, 8)]
    prices = pd.DataFrame({
        "trade_date": dates,
        "close": [120000, 121000, 119000, 119000, 122000],
        "adj_close": [120000, 121000, 119000, 119000, 122000],
        "volume": [0, 0, 0, 0, 0],
    })
    return compute_daily_returns_frame(prices)


class TestRetornoSimples:
    def test_primeira_linha_nao_tem_retorno_anterior(self, price_frame):
        result = compute_daily_returns_frame(price_frame)
        assert pd.isna(result.iloc[0]["return_1d_price"])

    def test_retorno_simples_de_preco(self, price_frame):
        result = compute_daily_returns_frame(price_frame)
        # 38.5 / 38.0 - 1
        assert result.iloc[1]["return_1d_price"] == pytest.approx(38.5 / 38.0 - 1)

    def test_retorno_ajustado_usa_serie_adj_close(self, price_frame):
        result = compute_daily_returns_frame(price_frame)
        assert result.iloc[1]["return_1d_adjusted"] == pytest.approx(26.8 / 26.5 - 1)
        assert result.iloc[1]["return_1d_adjusted"] != result.iloc[1]["return_1d_price"]

    def test_log_return_bate_com_formula(self, price_frame):
        result = compute_daily_returns_frame(price_frame)
        assert result.iloc[1]["log_return_1d"] == pytest.approx(np.log(26.8 / 26.5))

    def test_nenhuma_linha_extra_alem_dos_pregoes_de_entrada(self, price_frame):
        # 5 pregoes de entrada (pulando fim de semana) -> 5 linhas de saida, nunca mais.
        result = compute_daily_returns_frame(price_frame)
        assert len(result) == 5


class TestVolume:
    def test_volume_ratio_20_compara_com_media_movel(self, price_frame):
        result = compute_daily_returns_frame(price_frame)
        # com poucas observacoes, media movel usa o que existe (min_periods=1).
        expected_avg = price_frame["volume"].iloc[:2].mean()
        assert result.iloc[1]["volume_ratio_20"] == pytest.approx(1200 / expected_avg)


class TestBenchmark:
    def test_excess_return_e_retorno_menos_benchmark(self, price_frame, benchmark_frame):
        result = compute_daily_returns_frame(price_frame, benchmark_frame)
        row = result.iloc[1]
        expected_excess = row["return_1d_adjusted"] - row["benchmark_return_1d"]
        assert row["excess_return_1d"] == pytest.approx(expected_excess)

    def test_sem_benchmark_excess_fica_nulo(self, price_frame):
        result = compute_daily_returns_frame(price_frame, None)
        assert result["excess_return_1d"].isna().all()
