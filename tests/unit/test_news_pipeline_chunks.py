"""Testes de ``pipelines/news._weekly_chunks`` -- fatiamento de janela de
data para contornar a ausencia de paginacao real no GDELT DOC API (fase1.md
25). Funcao pura, sem rede nem banco."""

from __future__ import annotations

from datetime import date
from itertools import pairwise

from stock_research.pipelines.news import _weekly_chunks


class TestWeeklyChunks:
    def test_janela_menor_que_uma_semana_vira_um_unico_bloco(self):
        chunks = _weekly_chunks(date(2024, 1, 1), date(2024, 1, 3))

        assert chunks == [(date(2024, 1, 1), date(2024, 1, 3))]

    def test_janela_exata_de_uma_semana(self):
        chunks = _weekly_chunks(date(2024, 1, 1), date(2024, 1, 7))

        assert chunks == [(date(2024, 1, 1), date(2024, 1, 7))]

    def test_janela_de_duas_semanas_vira_dois_blocos_sem_sobreposicao(self):
        chunks = _weekly_chunks(date(2024, 1, 1), date(2024, 1, 14))

        assert chunks == [
            (date(2024, 1, 1), date(2024, 1, 7)),
            (date(2024, 1, 8), date(2024, 1, 14)),
        ]

    def test_ultimo_bloco_e_truncado_no_fim_da_janela(self):
        chunks = _weekly_chunks(date(2024, 1, 1), date(2024, 1, 10))

        assert chunks[-1] == (date(2024, 1, 8), date(2024, 1, 10))

    def test_data_unica_vira_um_bloco_de_um_dia(self):
        chunks = _weekly_chunks(date(2024, 1, 1), date(2024, 1, 1))

        assert chunks == [(date(2024, 1, 1), date(2024, 1, 1))]

    def test_blocos_cobrem_a_janela_inteira_sem_buraco(self):
        start, end = date(2024, 1, 1), date(2024, 3, 15)
        chunks = _weekly_chunks(start, end)

        assert chunks[0][0] == start
        assert chunks[-1][1] == end
        for (_, prev_end), (next_start, _) in pairwise(chunks):
            assert (next_start - prev_end).days == 1  # sem buraco, sem sobreposicao
