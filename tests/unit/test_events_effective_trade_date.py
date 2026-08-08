"""Testes de ``transforms/events.py`` -- effective_trade_date (fase1.md 39-41).

Esta e a regra mais importante do Milestone 9, no mesmo nivel de
criticidade do point-in-time de fundamentos (Milestone 5): atribuir uma
noticia ao pregao errado contamina todo o event study rio abaixo. Testado
com um ``CalendarLookup`` fake, sem tocar banco.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from stock_research.transforms.events import compute_effective_trade_date

BRT = ZoneInfo("America/Sao_Paulo")
MARKET_OPEN = time(10, 0)
MARKET_CLOSE = time(17, 0)


class _FakeCalendar:
    """Semana util seg-sex a partir de uma data base; sem feriados, exceto
    os explicitamente marcados via ``holidays``."""

    def __init__(self, holidays: set[date] | None = None) -> None:
        self._holidays = holidays or set()

    def is_trading_day(self, d: date) -> bool:
        return d.weekday() < 5 and d not in self._holidays

    def next_trading_day(self, d: date) -> date | None:
        cursor = d + timedelta(days=1)
        for _ in range(30):
            if self.is_trading_day(cursor):
                return cursor
            cursor += timedelta(days=1)
        return None


def _dt(y: int, m: int, d: int, h: int, mi: int) -> datetime:
    return datetime(y, m, d, h, mi, tzinfo=BRT)


class TestNoticiaDuranteOPregao:
    def test_publicada_as_11h_usa_o_proprio_dia(self):
        # 2026-08-05 e quarta-feira (dia util).
        result = compute_effective_trade_date(
            event_date=date(2026, 8, 5), event_time_local=_dt(2026, 8, 5, 11, 0),
            time_precision="exact", calendar=_FakeCalendar(),
            market_open_local=MARKET_OPEN, market_close_local=MARKET_CLOSE,
        )

        assert result.effective_trade_date == date(2026, 8, 5)
        assert result.market_session_uncertain is False

    def test_publicada_pre_abertura_ainda_usa_o_proprio_dia(self):
        result = compute_effective_trade_date(
            event_date=date(2026, 8, 5), event_time_local=_dt(2026, 8, 5, 7, 30),
            time_precision="exact", calendar=_FakeCalendar(),
            market_open_local=MARKET_OPEN, market_close_local=MARKET_CLOSE,
        )

        assert result.effective_trade_date == date(2026, 8, 5)

    def test_publicada_exatamente_no_fechamento_ainda_conta_como_durante(self):
        result = compute_effective_trade_date(
            event_date=date(2026, 8, 5), event_time_local=_dt(2026, 8, 5, 17, 0),
            time_precision="exact", calendar=_FakeCalendar(),
            market_open_local=MARKET_OPEN, market_close_local=MARKET_CLOSE,
        )

        assert result.effective_trade_date == date(2026, 8, 5)


class TestNoticiaAposFechamento:
    def test_publicada_as_19h_vai_para_o_proximo_pregao(self):
        # 2026-08-05 quarta -> proximo pregao e quinta 2026-08-06.
        result = compute_effective_trade_date(
            event_date=date(2026, 8, 5), event_time_local=_dt(2026, 8, 5, 19, 0),
            time_precision="exact", calendar=_FakeCalendar(),
            market_open_local=MARKET_OPEN, market_close_local=MARKET_CLOSE,
        )

        assert result.effective_trade_date == date(2026, 8, 6)

    def test_um_segundo_apos_o_fechamento_ja_conta_como_apos(self):
        result = compute_effective_trade_date(
            event_date=date(2026, 8, 5), event_time_local=_dt(2026, 8, 5, 17, 0).replace(second=1),
            time_precision="exact", calendar=_FakeCalendar(),
            market_open_local=MARKET_OPEN, market_close_local=MARKET_CLOSE,
        )

        assert result.effective_trade_date == date(2026, 8, 6)


class TestNoticiaForaDoPregao:
    def test_publicada_no_sabado_vai_para_segunda(self):
        # 2026-08-08 e sabado.
        result = compute_effective_trade_date(
            event_date=date(2026, 8, 8), event_time_local=_dt(2026, 8, 8, 11, 0),
            time_precision="exact", calendar=_FakeCalendar(),
            market_open_local=MARKET_OPEN, market_close_local=MARKET_CLOSE,
        )

        assert result.effective_trade_date == date(2026, 8, 10)  # segunda
        assert result.effective_trade_date.weekday() < 5

    def test_publicada_em_feriado_pula_para_o_proximo_pregao(self):
        # 2026-08-05 (quarta) marcado como feriado -> proximo pregao e quinta.
        calendar = _FakeCalendar(holidays={date(2026, 8, 5)})
        result = compute_effective_trade_date(
            event_date=date(2026, 8, 5), event_time_local=_dt(2026, 8, 5, 11, 0),
            time_precision="exact", calendar=calendar,
            market_open_local=MARKET_OPEN, market_close_local=MARKET_CLOSE,
        )

        assert result.effective_trade_date == date(2026, 8, 6)


class TestSemHoraConfiavel:
    def test_date_only_sempre_vai_para_o_proximo_pregao(self):
        # fase1.md 39: politica conservadora, mesmo se event_date ja for pregao.
        result = compute_effective_trade_date(
            event_date=date(2026, 8, 5), event_time_local=None,
            time_precision="date_only", calendar=_FakeCalendar(),
            market_open_local=MARKET_OPEN, market_close_local=MARKET_CLOSE,
        )

        assert result.effective_trade_date == date(2026, 8, 6)
        assert result.market_session_uncertain is True

    def test_unknown_tambem_vai_para_o_proximo_pregao(self):
        result = compute_effective_trade_date(
            event_date=date(2026, 8, 5), event_time_local=None,
            time_precision="unknown", calendar=_FakeCalendar(),
            market_open_local=MARKET_OPEN, market_close_local=MARKET_CLOSE,
        )

        assert result.effective_trade_date == date(2026, 8, 6)

    def test_date_only_em_sexta_pula_o_fim_de_semana(self):
        # 2026-08-07 e sexta -> proximo pregao e segunda 2026-08-10.
        result = compute_effective_trade_date(
            event_date=date(2026, 8, 7), event_time_local=None,
            time_precision="date_only", calendar=_FakeCalendar(),
            market_open_local=MARKET_OPEN, market_close_local=MARKET_CLOSE,
        )

        assert result.effective_trade_date == date(2026, 8, 10)


class TestMarketSessionUncertain:
    def test_precisao_hora_marca_incerto_mesmo_durante_o_pregao(self):
        # GDELT nunca da "exact" (seendate e quando o crawler viu, nao
        # publicacao) -- "hour" precisa ficar marcado incerto mesmo quando
        # cai dentro do horario de pregao (fase1.md 40).
        result = compute_effective_trade_date(
            event_date=date(2026, 8, 5), event_time_local=_dt(2026, 8, 5, 11, 0),
            time_precision="hour", calendar=_FakeCalendar(),
            market_open_local=MARKET_OPEN, market_close_local=MARKET_CLOSE,
        )

        assert result.market_session_uncertain is True

    def test_precisao_exact_nao_marca_incerto(self):
        result = compute_effective_trade_date(
            event_date=date(2026, 8, 5), event_time_local=_dt(2026, 8, 5, 11, 0),
            time_precision="exact", calendar=_FakeCalendar(),
            market_open_local=MARKET_OPEN, market_close_local=MARKET_CLOSE,
        )

        assert result.market_session_uncertain is False


class TestReasoning:
    def test_reasoning_sempre_presente_e_nao_vazio(self):
        result = compute_effective_trade_date(
            event_date=date(2026, 8, 5), event_time_local=_dt(2026, 8, 5, 19, 0),
            time_precision="exact", calendar=_FakeCalendar(),
            market_open_local=MARKET_OPEN, market_close_local=MARKET_CLOSE,
        )

        assert result.reasoning
