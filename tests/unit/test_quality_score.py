"""Testes de ``analytics/quality_score`` (fase2_plan.md 8, 17, 17.1)."""

from __future__ import annotations

from typing import ClassVar

import pytest

from stock_research.analytics.quality_score import (
    cagr,
    classify_trend,
    coefficient_of_variation,
    compute_quality_score,
    load_config,
    score_against_markers,
    score_block,
    score_subitem,
)

CONFIG = load_config()


class TestScoreAgainstMarkers:
    M: ClassVar[list[float]] = [0.0, 0.03, 0.08, 0.15, 0.25]  # net_margin

    def test_abaixo_do_piso_e_zero(self):
        assert score_against_markers(-0.10, self.M) == 0.0

    def test_acima_do_teto_e_cem(self):
        assert score_against_markers(0.40, self.M) == 100.0

    def test_marcadores_exatos(self):
        assert score_against_markers(0.03, self.M) == 25.0
        assert score_against_markers(0.08, self.M) == 50.0

    def test_interpola_linear(self):
        # 5,5% entre 3% (25pts) e 8% (50pts): frac 0.5 -> 37.5
        assert score_against_markers(0.055, self.M) == pytest.approx(37.5)

    def test_descending_menor_e_melhor(self):
        nde = [1.5, 1.0, 0.5, 0.2, 0.0]  # net_debt/equity
        assert score_against_markers(1.5, nde, descending=True) == 0.0
        assert score_against_markers(0.0, nde, descending=True) == 100.0
        assert score_against_markers(1.0, nde, descending=True) == 25.0


class TestAggregations:
    def test_cagr(self):
        assert cagr(100.0, 200.0, 4) == pytest.approx(2**0.25 - 1)

    def test_cagr_base_nao_positiva_e_none(self):
        assert cagr(-10.0, 50.0, 3) is None

    def test_cv(self):
        assert coefficient_of_variation([10, 10, 10]) == 0.0
        assert coefficient_of_variation([1]) is None

    def test_trend(self):
        assert classify_trend(1.0, 0.5, 0.10) == "melhorando"
        assert classify_trend(0.5, 1.0, 0.10) == "piorando"
        assert classify_trend(1.0, 1.05, 0.10) == "estavel"


def _sub(key):
    for block in CONFIG["blocks"].values():
        for s in block["subitems"]:
            if s["key"] == key:
                return s
    raise KeyError(key)


class TestScoreSubitem:
    def test_median_insufficient_history(self):
        s = score_subitem(
            _sub("net_margin_median"),
            {"net_margin": {2023: 0.1, 2024: 0.1}},
            [2023, 2024],
            commodity=False,
        )
        assert s["status"] == "insufficient_history"

    def test_median_ok(self):
        series = {"net_margin": {2021: 0.08, 2022: 0.08, 2023: 0.08, 2024: 0.08}}
        s = score_subitem(
            _sub("net_margin_median"), series, [2021, 2022, 2023, 2024], commodity=False
        )
        assert s["status"] == "ok"
        assert s["points"] == pytest.approx(50.0)  # 8% -> 50 pts

    def test_negativo_ancora_no_piso(self):
        series = {"net_margin": {2021: -0.2, 2022: -0.1, 2023: -0.05}}
        s = score_subitem(_sub("net_margin_median"), series, [2021, 2022, 2023], commodity=False)
        assert s["points"] == 0.0

    def test_cv_usa_banda_commodity(self):
        # net_margin estável -> cv baixo -> muitos pontos em ambas bandas
        series = {"net_margin": {2020: 0.10, 2021: 0.11, 2022: 0.09, 2023: 0.10, 2024: 0.10}}
        std = score_subitem(
            _sub("net_margin_cv"), series, [2020, 2021, 2022, 2023, 2024], commodity=False
        )
        com = score_subitem(
            _sub("net_margin_cv"), series, [2020, 2021, 2022, 2023, 2024], commodity=True
        )
        assert std["points"] >= 75 and com["points"] >= 75

    def test_positive_fraction(self):
        series = {"net_income": {2021: 10, 2022: -5, 2023: 20, 2024: 30, 2025: 40}}
        s = score_subitem(
            _sub("positive_net_income_fraction"),
            series,
            [2021, 2022, 2023, 2024, 2025],
            commodity=False,
        )
        assert s["value"] == pytest.approx(0.8)  # 4 de 5
        assert s["points"] == pytest.approx(75.0)

    def test_cagr_requer_dois_extremos(self):
        s = score_subitem(_sub("revenue_cagr"), {"revenue": {2024: 100}}, [2024], commodity=False)
        assert s["status"] == "missing_input"


class TestScoreBlock:
    def test_reescala_quando_subitem_falta(self):
        block = CONFIG["blocks"]["rentabilidade"]  # net_margin(12) + roe(13)
        # só net_margin disponível (>=3 anos), roe ausente
        series = {"net_margin": {2021: 0.08, 2022: 0.08, 2023: 0.08}}
        res = score_block(block, series, [2021, 2022, 2023], commodity=False)
        # net_margin 8% -> 50 pts; peso coberto 12 de 25 -> bloco = 50/100*12/12*25 = 12.5
        assert res["status"] == "partial"
        assert res["score"] == pytest.approx(12.5)

    def test_bloco_sem_dado_e_insufficient(self):
        res = score_block(
            CONFIG["blocks"]["rentabilidade"], {}, [2021, 2022, 2023], commodity=False
        )
        assert res["status"] == "insufficient_history"
        assert res["score"] is None


class TestComputeQualityScore:
    def _full_series(self, n=5, start=2020):
        yrs = list(range(start, start + n))
        return {
            "net_margin": {y: 0.10 for y in yrs},
            "roe": {y: 0.15 for y in yrs},
            "net_income": {y: 20_000 + i * 1000 for i, y in enumerate(yrs)},
            "revenue": {y: 100_000 + i * 5000 for i, y in enumerate(yrs)},
            "net_debt_to_equity": {y: 0.4 for y in yrs},
            "fcf_to_revenue": {y: 0.08 for y in yrs},
            "fcf_to_net_income": {y: 0.9 for y in yrs},
        }

    def test_happy_path_score_valido(self):
        r = compute_quality_score(self._full_series(), commodity_exposed=False, config=CONFIG)
        assert r["score_status"] == "ok"
        assert 0 <= r["score"] <= 100
        assert r["window_years"] == 5
        assert r["weight_covered"] == 100.0

    def test_janela_curta_e_incomplete(self):
        r = compute_quality_score(self._full_series(n=2), commodity_exposed=False, config=CONFIG)
        assert r["score_status"] == "incomplete"
        assert r["score"] is None

    def test_menos_de_60pct_do_peso_e_incomplete(self):
        # sobra só geração de caixa (20 de 100) -> abaixo dos 60% -> incomplete
        series = self._full_series()
        for m in ("net_margin", "roe", "net_debt_to_equity", "net_income", "revenue"):
            series[m] = {}
        r = compute_quality_score(series, commodity_exposed=False, config=CONFIG)
        assert r["score_status"] == "incomplete"
        assert r["weight_covered"] == 20.0

    def test_janela_pega_os_5_anos_mais_recentes(self):
        r = compute_quality_score(
            self._full_series(n=8, start=2016), commodity_exposed=False, config=CONFIG
        )
        assert r["window_years"] == 5
