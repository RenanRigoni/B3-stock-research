"""Backfill historico de precos -- selecao de candidatos e guardas (Fase 3 M2.1).

Cobre os testes 1-3 e 19 do HANDOFF rev.2:
 1. selecao admite so resolved/seeded + ticker valido + company_id + instrument_id
 2. nenhum modulo M2.1 LE instruments.active
 3. nenhum modulo M2.1 ESCREVE instruments.active
19. bitemporal: +-10 anos em source_* nao muda a composicao do lote nem a janela
"""

from __future__ import annotations

import ast
import inspect
from datetime import date

from stock_research.analytics import price_backfill as pb_mod
from stock_research.analytics import price_window as pw_mod
from stock_research.analytics.price_backfill import select_backfill_candidates
from stock_research.analytics.price_window import compute_price_window
from stock_research.pipelines import price_backfill as pb_pipeline
from stock_research.pipelines import price_window as pw_pipeline

TODAY = date(2026, 8, 30)
FUTURE = "2099-01-01T00:00:00+00:00"


def _lc_row(**over):
    base = {
        "instrument_id": 10,
        "company_id": 1,
        "ticker": "AAAA3",
        "share_class": "ON",
        "valid_from": date(2011, 1, 1),
        "valid_to": None,
        "listing_start": date(2011, 1, 1),
        "listing_end": None,
        "source": "cvm_fca",
        "source_reference_year_first": 2018,
        "source_available_from": FUTURE,
        "source_observed_at": FUTURE,
        "ingested_at": FUTURE,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Teste 1 -- criterio de selecao
# ---------------------------------------------------------------------------


def test_selecao_admite_resolved_e_seeded():
    rows = [
        _lc_row(instrument_id=1, ticker="AAAA3", source_reference_year_first=2015),  # resolved em 2020
        _lc_row(instrument_id=2, ticker="VALE5", source="seed_manual", source_reference_year_first=2000),
    ]
    got = {c.instrument_id for c in select_backfill_candidates(rows, date(2020, 6, 30))}
    assert got == {1, 2}


def test_selecao_rejeita_back_projected():
    rows = [_lc_row(instrument_id=3, ticker="BBBB3", source_reference_year_first=2023)]
    assert select_backfill_candidates(rows, date(2020, 6, 30)) == []


def test_selecao_rejeita_ticker_invalido_no_code_e_sem_ids():
    rows = [
        _lc_row(instrument_id=4, ticker="000000"),
        _lc_row(instrument_id=5, ticker=None),
        _lc_row(instrument_id=None, ticker="CCCC3"),
        _lc_row(instrument_id=6, company_id=None, ticker="DDDD3"),
    ]
    assert select_backfill_candidates(rows, date(2020, 6, 30)) == []


def test_selecao_dedupe_por_instrument_id_e_ordena():
    rows = [
        _lc_row(instrument_id=9, ticker="ZZZZ3", source_reference_year_first=2018),
        _lc_row(instrument_id=9, ticker="ZZZZ3", source_reference_year_first=2019),
        _lc_row(instrument_id=7, ticker="YYYY3", source_reference_year_first=2018),
    ]
    got = [c.instrument_id for c in select_backfill_candidates(rows, date(2021, 1, 1))]
    assert got == [7, 9]


# ---------------------------------------------------------------------------
# Testes 2 e 3 -- guarda: M2.1 nunca toca instruments.active
# ---------------------------------------------------------------------------

_M21_MODULES = [pw_mod, pb_mod, pw_pipeline, pb_pipeline]


def _executable_strings(module) -> list[str]:
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


def test_m21_nao_referencia_instruments_active_em_sql():
    for mod in _M21_MODULES:
        for s in _executable_strings(mod):
            low = s.lower()
            assert "instruments" not in low or "active" not in low, (
                f"{mod.__name__}: string executavel toca instruments.active: {s!r}"
            )
            assert "set active" not in low and '"active"' not in low, (
                f"{mod.__name__}: escreve coluna active: {s!r}"
            )


def test_m21_nao_acessa_atributo_active():
    for mod in _M21_MODULES:
        tree = ast.parse(inspect.getsource(mod))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                assert node.attr != "active", f"{mod.__name__}: acesso a .active"
            if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant):
                assert node.slice.value != "active", f"{mod.__name__}: indexa ['active']"


# ---------------------------------------------------------------------------
# Teste 19 -- invariancia bitemporal
# ---------------------------------------------------------------------------


def test_composicao_do_lote_invariante_a_proveniencia():
    base = [
        _lc_row(instrument_id=1, ticker="AAAA3", source_reference_year_first=2018),
        _lc_row(instrument_id=2, ticker="BBBB3", source_reference_year_first=2020),
    ]
    shifted_past = [
        {**r, "source_available_from": "1990-01-01T00:00:00+00:00",
         "source_observed_at": "1990-01-01T00:00:00+00:00",
         "ingested_at": "1990-01-01T00:00:00+00:00"}
        for r in base
    ]
    shifted_future = [
        {**r, "source_available_from": "2099-01-01T00:00:00+00:00",
         "source_observed_at": "2099-01-01T00:00:00+00:00",
         "ingested_at": "2099-01-01T00:00:00+00:00"}
        for r in base
    ]
    as_of = date(2021, 6, 30)
    a = [c.instrument_id for c in select_backfill_candidates(base, as_of)]
    b = [c.instrument_id for c in select_backfill_candidates(shifted_past, as_of)]
    c = [c.instrument_id for c in select_backfill_candidates(shifted_future, as_of)]
    assert a == b == c == [1, 2]


def test_janela_invariante_a_proveniencia():
    # compute_price_window nem recebe colunas de proveniencia -- a invariancia
    # e estrutural. Este teste fixa isso: mesmos argumentos efetivos -> mesma janela.
    kw = dict(
        year_first=2019,
        company_start=date(2011, 2, 2),
        company_end=None,
        class_start=date(2011, 2, 2),
        class_end=None,
        today=TODAY,
    )
    assert compute_price_window(**kw) == compute_price_window(**kw)


def test_m21_nao_referencia_colunas_de_proveniencia():
    banned = ("source_available_from", "source_observed_at", "ingested_at")
    for mod in _M21_MODULES:
        for s in _executable_strings(mod):
            for col in banned:
                assert col not in s, f"{mod.__name__}: referencia proveniencia {col!r}: {s!r}"
