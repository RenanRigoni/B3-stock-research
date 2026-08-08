"""Normalizacao de precos (fase1.md 11-13, 75). Offline -- fixture em tests/fixtures/prices."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from stock_research.transforms.prices import (
    flatten_yfinance_frame,
    to_corporate_action_rows,
    to_daily_price_rows,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "prices" / "petr4_flat_sample.csv"


@pytest.fixture
def sample_frame() -> pd.DataFrame:
    df = pd.read_csv(FIXTURE, parse_dates=["trade_date"])
    df["trade_date"] = df["trade_date"].dt.date
    return df


class TestFlatten:
    def test_multiindex_vira_colunas_simples(self):
        idx = pd.date_range("2024-01-02", periods=2, freq="D", name="Date")
        raw = pd.DataFrame(
            {("Close", "PETR4.SA"): [38.0, 38.5], ("Volume", "PETR4.SA"): [100, 200]}, index=idx
        )
        raw.columns = pd.MultiIndex.from_tuples(raw.columns)

        flat = flatten_yfinance_frame(raw, "PETR4.SA")

        assert list(flat.columns[:2]) == ["trade_date", "symbol"]
        assert "Close" in flat.columns and "Volume" in flat.columns
        assert flat["trade_date"].tolist() == [pd.Timestamp("2024-01-02").date(), pd.Timestamp("2024-01-03").date()]


class TestPrecoDiario:
    def test_preco_e_ajustado_ficam_separados(self, sample_frame):
        rows = to_daily_price_rows(
            sample_frame, instrument_id=1, source="yfinance", source_symbol="PETR4.SA",
            currency="BRL", raw_file="raw/x.parquet", run_id=99,
        )
        first = rows[0]
        assert first["close"] == 38.0
        assert first["adj_close"] == 26.5
        assert first["close"] != first["adj_close"]

    def test_seis_linhas_geram_seis_pregoes_sem_duplicar_chave(self, sample_frame):
        rows = to_daily_price_rows(
            sample_frame, instrument_id=1, source="yfinance", source_symbol="PETR4.SA",
            currency="BRL", raw_file=None, run_id=1,
        )
        keys = {(r["instrument_id"], r["trade_date"], r["source"]) for r in rows}
        assert len(keys) == len(rows) == 6

    def test_campos_de_rastreabilidade_preservados(self, sample_frame):
        rows = to_daily_price_rows(
            sample_frame, instrument_id=1, source="yfinance", source_symbol="PETR4.SA",
            currency="BRL", raw_file="data/raw/prices/PETR4/x.parquet", run_id=42,
        )
        assert all(r["raw_file"] == "data/raw/prices/PETR4/x.parquet" for r in rows)
        assert all(r["run_id"] == 42 for r in rows)

    def test_normalizar_o_mesmo_lote_duas_vezes_produz_linhas_identicas(self, sample_frame):
        # A idempotencia do upsert depende de a chave natural (instrument_id,
        # trade_date, source) e os valores serem deterministicos entre chamadas
        # (fase1.md 102) -- reprocessar o mesmo raw nunca pode gerar dado diferente.
        kwargs = dict(instrument_id=1, source="yfinance", source_symbol="PETR4.SA",
                      currency="BRL", raw_file="raw/x.parquet", run_id=7)
        first = to_daily_price_rows(sample_frame, **kwargs)
        second = to_daily_price_rows(sample_frame, **kwargs)
        assert first == second


class TestAcoesCorporativas:
    def test_dividendo_vira_action_type_dividend_nunca_jcp(self, sample_frame):
        rows = to_corporate_action_rows(sample_frame, instrument_id=1, source="yfinance", run_id=1)
        dividends = [r for r in rows if r["action_type"] == "dividend"]
        assert len(dividends) == 1
        assert dividends[0]["value"] == 1.5
        assert all(r["action_type"] != "jcp" for r in rows)

    def test_stock_split_ratio_maior_que_1_vira_split(self, sample_frame):
        rows = to_corporate_action_rows(sample_frame, instrument_id=1, source="yfinance", run_id=1)
        splits = [r for r in rows if r["action_type"] == "split"]
        assert len(splits) == 1
        assert splits[0]["ratio"] == 2.0

    def test_dias_sem_provento_nao_geram_linha(self, sample_frame):
        rows = to_corporate_action_rows(sample_frame, instrument_id=1, source="yfinance", run_id=1)
        # 6 pregoes, so 1 dividendo e 1 split -> 2 linhas, nao 6+.
        assert len(rows) == 2
