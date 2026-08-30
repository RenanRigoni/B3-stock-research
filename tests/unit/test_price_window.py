"""Janela canonica de preco (Fase 3 M2.1, Bloco 1).

Cobre a correcao normativa 1 do HANDOFF rev.2: preco anterior a identidade
comprovada do ticker NUNCA entra em daily_prices canonico.
"""

from __future__ import annotations

from datetime import date

from stock_research.analytics.price_window import (
    FROM_DAY,
    FROM_YEAR,
    OUT_AFTER,
    OUT_BEFORE,
    compute_price_window,
    order_name_variants,
    partition_by_window,
)
from stock_research.config import load_price_continuity_exceptions

TODAY = date(2026, 8, 30)


# ---------------------------------------------------------------------------
# Teste 4 -- variante unica: o piso e 01/01/year_first, precisao ANUAL.
# `ticker_evidence_from = NULL` (permissivo) foi REVOGADO -- ser variante unica
# no FCA nao prova o simbolo antes do primeiro ano observado.
# ---------------------------------------------------------------------------


def test_variante_unica_piso_e_jan1_do_year_first_precisao_anual():
    win = compute_price_window(
        year_first=2021,
        company_start=date(2005, 1, 1),
        company_end=None,
        class_start=date(2005, 1, 1),
        class_end=None,
        successor_year_first=None,
        continuity_from=None,
        today=TODAY,
    )
    assert win.price_valid_from == date(2021, 1, 1)
    assert win.from_precision == FROM_YEAR
    assert win.price_valid_to == TODAY
    assert win.to_precision == "open"
    assert win.basis["from"]["binding"] == "ticker_year_first"


def test_class_start_posterior_ao_year_first_vincula_com_precisao_diaria():
    win = compute_price_window(
        year_first=2018,
        company_start=date(2019, 3, 4),
        company_end=None,
        class_start=date(2019, 3, 4),
        class_end=None,
        today=TODAY,
    )
    assert win.price_valid_from == date(2019, 3, 4)
    assert win.from_precision == FROM_DAY


# ---------------------------------------------------------------------------
# Teste 5 -- CASO B: ALOS3 (year_first 2023) nao recebe nada antes de
# 2023-01-01; ALSO3 (year_first 2019) truncado em 2022-12-31.
# ---------------------------------------------------------------------------


def test_caso_b_alos3_nao_recebe_historico_do_predecessor():
    win = compute_price_window(
        year_first=2023,
        company_start=date(2011, 2, 2),
        company_end=None,
        class_start=date(2011, 2, 2),
        class_end=None,
        successor_year_first=None,
        continuity_from=None,
        today=TODAY,
    )
    assert win.price_valid_from == date(2023, 1, 1)
    assert win.from_precision == FROM_YEAR


def test_caso_b_also3_truncado_no_ano_anterior_ao_sucessor():
    win = compute_price_window(
        year_first=2019,
        company_start=date(2011, 2, 2),
        company_end=None,
        class_start=date(2011, 2, 2),
        class_end=None,
        successor_year_first=2023,
        continuity_from=None,
        today=TODAY,
    )
    assert win.price_valid_from == date(2019, 1, 1)
    assert win.price_valid_to == date(2022, 12, 31)
    assert win.to_precision == FROM_YEAR  # "year"
    assert win.basis["to"]["binding"] == "successor_truncation"


def test_order_name_variants_marca_sucessor_e_paralelas():
    rows = [
        {"ticker": "SSBR3", "source_reference_year_first": 2018},
        {"ticker": "ALSO3", "source_reference_year_first": 2019},
        {"ticker": "ALOS3", "source_reference_year_first": 2023},
    ]
    out = {r["ticker"]: r for r in order_name_variants(rows)}
    assert out["SSBR3"]["successor_year_first"] == 2019
    assert out["ALSO3"]["successor_year_first"] == 2023
    assert out["ALOS3"]["successor_year_first"] is None
    assert not any(r["parallel_variants_same_year"] for r in out.values())

    parallel = order_name_variants(
        [
            {"ticker": "BRKM5", "source_reference_year_first": 2018},
            {"ticker": "BRKM6", "source_reference_year_first": 2018},
        ]
    )
    assert all(r["successor_year_first"] is None for r in parallel)
    assert all(r["parallel_variants_same_year"] for r in parallel)


# ---------------------------------------------------------------------------
# Teste 6 -- linha do provedor ANTES de price_valid_from: nao entra,
# vira ticker_identity_not_proven.
# ---------------------------------------------------------------------------


def test_linha_antes_da_janela_nao_entra_e_e_contada():
    win = compute_price_window(
        year_first=2023,
        company_start=date(2011, 2, 2),
        company_end=None,
        class_start=date(2011, 2, 2),
        class_end=None,
        today=TODAY,
    )
    rows = [
        {"trade_date": date(2015, 6, 1), "close": 10.0},  # pre-2023 -> fora
        {"trade_date": date(2024, 6, 3), "close": 20.0},  # dentro
    ]
    inside, outside = partition_by_window(rows, win)
    assert [r["trade_date"] for r in inside] == [date(2024, 6, 3)]
    assert len(outside) == 1
    assert outside[0][1] == OUT_BEFORE


# ---------------------------------------------------------------------------
# Teste 7 -- linha do provedor DEPOIS de price_valid_to: nao entra.
# ---------------------------------------------------------------------------


def test_linha_depois_da_janela_nao_entra():
    win = compute_price_window(
        year_first=2019,
        company_start=date(2011, 2, 2),
        company_end=None,
        class_start=date(2011, 2, 2),
        class_end=date(2022, 12, 31),
        successor_year_first=2023,
        today=TODAY,
    )
    rows = [
        {"trade_date": date(2021, 5, 5)},  # dentro
        {"trade_date": date(2023, 3, 1)},  # apos 2022-12-31 -> fora
    ]
    inside, outside = partition_by_window(rows, win)
    assert [r["trade_date"] for r in inside] == [date(2021, 5, 5)]
    assert outside[0][1] == OUT_AFTER


# ---------------------------------------------------------------------------
# Excecao de continuidade -- prova independente libera preco anterior ao piso.
# ---------------------------------------------------------------------------


def test_continuidade_independente_vence_o_piso_canonico():
    win = compute_price_window(
        year_first=2018,
        company_start=date(1968, 8, 27),
        company_end=None,
        class_start=date(1968, 8, 27),
        class_end=None,
        continuity_from=date(2010, 1, 4),
        today=TODAY,
    )
    assert win.price_valid_from == date(2010, 1, 4)
    assert win.from_precision == FROM_DAY
    assert win.basis["from"]["binding"] == "continuity"


def test_continuidade_posterior_ao_piso_nao_afrouxa():
    # se a "continuidade" fosse mais tarde que o piso canonico, ela nao pode
    # abrir a janela mais cedo -- o piso canonico prevalece.
    win = compute_price_window(
        year_first=2015,
        company_start=date(2005, 1, 1),
        company_end=None,
        class_start=date(2005, 1, 1),
        class_end=None,
        continuity_from=date(2018, 1, 1),
        today=TODAY,
    )
    assert win.price_valid_from == date(2015, 1, 1)


def test_intervalo_degenerado_colapsa_sem_inverter():
    win = compute_price_window(
        year_first=2020,
        company_start=date(2019, 1, 1),
        company_end=date(2019, 6, 1),  # empresa cancelada antes do ticker existir
        class_start=date(2019, 1, 1),
        class_end=None,
        today=TODAY,
    )
    assert win.price_valid_from == date(2020, 1, 1)
    assert win.price_valid_to == date(2020, 1, 1)
    assert win.basis.get("collapsed") is True


# ---------------------------------------------------------------------------
# Guarda: a lista de excecoes de continuidade so pode conter os 5 tickers da
# Fase 1 -- nao cresce sem autorizacao (HANDOFF rev.2).
# ---------------------------------------------------------------------------


def test_lista_de_continuidade_e_exatamente_os_cinco_da_fase1():
    cfg = load_price_continuity_exceptions()
    tickers = {e["ticker"] for e in cfg["exceptions"]}
    assert tickers == {"PETR3", "PETR4", "VALE3", "ITUB3", "ITUB4"}, (
        "excecao de continuidade so cresce com autorizacao explicita do Opus"
    )
    for e in cfg["exceptions"]:
        assert e.get("proof"), f"{e['ticker']} sem prova"
        assert e.get("justification"), f"{e['ticker']} sem justificativa"
        assert e.get("instrument_id") is not None
