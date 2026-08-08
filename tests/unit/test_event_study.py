"""Testes de ``analytics/event_study.py`` (fase1.md 53-61). Funcoes puras.

``estimate_market_model`` e conferido contra ``numpy.polyfit`` (implementacao
independente da nossa) -- nao basta o teste confirmar que a funcao faz o que
o codigo faz, precisa confirmar que bate com uma segunda fonte de verdade
para a regressao OLS.
"""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pytest

from stock_research.analytics.event_study import (
    HorizonReturn,
    abnormal_returns_at_horizons,
    compute_return,
    estimate_market_model,
    returns_at_horizons,
)


class _FakeTradingIndex:
    """Sequencia de pregoes consecutivos (segunda a sexta, sem feriado) a
    partir de uma data base -- suficiente pra testar aritmetica de indice
    sem precisar do calendario real."""

    def __init__(self, start: date, n_days: int) -> None:
        self._dates = []
        cursor = start
        while len(self._dates) < n_days:
            if cursor.weekday() < 5:
                self._dates.append(cursor)
            cursor += timedelta(days=1)
        self._index = {d: i for i, d in enumerate(self._dates)}

    def index_of(self, d: date) -> int | None:
        return self._index.get(d)

    def date_at(self, index: int) -> date | None:
        if 0 <= index < len(self._dates):
            return self._dates[index]
        return None


class TestComputeReturn:
    def test_retorno_positivo(self):
        assert compute_return(100, 110) == pytest.approx(0.10)

    def test_retorno_negativo(self):
        assert compute_return(100, 90) == pytest.approx(-0.10)

    def test_base_none(self):
        assert compute_return(None, 110) is None

    def test_target_none(self):
        assert compute_return(100, None) is None

    def test_base_zero_nao_divide_por_zero(self):
        assert compute_return(0, 110) is None


class TestReturnsAtHorizons:
    def test_horizonte_positivo_simples(self):
        calendar = _FakeTradingIndex(date(2026, 1, 5), 30)  # segunda
        ref = date(2026, 1, 5)
        prices = {ref: 100.0, calendar.date_at(calendar.index_of(ref) + 5): 110.0}

        results = returns_at_horizons(reference_date=ref, horizons=[5], prices=prices, calendar=calendar)

        assert results[0].return_actual == pytest.approx(0.10)
        assert results[0].is_censored is False

    def test_horizonte_negativo_pre_evento(self):
        calendar = _FakeTradingIndex(date(2026, 1, 1), 30)
        ref = calendar.date_at(10)
        pre = calendar.date_at(10 - 5)
        prices = {ref: 100.0, pre: 90.0}

        results = returns_at_horizons(reference_date=ref, horizons=[-5], prices=prices, calendar=calendar)

        # retorno de -5 ate 0: 100/90 - 1
        assert results[0].return_actual == pytest.approx(100.0 / 90.0 - 1.0)

    def test_horizonte_fora_da_janela_conhecida_fica_censurado(self):
        calendar = _FakeTradingIndex(date(2026, 1, 1), 10)  # so 10 pregoes
        ref = calendar.date_at(0)
        prices = {ref: 100.0}

        results = returns_at_horizons(reference_date=ref, horizons=[252], prices=prices, calendar=calendar)

        assert results[0].is_censored is True
        assert results[0].return_actual is None
        assert results[0].end_trade_date is None

    def test_preco_ausente_no_horizonte_fica_censurado(self):
        calendar = _FakeTradingIndex(date(2026, 1, 5), 30)
        ref = date(2026, 1, 5)
        prices = {ref: 100.0}  # sem preco no D+5

        results = returns_at_horizons(reference_date=ref, horizons=[5], prices=prices, calendar=calendar)

        assert results[0].is_censored is True
        assert results[0].end_trade_date is not None  # a data existe, so falta o preco

    def test_data_de_referencia_fora_do_calendario(self):
        calendar = _FakeTradingIndex(date(2026, 1, 5), 30)
        results = returns_at_horizons(
            reference_date=date(1999, 1, 1), horizons=[1, 5], prices={}, calendar=calendar
        )

        assert all(r.is_censored for r in results)

    def test_multiplos_horizontes_preservam_ordem(self):
        calendar = _FakeTradingIndex(date(2026, 1, 5), 300)
        ref = date(2026, 1, 5)
        prices = {ref: 100.0}
        for h in [1, 5, 20]:
            prices[calendar.date_at(calendar.index_of(ref) + h)] = 100.0 + h

        results = returns_at_horizons(reference_date=ref, horizons=[1, 5, 20], prices=prices, calendar=calendar)

        assert [r.horizon_days for r in results] == [1, 5, 20]


class TestEstimateMarketModel:
    def test_relacao_perfeita_beta_1_alpha_0(self):
        market = [0.01, -0.02, 0.03, 0.0, 0.015] * 20
        stock = list(market)  # replica exata: beta=1, alpha=0

        model = estimate_market_model(stock, market, min_observations=10)

        assert model.alpha == pytest.approx(0.0, abs=1e-9)
        assert model.beta == pytest.approx(1.0, abs=1e-9)
        assert model.r_squared == pytest.approx(1.0, abs=1e-9)
        assert model.low_sample is False

    def test_bate_com_numpy_polyfit(self):
        rng = np.random.default_rng(42)
        market = rng.normal(0, 0.02, 200).tolist()
        stock = [0.001 + 1.3 * m + rng.normal(0, 0.005) for m in market]

        model = estimate_market_model(stock, market, min_observations=60)

        np_beta, np_alpha = np.polyfit(market, stock, 1)
        assert model.alpha == pytest.approx(np_alpha, abs=1e-9)
        assert model.beta == pytest.approx(np_beta, abs=1e-9)

    def test_amostra_pequena_marca_low_sample(self):
        model = estimate_market_model([0.01, 0.02, -0.01], [0.01, 0.015, -0.005], min_observations=60)

        assert model.low_sample is True
        assert model.observations == 3
        # Ainda calcula alpha/beta -- nao esconde, so sinaliza (fase1.md 57).
        assert model.alpha is not None
        assert model.beta is not None

    def test_amostra_suficiente_nao_marca_low_sample(self):
        rng = np.random.default_rng(1)
        market = rng.normal(0, 0.02, 100).tolist()
        stock = [1.0 * m for m in market]

        model = estimate_market_model(stock, market, min_observations=60)

        assert model.low_sample is False

    def test_menos_de_dois_pontos(self):
        model = estimate_market_model([0.01], [0.01])

        assert model.alpha is None
        assert model.beta is None
        assert model.low_sample is True

    def test_mercado_sem_variancia_nao_quebra(self):
        model = estimate_market_model([0.01, 0.02, 0.03], [0.0, 0.0, 0.0], min_observations=1)

        assert model.beta is None
        assert model.alpha is None


class TestAbnormalReturnsAtHorizons:
    def test_excesso_simples_e_diferenca(self):
        stock = [HorizonReturn(1, date(2026, 1, 2), 0.05, False)]
        bench = [HorizonReturn(1, date(2026, 1, 2), 0.02, False)]
        model = estimate_market_model([], [])  # sem modelo -- so testa excess_return

        results = abnormal_returns_at_horizons(stock, bench, model)

        assert results[0].excess_return == pytest.approx(0.03)

    def test_abnormal_return_usa_o_modelo(self):
        stock = [HorizonReturn(1, date(2026, 1, 2), 0.10, False)]
        bench = [HorizonReturn(1, date(2026, 1, 2), 0.02, False)]
        from stock_research.analytics.event_study import MarketModel

        model = MarketModel(alpha=0.001, beta=1.2, r_squared=0.8, residual_std=0.01, observations=100, low_sample=False)

        results = abnormal_returns_at_horizons(stock, bench, model)

        expected = 0.001 + 1.2 * 0.02
        assert results[0].expected_return == pytest.approx(expected)
        assert results[0].abnormal_return == pytest.approx(0.10 - expected)

    def test_car_acumula_apenas_horizontes_positivos(self):
        stock = [
            HorizonReturn(-5, date(2025, 12, 20), 0.01, False),
            HorizonReturn(1, date(2026, 1, 2), 0.02, False),
            HorizonReturn(5, date(2026, 1, 6), 0.03, False),
        ]
        bench = [
            HorizonReturn(-5, date(2025, 12, 20), 0.0, False),
            HorizonReturn(1, date(2026, 1, 2), 0.0, False),
            HorizonReturn(5, date(2026, 1, 6), 0.0, False),
        ]
        from stock_research.analytics.event_study import MarketModel

        model = MarketModel(alpha=0.0, beta=1.0, r_squared=1.0, residual_std=0.0, observations=100, low_sample=False)

        results = abnormal_returns_at_horizons(stock, bench, model)
        by_h = {r.horizon_days: r for r in results}

        assert by_h[-5].car is None  # pre-evento nunca acumula CAR
        assert by_h[1].car == pytest.approx(0.02)
        assert by_h[5].car == pytest.approx(0.02 + 0.03)

    def test_horizonte_censurado_quebra_a_cadeia_do_car(self):
        stock = [
            HorizonReturn(1, date(2026, 1, 2), 0.02, False),
            HorizonReturn(5, date(2026, 1, 6), None, True),  # censurado
            HorizonReturn(20, date(2026, 1, 30), 0.05, False),
        ]
        bench = [
            HorizonReturn(1, date(2026, 1, 2), 0.0, False),
            HorizonReturn(5, date(2026, 1, 6), None, True),
            HorizonReturn(20, date(2026, 1, 30), 0.0, False),
        ]
        from stock_research.analytics.event_study import MarketModel

        model = MarketModel(alpha=0.0, beta=1.0, r_squared=1.0, residual_std=0.0, observations=100, low_sample=False)

        results = abnormal_returns_at_horizons(stock, bench, model)
        by_h = {r.horizon_days: r for r in results}

        assert by_h[1].car == pytest.approx(0.02)
        assert by_h[5].car is None
        # D+20 nao pode aparentar CAR completo se D+5 faltou no meio.
        assert by_h[20].car is None

    def test_sem_benchmark_no_horizonte_fica_none(self):
        stock = [HorizonReturn(1, date(2026, 1, 2), 0.02, False)]
        bench: list[HorizonReturn] = []
        model = estimate_market_model([], [])

        results = abnormal_returns_at_horizons(stock, bench, model)

        assert results[0].benchmark_return is None
        assert results[0].excess_return is None
