"""Liquidez point-in-time (Fase 3 M2) + a guarda de `adj_close`.

O teste central e `test_adj_close_nunca_entra_no_volume_financeiro`: usar
`adj_close` numa metrica historica injetaria proventos e splits FUTUROS no
passado (a Fase 1.1 mediu 81% das linhas do PETR4 mudando entre duas leituras
da mesma serie ajustada). E look-ahead silencioso -- o pior tipo.
"""

from __future__ import annotations

import ast
import inspect
from datetime import date

from stock_research.analytics import liquidity as liq_mod
from stock_research.analytics.liquidity import (
    PRICE_FIELD,
    DailyBar,
    compute_liquidity_series,
    to_bars,
)
from stock_research.pipelines import liquidity as liq_pipeline


def _calendar(n: int, start: date = date(2020, 1, 1)) -> list[date]:
    """`n` pregoes ficticios consecutivos (dias uteis aproximados)."""
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d = date.fromordinal(d.toordinal() + 1)
    return out


# ===========================================================================
# GUARDA -- adj_close proibido
# ===========================================================================


def test_adj_close_nunca_entra_no_volume_financeiro():
    """Uma barra construida com close=10 e volume=100 tem volume financeiro
    1000, INDEPENDENTE de qualquer adj_close presente na linha de origem."""
    rows = [{"trade_date": date(2020, 1, 2), "close": 10.0, "adj_close": 999.0, "volume": 100.0}]
    bars = to_bars(rows)
    assert len(bars) == 1
    assert bars[0].close == 10.0
    assert bars[0].financial_volume == 1000.0  # 10 x 100, nao 999 x 100


def _executable_strings(module) -> list[str]:
    """Constantes de string do modulo, EXCLUINDO docstrings.

    Docstring pode (e deve) citar `adj_close` para explicar a proibicao; o que
    nao pode e uma query ou literal executavel referenciar a coluna.
    """
    tree = ast.parse(inspect.getsource(module))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            body = getattr(node, "body", None)
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                docstrings.add(id(body[0].value))
    return [
        n.value
        for n in ast.walk(tree)
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings
    ]


def test_modulo_de_liquidez_nao_le_adj_close():
    for s in _executable_strings(liq_mod):
        assert "adj_close" not in s, f"analytics/liquidity.py nao pode LER adj_close: {s!r}"


def test_pipeline_de_liquidez_nao_seleciona_adj_close():
    for s in _executable_strings(liq_pipeline):
        assert "adj_close" not in s, f"pipeline nao pode selecionar adj_close: {s!r}"


def test_price_field_declarado_e_close():
    assert PRICE_FIELD == "close"


# ===========================================================================
# Janelas em PREGOES, nunca dias corridos
# ===========================================================================


def test_janela_conta_pregoes_nao_dias_corridos():
    cal = _calendar(60)
    bars = [DailyBar(d, 10.0, 100.0) for d in cal]
    rows = compute_liquidity_series(bars, cal, as_of_dates=[cal[-1]])
    assert rows[0]["expected_trading_days_20"] == 20
    assert rows[0]["expected_trading_days_60"] == 60


def test_somente_pregoes_ate_as_of():
    cal = _calendar(60)
    bars = [DailyBar(d, 10.0, 100.0) for d in cal]
    meio = cal[29]
    rows = compute_liquidity_series(bars, cal, as_of_dates=[meio])
    assert rows[0]["as_of_date"] == meio
    assert rows[0]["expected_trading_days_60"] == 30  # so o que existe ate D
    assert rows[0]["quality_flag"] == "estimated"  # janela truncada, sinalizada


def test_janela_truncada_no_inicio_da_serie_e_estimated():
    cal = _calendar(10)
    bars = [DailyBar(d, 10.0, 100.0) for d in cal]
    rows = compute_liquidity_series(bars, cal, as_of_dates=[cal[-1]])
    assert rows[0]["expected_trading_days_20"] == 10
    assert rows[0]["quality_flag"] == "estimated"
    assert "truncada" in (rows[0]["quality_reason"] or "")


# ===========================================================================
# Media sobre a janela ESPERADA (pregao sem negocio = zero)
# ===========================================================================


def test_pregao_sem_negocio_conta_como_zero_na_media():
    """Papel que negociou 10 de 20 pregoes tem media METADE da de um papel que
    negociou todos os 20 com o mesmo volume -- dividir so pelos dias negociados
    superestimaria o ilíquido."""
    cal = _calendar(20)
    denso = [DailyBar(d, 10.0, 100.0) for d in cal]
    esparso = [DailyBar(d, 10.0, 100.0) for d in cal[::2]]

    r_denso = compute_liquidity_series(denso, cal, as_of_dates=[cal[-1]])[0]
    r_esparso = compute_liquidity_series(esparso, cal, as_of_dates=[cal[-1]])[0]

    assert r_denso["trading_days_20"] == 20
    assert r_esparso["trading_days_20"] == 10
    assert float(r_esparso["avg_financial_volume_20"]) == float(
        r_denso["avg_financial_volume_20"]
    ) / 2


def test_trading_days_expoe_esparsidade():
    cal = _calendar(20)
    esparso = [DailyBar(d, 10.0, 100.0) for d in cal[:5]]
    r = compute_liquidity_series(esparso, cal, as_of_dates=[cal[-1]])[0]
    assert r["trading_days_20"] == 5
    assert r["expected_trading_days_20"] == 20


def test_volume_zero_nao_conta_como_pregao_negociado():
    cal = _calendar(20)
    bars = [DailyBar(d, 10.0, 0.0) for d in cal]
    r = compute_liquidity_series(bars, cal, as_of_dates=[cal[-1]])[0]
    assert r["trading_days_20"] == 0
    assert float(r["avg_financial_volume_20"]) == 0.0


# ===========================================================================
# Mediana de 60 -- robusta a leilao isolado
# ===========================================================================


def test_mediana_de_60_ignora_leilao_isolado_que_infla_a_media():
    cal = _calendar(60)
    bars = [DailyBar(d, 1.0, 1.0) for d in cal[:-1]]
    bars.append(DailyBar(cal[-1], 1.0, 1_000_000.0))  # leilao unico gigante
    r = compute_liquidity_series(bars, cal, as_of_dates=[cal[-1]])[0]
    assert float(r["avg_financial_volume_60"]) > 1000  # media contaminada
    assert float(r["median_financial_volume_60"]) == 1.0  # mediana resiste


# ===========================================================================
# Determinismo / idempotencia da funcao pura
# ===========================================================================


def test_calculo_e_deterministico():
    cal = _calendar(60)
    bars = [DailyBar(d, 10.0, 100.0) for d in cal]
    a = compute_liquidity_series(bars, cal, as_of_dates=cal)
    b = compute_liquidity_series(bars, cal, as_of_dates=cal)
    assert a == b


def test_to_bars_descarta_linha_sem_close_ou_volume():
    rows = [
        {"trade_date": date(2020, 1, 2), "close": None, "volume": 100.0},
        {"trade_date": date(2020, 1, 3), "close": 10.0, "volume": None},
        {"trade_date": date(2020, 1, 6), "close": 10.0, "volume": 100.0},
    ]
    assert len(to_bars(rows)) == 1


def test_as_of_fora_do_calendario_usa_ultimo_pregao_anterior():
    cal = _calendar(20)
    bars = [DailyBar(d, 10.0, 100.0) for d in cal]
    fim_de_semana = date(cal[4].year, cal[4].month, cal[4].day)
    rows = compute_liquidity_series(bars, cal, as_of_dates=[fim_de_semana])
    assert len(rows) == 1
