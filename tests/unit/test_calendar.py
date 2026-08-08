"""Calendario de negociacao e aritmetica D+N por pregao (fase1.md 16, 54, 75). Offline."""

from __future__ import annotations

from datetime import date

from stock_research.transforms.calendar import build_calendar_days, shift_trading_days

# Segunda 2024-01-08 a sexta 2024-01-12, pulando quarta (feriado fictício) e o
# fim de semana anterior/seguinte -- soh o que o "benchmark" realmente negociou.
TRADING_DATES = [
    date(2024, 1, 8),   # segunda
    date(2024, 1, 9),   # terca
    date(2024, 1, 11),  # quinta (quarta 10 "feriado", ausente de proposito)
    date(2024, 1, 12),  # sexta
    date(2024, 1, 15),  # segunda seguinte
]


class TestBuildCalendarDays:
    def test_indice_e_sequencial_a_partir_de_zero(self):
        days = build_calendar_days(TRADING_DATES)
        assert [d.trading_day_index for d in days] == [0, 1, 2, 3, 4]

    def test_previous_next_ignoram_dias_sem_pregao(self):
        days = build_calendar_days(TRADING_DATES)
        quinta = next(d for d in days if d.trade_date == date(2024, 1, 11))
        assert quinta.previous_trading_day == date(2024, 1, 9)  # terca, nao quarta
        assert quinta.next_trading_day == date(2024, 1, 12)

    def test_duplicatas_e_ordem_de_entrada_nao_importam(self):
        shuffled = [*TRADING_DATES[::-1], TRADING_DATES[0], TRADING_DATES[2]]  # embaralhado + duplicatas
        days = build_calendar_days(shuffled)
        assert [d.trade_date for d in days] == sorted(set(TRADING_DATES))


class TestShiftTradingDays:
    def test_dplus1_pula_feriado_no_meio_da_semana(self):
        # Terca (09) + 1 pregao -> quinta (11), nunca quarta (feriado).
        assert shift_trading_days(TRADING_DATES, date(2024, 1, 9), 1) == date(2024, 1, 11)

    def test_sexta_dplus1_pula_fim_de_semana(self):
        assert shift_trading_days(TRADING_DATES, date(2024, 1, 12), 1) == date(2024, 1, 15)

    def test_dminus1(self):
        assert shift_trading_days(TRADING_DATES, date(2024, 1, 12), -1) == date(2024, 1, 11)

    def test_referencia_fora_do_calendario_retorna_none(self):
        # 2024-01-10 e o "feriado": nunca foi pregao.
        assert shift_trading_days(TRADING_DATES, date(2024, 1, 10), 1) is None

    def test_destino_fora_do_horizonte_retorna_none(self):
        assert shift_trading_days(TRADING_DATES, date(2024, 1, 8), -1) is None
        assert shift_trading_days(TRADING_DATES, date(2024, 1, 15), 1) is None

    def test_d0_devolve_a_propria_data(self):
        assert shift_trading_days(TRADING_DATES, date(2024, 1, 11), 0) == date(2024, 1, 11)
