"""Testes de ``analytics/ttm`` (fase2_plan.md 5)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from stock_research.analytics.ttm import (
    Point,
    assemble_ttm,
    isolated_quarters,
    quarter_of,
    ttm_series,
)


def _p(v, af_days=0, flag="ok"):
    return Point(Decimal(v), datetime(2025, 1, 1, tzinfo=UTC) + timedelta(days=af_days), flag)


class TestIsolatedQuarters:
    def test_q1_e_o_proprio_ytd(self):
        iso = isolated_quarters({}, {(2024, 1): _p(100)})
        assert iso[(2024, 1)].value == Decimal(100)

    def test_q2_q3_por_subtracao(self):
        ytd = {(2024, 1): _p(100), (2024, 2): _p(250), (2024, 3): _p(400)}
        iso = isolated_quarters({}, ytd)
        assert iso[(2024, 2)].value == Decimal(150)
        assert iso[(2024, 3)].value == Decimal(150)

    def test_q4_e_anual_menos_ytd_q3(self):
        iso = isolated_quarters({2024: _p(600)}, {(2024, 3): _p(400)})
        assert iso[(2024, 4)].value == Decimal(200)

    def test_q2_sem_q1_nao_deriva(self):
        iso = isolated_quarters({}, {(2024, 2): _p(250)})
        assert (2024, 2) not in iso

    def test_available_from_e_o_mais_recente_dos_componentes(self):
        iso = isolated_quarters(
            {}, {(2024, 1): _p(100, af_days=10), (2024, 2): _p(250, af_days=40)}
        )
        assert iso[(2024, 2)].available_from == datetime(2025, 1, 1, tzinfo=UTC) + timedelta(
            days=40
        )

    def test_quality_flag_e_o_pior(self):
        iso = isolated_quarters(
            {}, {(2024, 1): _p(100, flag="ok"), (2024, 2): _p(250, flag="estimated")}
        )
        assert iso[(2024, 2)].quality_flag == "estimated"


class TestTtmSeries:
    def _four_years_of_quarters(self):
        annual = {y: _p(400) for y in (2023, 2024, 2025)}
        ytd = {}
        for y in (2023, 2024, 2025):
            for q, cum in ((1, 100), (2, 200), (3, 300)):
                ytd[(y, q)] = _p(cum)
        return annual, ytd

    def test_ttm_no_fim_do_ano_bate_com_o_anual(self):
        annual, ytd = self._four_years_of_quarters()
        ttm = assemble_ttm(annual, ytd)
        # TTM em 31/12/2024 = Q1..Q4 de 2024 isolados = 100+100+100+100 = 400 = anual
        assert ttm[date(2024, 12, 31)].value == Decimal(400)

    def test_ttm_em_q2_soma_os_4_trailing(self):
        annual, ytd = self._four_years_of_quarters()
        ttm = assemble_ttm(annual, ytd)
        # TTM em 30/06/2025 = Q3'24 + Q4'24 + Q1'25 + Q2'25 = 100+100+100+100
        assert ttm[date(2025, 6, 30)].value == Decimal(400)

    def test_sem_4_trimestres_consecutivos_nao_emite(self):
        # só 2024 completo, sem 2023 -> nenhum TTM antes de 31/12/2024
        annual = {2024: _p(400)}
        ytd = {(2024, 1): _p(100), (2024, 2): _p(200), (2024, 3): _p(300)}
        ttm = ttm_series(isolated_quarters(annual, ytd))
        assert set(ttm) == {date(2024, 12, 31)}

    def test_available_from_do_ttm_e_o_mais_recente(self):
        annual = {2024: _p(400, af_days=500)}
        ytd = {
            (2024, 1): _p(100, af_days=100),
            (2024, 2): _p(200, af_days=200),
            (2024, 3): _p(300, af_days=300),
        }
        ttm = assemble_ttm(annual, ytd)
        # o TTM só é elegível a partir do available_from do pacote anual (mais recente)
        assert ttm[date(2024, 12, 31)].available_from == datetime(
            2025, 1, 1, tzinfo=UTC
        ) + timedelta(days=500)


def test_quarter_of():
    assert quarter_of(date(2024, 3, 31)) == 1
    assert quarter_of(date(2024, 12, 31)) == 4
    assert quarter_of(date(2024, 5, 15)) is None
