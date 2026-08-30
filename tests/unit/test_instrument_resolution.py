"""Resolucao de instrumento -- ``resolution_status(row, D)``.

Pre-requisitos 2 e 3 do M2 (Opus, "structural vs investable"). Dois bugs reais
achados contra dado da CVM viram regressao aqui:

BUG 1 -- ``Codigo_Negociacao`` e texto livre. 82 das 778 linhas com ticker no
    ``instrument_lifecycle`` nao sao codigo nenhum: ``000000``, ``NAO HA``,
    ``N/A``, ``1545-8``, ``713854``, ``SEIVA``. O M1 gravou verbatim (correto --
    a fonte se preserva). Deixar isso virar identificador em ``instruments`` ou
    ligar serie de preco seria desastre.

BUG 2 -- ticker futuro retroagido. ``ALOS3`` tem ``valid_from = 2011-02-02``
    (data de listagem da CLASSE) mas so aparece na FCA de 2023. Perguntar o
    universo de 2013 nao pode devolver ``ALOS3`` -- em 2013 a empresa negociava
    como ``SSBR3``, e nem isso sabemos pela FCA (que so publica codigo em
    2018+).
"""

from __future__ import annotations

from datetime import date

from stock_research.transforms.instrument_lifecycle import (
    BACK_PROJECTED,
    IDENTIFIABLE,
    RESOLVED,
    SEEDED,
    UNRESOLVED_INVALID_CODE,
    UNRESOLVED_NO_CODE,
    is_valid_ticker,
    merge_instrument_intervals,
    resolution_status,
)

D2013 = date(2013, 6, 15)
D2020 = date(2020, 6, 15)
D2024 = date(2024, 6, 15)


def _row(ticker, *, year_first=2018, source="cvm_fca"):
    return {"ticker": ticker, "source_reference_year_first": year_first, "source": source}


# ===========================================================================
# BUG 1 -- lixo no Codigo_Negociacao nunca vira identificador
# ===========================================================================

# Valores REAIS extraidos do instrument_lifecycle apos ingerir a FCA 2010-2026.
LIXO_REAL = [
    "NÃO", "NÃO HÁ", "N/A", "0", "00", "0000", "00000", "000000",
    "007424", "021725", "1545-8", "13471", "24201A", "5.4", "713854",
    "SEIVA", "BRQB", "JOPA", "SMFT", "SJOS", "8192",
]


def test_valores_lixo_reais_nunca_sao_ticker_valido():
    for v in LIXO_REAL:
        assert not is_valid_ticker(v), f"{v!r} nao pode passar como ticker"


def test_valores_lixo_reais_viram_unresolved_invalid_code():
    for v in LIXO_REAL:
        assert resolution_status(_row(v), D2024) == UNRESOLVED_INVALID_CODE


def test_lixo_nunca_e_identificavel_em_nenhuma_data():
    for v in LIXO_REAL:
        for d in (D2013, D2020, D2024):
            assert resolution_status(_row(v), d) not in IDENTIFIABLE


def test_codigos_b3_bem_formados_sao_validos():
    for v in ("PETR4", "VALE3", "ITUB4", "ALOS3", "MGLU3", "BPAC11"):
        assert is_valid_ticker(v), f"{v!r} deveria ser valido"


def test_sufixo_b_do_bovespa_mais_e_valido_nao_lixo():
    """ETRO3B/QVQP3B/OPSE3B sao codigos REAIS de Bovespa Mais / balcao --
    descartar como lixo perderia instrumento legitimo."""
    for v in ("ETRO3B", "QVQP3B", "OPSE3B", "BNPA3B", "UPKP3B", "ALEF3B"):
        assert is_valid_ticker(v), f"{v!r} e codigo real de Bovespa Mais"
        assert resolution_status(_row(v), D2024) == RESOLVED


def test_ticker_none_e_unresolved_no_code_nao_invalid():
    assert resolution_status(_row(None), D2024) == UNRESOLVED_NO_CODE


# ===========================================================================
# BUG 2 -- ticker futuro nunca retroage
# ===========================================================================


def test_ticker_observado_so_em_2023_e_back_projected_em_2013():
    alos3 = _row("ALOS3", year_first=2023)
    assert resolution_status(alos3, D2013) == BACK_PROJECTED
    assert resolution_status(alos3, D2020) == BACK_PROJECTED
    assert resolution_status(alos3, D2024) == RESOLVED


def test_sequencia_real_allos_ssbr3_also3_alos3():
    """Caso real (company_id 81): os tres tickers tem o MESMO valid_from
    (2011-02-02, listagem da classe) e so se distinguem pelo ano da FCA."""
    ssbr3 = _row("SSBR3", year_first=2018)
    also3 = _row("ALSO3", year_first=2019)
    alos3 = _row("ALOS3", year_first=2023)

    # 2013: nenhum e conhecivel -- a FCA nao publicava codigo.
    assert {resolution_status(r, D2013) for r in (ssbr3, also3, alos3)} == {BACK_PROJECTED}
    # 2020: SSBR3 e ALSO3 ja observados; ALOS3 ainda nao existe como codigo.
    assert resolution_status(also3, D2020) == RESOLVED
    assert resolution_status(alos3, D2020) == BACK_PROJECTED
    # 2024: todos observados.
    assert resolution_status(alos3, D2024) == RESOLVED


def test_year_first_ausente_e_back_projected_nunca_resolved():
    """Sem saber quando o ticker foi observado, o conservador e recusar."""
    assert resolution_status(_row("PETR4", year_first=None), D2024) == BACK_PROJECTED


def test_fronteira_do_ano_e_inclusiva():
    r = _row("XPTO3", year_first=2018)
    assert resolution_status(r, date(2018, 1, 1)) == RESOLVED
    assert resolution_status(r, date(2017, 12, 31)) == BACK_PROJECTED


# ===========================================================================
# seed_manual
# ===========================================================================


def test_seed_manual_e_seeded_e_identificavel():
    vale5 = _row("VALE5", year_first=2000, source="seed_manual")
    assert resolution_status(vale5, D2013) == SEEDED
    assert SEEDED in IDENTIFIABLE


def test_seed_manual_ganha_de_qualquer_outro_criterio():
    """Curadoria manual e autoritativa -- nao passa pelo regex nem pelo ano."""
    esquisito = _row("XX", year_first=2099, source="seed_manual")
    assert resolution_status(esquisito, D2013) == SEEDED


# ===========================================================================
# source_reference_year_first no merge (pre-requisito 1)
# ===========================================================================


def _cand(ticker, year):
    return {
        "cnpj": "11.111.111/0001-11",
        "share_class": "ON",
        "ticker": ticker,
        "valid_from": date(2011, 2, 2),
        "valid_to": None,
        "listing_start": date(2011, 2, 2),
        "listing_end": None,
        "market": "bolsa",
        "segment": None,
        "listing_venue": "B3",
        "source_reference_year": year,
        "source_reference_year_first": year,
        "source_available_from": None,
        "source_observed_at": None,
        "quality_flag": "ok",
        "quality_reason": None,
    }


def test_merge_guarda_menor_ano_em_year_first_e_maior_em_year():
    merged = merge_instrument_intervals([_cand("X3", 2020), _cand("X3", 2018), _cand("X3", 2026)])
    assert len(merged) == 1
    assert merged[0]["source_reference_year_first"] == 2018  # menor: quando foi observado
    assert merged[0]["source_reference_year"] == 2026  # maior: conhecimento mais recente


def test_year_first_e_por_ticker_nao_global():
    """Usar o piso global (2018, primeiro ano em que a FCA publica codigo)
    marcaria ALOS3 como observavel em 2018 -- falso, so aparece em 2023."""
    merged = merge_instrument_intervals(
        [_cand("SSBR3", 2018), _cand("ALSO3", 2019), _cand("ALOS3", 2023)]
    )
    by_ticker = {m["ticker"]: m["source_reference_year_first"] for m in merged}
    assert by_ticker == {"SSBR3": 2018, "ALSO3": 2019, "ALOS3": 2023}
