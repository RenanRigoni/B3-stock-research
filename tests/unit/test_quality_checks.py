"""Checks de qualidade de preco (fase1.md 22, 75). Puro, offline."""

from __future__ import annotations

from datetime import date

from stock_research.quality.checks import (
    ERROR,
    WARNING,
    check_chronological_order,
    check_duplicates,
    check_extreme_return,
    check_high_low_consistency,
    check_negative_volume,
    check_ohlc_non_positive,
    check_prolonged_gap,
    check_scale_anomaly,
    check_unexpected_currency,
    run_all_price_checks,
)


def _row(trade_date, **overrides):
    base = {
        "trade_date": trade_date, "open": 10.0, "high": 10.5, "low": 9.5, "close": 10.0,
        "volume": 1000, "currency": "BRL",
    }
    base.update(overrides)
    return base


class TestDuplicatas:
    def test_mesma_data_duas_vezes_e_error(self):
        rows = [_row(date(2024, 1, 2)), _row(date(2024, 1, 2))]
        findings = check_duplicates(rows)
        assert len(findings) == 1
        assert findings[0].severity == ERROR

    def test_datas_unicas_nao_geram_achado(self):
        rows = [_row(date(2024, 1, 2)), _row(date(2024, 1, 3))]
        assert check_duplicates(rows) == []


class TestOrdemCronologica:
    def test_data_fora_de_ordem_gera_warning(self):
        rows = [_row(date(2024, 1, 3)), _row(date(2024, 1, 2))]
        findings = check_chronological_order(rows)
        assert len(findings) == 1
        assert findings[0].severity == WARNING

    def test_ordem_crescente_nao_gera_achado(self):
        rows = [_row(date(2024, 1, 2)), _row(date(2024, 1, 3))]
        assert check_chronological_order(rows) == []


class TestVolumeNegativo:
    def test_volume_negativo_e_error(self):
        findings = check_negative_volume([_row(date(2024, 1, 2), volume=-10)])
        assert len(findings) == 1 and findings[0].severity == ERROR

    def test_volume_none_nao_quebra(self):
        assert check_negative_volume([_row(date(2024, 1, 2), volume=None)]) == []


class TestOhlcNaoPositivo:
    def test_close_zero_e_error(self):
        findings = check_ohlc_non_positive([_row(date(2024, 1, 2), close=0)])
        assert len(findings) == 1 and findings[0].severity == ERROR

    def test_open_negativo_e_error(self):
        findings = check_ohlc_non_positive([_row(date(2024, 1, 2), open=-1.0)])
        assert len(findings) == 1


class TestConsistenciaHighLow:
    def test_high_menor_que_low_e_error(self):
        findings = check_high_low_consistency([_row(date(2024, 1, 2), high=9.0, low=9.5)])
        assert any(f.severity == ERROR for f in findings)

    def test_high_menor_que_close_e_warning(self):
        findings = check_high_low_consistency([_row(date(2024, 1, 2), high=9.0, low=8.0, close=10.0, open=9.0)])
        assert any(f.severity == WARNING and "close" in f.message for f in findings)

    def test_ohlc_consistente_nao_gera_achado(self):
        assert check_high_low_consistency([_row(date(2024, 1, 2))]) == []


class TestRetornoExtremo:
    def test_retorno_acima_do_limite_gera_warning(self):
        rows = [_row(date(2024, 1, 2), close=10.0), _row(date(2024, 1, 3), close=13.0)]
        findings = check_extreme_return(rows, threshold=0.20)
        assert len(findings) == 1 and findings[0].severity == WARNING

    def test_retorno_dentro_do_limite_nao_gera_achado(self):
        rows = [_row(date(2024, 1, 2), close=10.0), _row(date(2024, 1, 3), close=10.5)]
        assert check_extreme_return(rows, threshold=0.20) == []


class TestGapProlongado:
    def test_gap_maior_que_limite_gera_warning(self):
        rows = [_row(date(2024, 1, 2)), _row(date(2024, 2, 20))]
        findings = check_prolonged_gap(rows, max_gap_trading_days=5)
        assert len(findings) == 1 and findings[0].severity == WARNING

    def test_gap_normal_de_fim_de_semana_nao_gera_achado(self):
        rows = [_row(date(2024, 1, 5)), _row(date(2024, 1, 8))]  # sexta -> segunda
        assert check_prolonged_gap(rows, max_gap_trading_days=5) == []


class TestMoedaInesperada:
    def test_moeda_diferente_da_esperada_gera_warning(self):
        findings = check_unexpected_currency([_row(date(2024, 1, 2), currency="USD")], expected_currency="BRL")
        assert len(findings) == 1 and findings[0].severity == WARNING


class TestAnomaliaDeEscala:
    def test_close_100x_maior_gera_warning(self):
        rows = [_row(date(2024, 1, 2), close=10.0), _row(date(2024, 1, 3), close=1000.0)]
        findings = check_scale_anomaly(rows)
        assert len(findings) == 1 and findings[0].severity == WARNING

    def test_variacao_normal_nao_gera_achado(self):
        rows = [_row(date(2024, 1, 2), close=10.0), _row(date(2024, 1, 3), close=10.5)]
        assert check_scale_anomaly(rows) == []


class TestRunAll:
    def test_lote_vazio_nao_gera_achado(self):
        assert run_all_price_checks([], extreme_return_threshold=0.2, max_gap_trading_days=5) == []

    def test_lote_limpo_nao_gera_achado(self):
        rows = [_row(date(2024, 1, 2)), _row(date(2024, 1, 3), close=10.1, open=10.0, high=10.6, low=9.6)]
        findings = run_all_price_checks(rows, extreme_return_threshold=0.2, max_gap_trading_days=5)
        assert findings == []
