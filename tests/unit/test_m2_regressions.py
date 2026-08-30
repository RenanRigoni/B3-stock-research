"""Regressao dos bugs REAIS encontrados no M2 contra dados de producao.

Nenhum apareceu em fixture pequena -- todos so bateram rodando o universo
completo (2530 companhias, 1448 linhas de lifecycle, 695 instrumentos).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from stock_research.analytics.universe import (
    NO_PRICE_LINK,
    InvestabilityInputs,
    InvestabilityThresholds,
    apply_investable_gates,
    select_structural_universe,
)
from stock_research.config import CONFIG_DIR, load_universe_config

# ===========================================================================
# BUG 1 -- YAML 1.1 le `ON` como BOOLEANO
#
# `allowed_share_classes: [ON, PN, PNA, PNB]` virava `[True, 'PN', ...]`, e
# NENHUMA acao ordinaria casava o filtro: o universo estrutural de 2013 caiu de
# 586 para 151 instrumentos em silencio. Mesma classe de bug do fase2_plan.md
# §31 (`share_class: ON` gravado como a string "true" desde a Fase 1).
# ===========================================================================


def test_allowed_share_classes_nao_vira_booleano():
    cfg = load_universe_config()
    classes = cfg["eligibility"]["allowed_share_classes"]
    assert all(isinstance(c, str) for c in classes), (
        f"YAML 1.1 converteu classe em booleano: {classes} -- faltam aspas no YAML"
    )
    assert "ON" in classes


def test_yaml_do_universo_tem_on_entre_aspas():
    raw = (CONFIG_DIR / "backtest_universe_v1.yaml").read_text(encoding="utf-8")
    assert '"ON"' in raw, "ON precisa de aspas explicitas (YAML 1.1 le como boolean)"


def test_qualquer_yaml_de_config_com_ON_solto_e_detectado():
    """Guarda ampla: nenhum YAML do projeto pode ter uma palavra-chave booleana
    do YAML 1.1 usada como valor de classe de acao."""
    perigosos = {True, False}
    for path in Path(CONFIG_DIR).glob("*.yaml"):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        for chave, valor in _walk(data):
            if "share_class" in chave or "share_classes" in chave:
                vals = valor if isinstance(valor, list) else [valor]
                assert not any(v in perigosos for v in vals if isinstance(v, bool)), (
                    f"{path.name}: {chave} contem booleano -- use aspas"
                )


def _walk(node, prefix=""):
    if isinstance(node, dict):
        for k, v in node.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            yield key, v
            yield from _walk(v, key)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item, prefix)


# ===========================================================================
# BUG 2 -- instrument_id obsoleto no instrument_lifecycle
#
# O rebuild da FCA resolve `instrument_id` contra os instrumentos que existiam
# NAQUELE momento. Rodar o rebuild antes de cadastrar os 688 instrumentos
# historicos deixou 1442 de 1448 linhas com `instrument_id` NULL -- e o gate de
# price link, que precisa do id, reprovava TUDO em silencio (investable = 1 em
# vez de 5). O pipeline passou a religar apos cadastrar.
# ===========================================================================


def test_sem_instrument_id_o_price_link_reprova_e_conta():
    companies = [_company(1, "A")]
    instruments = [_inst(1, "A", instrument_id=None, ticker="AAAA3")]
    res = _run(companies, instruments, date(2020, 6, 30))
    assert len(res.instruments) == 0
    assert res.rejection_counts[NO_PRICE_LINK] == 1  # contado, nunca sumido


def test_com_instrument_id_religado_o_price_link_passa():
    companies = [_company(1, "A")]
    instruments = [_inst(1, "A", instrument_id=10, ticker="AAAA3")]
    inputs = InvestabilityInputs(price_dates={10: (date(2010, 1, 4), date(2020, 6, 30))})
    res = _run(companies, instruments, date(2020, 6, 30), inputs=inputs)
    assert [i.ticker for i in res.instruments] == ["AAAA3"]


def test_pipeline_expoe_o_relink_no_resultado():
    """O contrato do estagio `instruments` inclui religar o lifecycle -- se
    alguem remover, o resultado perde a chave e este teste quebra."""
    import inspect

    from stock_research.pipelines import historical_universe as hu

    src = inspect.getsource(hu.register_identified_instruments)
    assert "_relink_lifecycle_instruments" in src
    assert "relinked_lifecycle_rows" in src


# ===========================================================================
# BUG 3 -- teto de cancelamento vindo de registro SUPERADO
#
# 140 CNPJs tem mais de um registro no cadastro CVM. O Itau foi cancelado em
# 1998-04-08 e re-registrado em 2002-12-30. O teto do Handoff §5.2 usava
# QUALQUER cancelamento da companhia, entao aplicava 1998 como `listing_end` de
# um instrumento que comeca em 2002 -> listing_end < valid_from -> o clamp
# colapsava para intervalo de 1 dia -> ITUB3/ITUB4 fora do universo desde 2003.
# ===========================================================================


def test_instrumento_de_empresa_reregistrada_sobrevive_ao_teto_de_cancelamento():
    """Companhia com cancelamento ANTIGO (1998) e registro vigente (2002): o
    instrumento aberto em 2002 nao pode ser truncado pelo cancelamento de 1998."""
    companies = [
        # registro antigo, cancelado
        {
            **_company(3, "ITAU", valid_from="1977-07-20"),
            "valid_to": date(1998, 4, 8),
            "registration_status": "canceled",
            "event_type": "cancellation",
        },
        # registro vigente
        _company(3, "ITAU", valid_from="2002-12-30"),
    ]
    instruments = [
        _inst(3, "ITAU", instrument_id=4, ticker="ITUB4", share_class="PN",
              valid_from="2002-12-30", listing_start="2002-12-30", year_first=2018),
    ]
    inputs = InvestabilityInputs(price_dates={4: (date(2010, 1, 4), date(2020, 6, 30))})
    res = _run(companies, instruments, date(2020, 6, 30), inputs=inputs)
    assert [i.ticker for i in res.instruments] == ["ITUB4"]


def test_companhia_com_registro_vigente_permanece_elegivel():
    """O cancelamento antigo nao pode tirar a companhia do universo enquanto o
    registro mais recente estiver ativo."""
    companies = [
        {
            **_company(3, "ITAU", valid_from="1977-07-20"),
            "valid_to": date(1998, 4, 8),
            "registration_status": "canceled",
            "event_type": "cancellation",
        },
        _company(3, "ITAU", valid_from="2002-12-30"),
    ]
    res = _run(companies, [], date(2020, 6, 30))
    assert 3 in {c[0] for c in res.structural.companies}


# ---------------------------------------------------------------------------

FUTURE = "2099-01-01T00:00:00+00:00"


def _company(cid, cnpj, valid_from="2010-01-01"):
    return {
        "company_id": cid,
        "cnpj": cnpj,
        "valid_from": date.fromisoformat(valid_from),
        "valid_to": None,
        "registration_status": "registered",
        "event_type": "registration",
        "source": "cvm_cad",
        "source_available_from": FUTURE,
        "source_observed_at": FUTURE,
        "ingested_at": FUTURE,
    }


def _inst(
    cid,
    cnpj,
    *,
    instrument_id=None,
    ticker=None,
    share_class="ON",
    valid_from="2010-01-01",
    listing_start="2010-01-01",
    year_first=2018,
):
    return {
        "company_id": cid,
        "cnpj": cnpj,
        "instrument_id": instrument_id,
        "ticker": ticker,
        "share_class": share_class,
        "valid_from": date.fromisoformat(valid_from),
        "valid_to": None,
        "listing_start": date.fromisoformat(listing_start),
        "listing_end": None,
        "market": "bolsa",
        "listing_venue": "B3",
        "segment": None,
        "quality_flag": "ok",
        "source": "cvm_fca",
        "source_reference_year_first": year_first,
        "source_reference_year": 2026,
        "source_available_from": FUTURE,
        "source_observed_at": FUTURE,
        "ingested_at": FUTURE,
    }


def _run(companies, instruments, as_of, *, inputs=None):
    structural = select_structural_universe(companies, instruments, as_of)
    return apply_investable_gates(
        structural, inputs or InvestabilityInputs(), InvestabilityThresholds()
    )
