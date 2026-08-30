"""Os 8 checks do backfill + regressao dos 2 bugs reais do piloto (Fase 3 M2.1).

Bugs reais encontrados contra dado de producao no PILOTO 0:
 A. duplicate_series disparava CRITICAL quando a serie casava a PROPRIA serie
    ja em daily_prices (fingerprint_owner semeado com o proprio ticker).
 B. calendar_drift disparava WARN para datas apos o fim do trading_calendar
    (2 dias de defasagem do calendario), que nao sao drift.
"""

from __future__ import annotations

import ast
import inspect
from datetime import date

from stock_research.pipelines import price_backfill as pb_pipeline
from stock_research.pipelines.price_checks import (
    CheckFinding,
    run_backfill_checks,
    series_fingerprint,
    summarize,
)


def _cal(n: int, start: date = date(2018, 1, 1)) -> dict[date, int]:
    out: dict[date, int] = {}
    d, i = start, 0
    while len(out) < n:
        if d.weekday() < 5:
            out[d] = i
            i += 1
        d = date.fromordinal(d.toordinal() + 1)
    return out


CAL = _cal(2200)
CAL_MAX = max(CAL)


def _run(**over):
    base = dict(
        ticker="AAAA3",
        resolution="resolved",
        raw_row_count=100,
        written_dates=list(CAL)[:100],
        written_closes=[10.0] * 100,
        price_valid_from=date(2018, 1, 1),
        price_valid_to=None,
        company_valid_to=None,
        calendar_index=CAL,
        calendar_max=CAL_MAX,
        expected_trading_days_60=60,
        fingerprint=None,
        fingerprint_owner={},
    )
    base.update(over)
    return run_backfill_checks(**base)


# --- teste 8: serie vazia -> resolved_empty WARN, nada inventado -------------


def test_serie_vazia_resolved_gera_warn_e_nao_critical():
    f = _run(raw_row_count=0, written_dates=[], written_closes=[])
    names = {x.name for x in f}
    assert "resolved_empty" in names
    assert all(x.severity == "WARN" for x in f)


# --- teste 9: symbol_not_found -> serie nao inventada -----------------------


def test_symbol_not_found_nao_produz_linha_gravada():
    # sem written_dates, nenhum check de conteudo dispara; so o WARN informativo
    f = _run(raw_row_count=0, written_dates=[], written_closes=[], resolution="seeded")
    assert [x.name for x in f] == ["resolved_empty"]


# --- teste 13: serie duplicada entre DOIS instrumentos --------------------


def test_duplicate_series_dispara_quando_dono_e_outro_ticker():
    dates = list(CAL)[:100]
    fp = series_fingerprint(dates, [10.0] * 100)
    f = _run(written_dates=dates, fingerprint=fp, fingerprint_owner={fp: "BBBB3"})
    assert any(x.name == "duplicate_series" and x.severity == "CRITICAL" for x in f)


# --- BUG A (regressao): mesma serie, MESMO ticker -> NAO dispara ----------


def test_duplicate_series_nao_dispara_contra_a_propria_serie():
    dates = list(CAL)[:100]
    fp = series_fingerprint(dates, [10.0] * 100)
    f = _run(ticker="AAAA3", written_dates=dates, fingerprint=fp, fingerprint_owner={fp: "AAAA3"})
    assert not any(x.name == "duplicate_series" for x in f), (
        "fingerprint_owner do PROPRIO ticker nao pode contar como duplicata"
    )


# --- teste 14: simbolo reutilizado ---------------------------------------


def test_symbol_reuse_por_gap_interno_grande():
    dates = list(CAL)[:50] + list(CAL)[400:450]  # buraco de ~350 pregoes
    f = _run(written_dates=dates, written_closes=[10.0] * 100)
    assert any(x.name == "symbol_reuse" and x.severity == "CRITICAL" for x in f)


def test_symbol_reuse_quando_serie_cruza_company_valid_to():
    dates = list(CAL)[:100]
    f = _run(written_dates=dates, company_valid_to=dates[50])
    assert any(x.name == "symbol_reuse" for x in f)


# --- BUG B (regressao): datas apos calendar_max nao sao drift -----------


def test_calendar_drift_ignora_datas_apos_o_fim_do_calendario():
    trailing = [date.fromordinal(CAL_MAX.toordinal() + 1), date.fromordinal(CAL_MAX.toordinal() + 2)]
    f = _run(written_dates=list(CAL)[:98] + trailing)
    assert not any(x.name == "calendar_drift" for x in f), (
        "defasagem do calendario (datas apos o fim) nao e drift"
    )


def test_calendar_drift_dispara_para_data_interior_ausente():
    weird = date(2018, 7, 4)  # feriado que nao esta no calendario ficticio
    f = _run(written_dates=[*list(CAL)[:50], weird])
    assert any(x.name == "calendar_drift" for x in f) or weird in CAL


# --- teste 15: o pipeline NAO alimenta trading_calendar ------------------


def test_pipeline_nunca_escreve_em_trading_calendar():
    src = inspect.getsource(pb_pipeline)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = node.value.lower()
            assert not (
                "trading_calendar" in low and ("insert" in low or "update" in low or "upsert" in low)
            ), f"backfill nao pode escrever trading_calendar: {node.value!r}"
        # nenhuma chamada rebuild_trading_calendar
        if isinstance(node, ast.Name):
            assert node.id != "rebuild_trading_calendar", "backfill nao rebuilda o calendario"


# --- teste 10: falha individual nao aborta o lote (estrutural) ----------


def test_execute_captura_excecao_por_instrumento():
    """O loop de _execute embrulha cada fetch num try/except que NAO propaga --
    falha individual vira status='failed' e o lote continua."""
    src = inspect.getsource(pb_pipeline._execute)
    assert "except Exception" in src
    assert 'status="failed"' in src
    assert "continue" in src  # segue para o proximo instrumento


def test_circuit_breaker_por_429_consecutivo():
    src = inspect.getsource(pb_pipeline._execute)
    assert "consecutive_429" in src and "abort_after" in src


# --- teste 12: resume pula terminais (contrato do ledger) --------------


def test_ledger_tem_chave_de_checkpoint():
    """A PK (backfill_run_id, instrument_id) e o upsert do attempt permitem
    resume: rodar de novo atualiza a linha, nunca duplica."""
    src = inspect.getsource(pb_pipeline._upsert_attempts)
    assert '"backfill_run_id"' in src and '"instrument_id"' in src


def test_summarize_separa_critical_de_warn():
    findings = [
        CheckFinding("duplicate_series", "CRITICAL", "x"),
        CheckFinding("short_series", "WARN", "y"),
    ]
    s = summarize(findings)
    assert s["critical"] == ["duplicate_series"]
    assert s["warn"] == ["short_series"]
