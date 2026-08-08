"""Parsing da resposta da brapi (fase1.md 20, 75). Offline -- fixture em tests/fixtures/prices."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from stock_research.sources.prices.brapi_source import (
    BrapiUnavailableError,
    _range_for_window,
    parse_historical,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "prices" / "brapi_quote_sample.json"


@pytest.fixture
def payload() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class TestParseHistorical:
    def test_extrai_linhas_dentro_da_janela(self, payload):
        frame = parse_historical(payload, date(2024, 1, 1), date(2024, 1, 10))
        assert len(frame) == 2
        assert frame.iloc[0]["trade_date"] == date(2024, 1, 2)
        assert frame.iloc[0]["close"] == 38.0

    def test_janela_estreita_filtra_linhas(self, payload):
        frame = parse_historical(payload, date(2024, 1, 3), date(2024, 1, 10))
        assert len(frame) == 1
        assert frame.iloc[0]["trade_date"] == date(2024, 1, 3)

    def test_end_e_exclusivo(self, payload):
        frame = parse_historical(payload, date(2024, 1, 1), date(2024, 1, 3))
        assert frame["trade_date"].tolist() == [date(2024, 1, 2)]

    def test_resposta_sem_results_lanca_erro_claro(self):
        with pytest.raises(BrapiUnavailableError):
            parse_historical({}, date(2024, 1, 1), date(2024, 1, 10))


class TestRangeParaJanela:
    def test_janela_curta_pede_5d(self):
        assert _range_for_window(date(2024, 1, 1), date(2024, 1, 3)) == "5d"

    def test_janela_de_60_dias_pede_3mo(self):
        assert _range_for_window(date(2024, 1, 1), date(2024, 3, 1)) == "3mo"
