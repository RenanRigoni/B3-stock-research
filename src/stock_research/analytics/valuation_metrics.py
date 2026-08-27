"""Métricas de valuation derivadas dos fundamentos (fase2_plan.md 5-7).

Estende ``fundamental_metrics`` com um ``calculation_version`` próprio
(``valuation_metrics_v1``) -- as linhas da Fase 1 (``fundamental_metrics_v1``)
não são tocadas, ficam lado a lado na mesma tabela.

Métricas produzidas, todas por ``(reference_date, period_type)``:

    da                  -- Depreciação, Amortização e Exaustão do período (DFC_MI 6.01.01;
                           fallback DFC_MD; fallback abs(DVA 7.0x.01) com flag 'estimated')
    ebitda              -- EBIT + D&A
    pretax_income       -- "Resultado Antes dos Tributos sobre o Lucro" (DRE)
    income_tax          -- "Imposto de Renda e Contribuição Social sobre o Lucro" (DRE, negativo)
    effective_tax_rate  -- abs(income_tax) / pretax_income   (só quando pretax_income > 0)
    nopat               -- EBIT * (1 - effective_tax_rate)
    invested_capital    -- gross_debt + equity - cash
    roic                -- nopat / invested_capital

Bancos (``instruments.financial_company``): ``ebitda``, ``nopat``,
``invested_capital`` e ``roic`` NÃO se aplicam -- gravados com
``quality_flag='sector_inadequate'`` e valor NULL, mesma disciplina de
``ebit`` na Fase 1 (fase2_plan.md 7, 9). ``effective_tax_rate``,
``pretax_income`` e ``income_tax`` continuam sendo calculados para banco
(instituição financeira paga imposto; o que não se aplica é o ROIC).

D&A absente, ``pretax_income <= 0`` ou ``invested_capital == 0`` ->
``quality_flag='missing_input'``, nunca valor inventado (fase2_plan.md 6, 7).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from stock_research.analytics import ttm
from stock_research.analytics.fundamentals_metrics import (
    CASH_DESC,
    DEBT_BRANCHES,
    DEBT_DESC,
    EBIT_DESC,
    EQUITY_DESC,
    _flow_slice,
    _match_one,
    _norm,
    _scaled,
    _sum_per_branch,
)
from stock_research.db import fetch_all, fetch_one, finish_run, start_run, upsert_many
from stock_research.logging import get_logger

logger = get_logger(__name__)

PIPELINE = "valuation_metrics"
CALCULATION_VERSION = "valuation_metrics_v1"
# Métricas da Fase 1 cujo TTM montamos aqui (via extra_ttm_series).
_TTM_FROM_PHASE1 = ("net_income", "free_cash_flow", "revenue")

# D&A -- ramo 6.01.01 do DFC (add-back ao lucro liquido), casado por descricao
# normalizada porque o codigo do subitem migra ao longo dos anos (fase2_plan.md 28).
DA_DFC_DESC = [
    "Depreciação, Depleção e Amortização",
    "Depreciação, amortização e exaustão",
    "Depreciações e Amortizações",
    "Depreciação e Amortizações",
    "Depreciação, Amortização e Exaustão",
]
DA_DFC_BRANCH = "6.01.01"
# Fallback: DVA. Sinal NEGATIVO (deducao do valor adicionado) -> abs(). Valor
# bruto (inclui parcela capitalizada), por isso 'estimated'.
DA_DVA_DESC = ["Depreciação, Amortização e Exaustão"]
DA_DVA_BRANCH = "7."

PRETAX_DESC = ["Resultado Antes dos Tributos sobre o Lucro"]
INCOME_TAX_DESC = ["Imposto de Renda e Contribuição Social sobre o Lucro"]

_SECTOR_INADEQUATE_FOR_BANKS = {"ebitda", "nopat", "invested_capital", "roic"}
_RATIO_METRICS = {"effective_tax_rate", "roic"}


def _match_in_branch(
    facts: list[dict[str, Any]], statement_type: str, branch: str, descriptions: list[str]
) -> dict[str, Any] | None:
    """Match mais raso dentro de um ramo de ``CD_CONTA`` (ex.: ``6.01.01``),
    por descrição normalizada. Igual em espírito a ``_match_one``, mas restrito
    ao ramo -- o código exato do subitem não é estável entre anos."""
    targets = {_norm(d) for d in descriptions}
    candidates = [
        f
        for f in facts
        if f["statement_type"] == statement_type
        and f["account_code"].startswith(branch)
        and _norm(f["account_description"]) in targets
    ]
    return min(candidates, key=lambda f: f["account_code"].count(".")) if candidates else None


def _da_for_slice(facts: list[dict[str, Any]]) -> tuple[Decimal | None, list[int], str, str | None]:
    """``(valor, doc_ids, quality_flag, quality_reason)`` do D&A do período."""
    for stmt in ("DFC_MI", "DFC_MD"):
        hit = _match_in_branch(facts, stmt, DA_DFC_BRANCH, DA_DFC_DESC)
        if hit is not None:
            value = _scaled(hit)
            if value is not None:
                return value, _docs(hit), "ok", None

    dva = _match_in_branch(facts, "DVA", DA_DVA_BRANCH, DA_DVA_DESC)
    if dva is not None:
        value = _scaled(dva)
        if value is not None:
            return (
                abs(value),
                _docs(dva),
                "estimated",
                "D&A da DVA (bruto, sinal invertido) -- DFC não trouxe a linha de add-back",
            )
    return None, [], "missing_input", "nenhuma linha de D&A reconhecida no DFC nem na DVA"


def _docs(fact: dict[str, Any] | None) -> list[int]:
    return [fact["document_id"]] if fact and fact.get("document_id") else []


def _emit(
    rows: list[dict[str, Any]],
    *,
    instrument_id: int,
    company_id: int | None,
    reference_date: date,
    available_from: Any,
    period_type: str,
    name: str,
    value: Any,
    doc_ids: list[int],
    flag: str = "ok",
    reason: str | None = None,
) -> None:
    rows.append(
        {
            "instrument_id": instrument_id,
            "company_id": company_id,
            "reference_date": reference_date,
            "available_from": available_from,
            "period_type": period_type,
            "metric_name": name,
            "metric_value": value,
            "unit": "ratio" if name in _RATIO_METRICS else "BRL",
            "calculation_version": CALCULATION_VERSION,
            "source_document_ids": sorted(set(doc_ids)) or None,
            "quality_flag": flag,
            "quality_reason": reason,
            "run_id": None,
        }
    )


def _metrics_for_group(
    group: list[dict[str, Any]],
    *,
    instrument_id: int,
    company_id: int | None,
    financial_company: bool,
) -> list[dict[str, Any]]:
    current = [f for f in group if f["fiscal_year_order"] == "ULTIMO"]
    if not current:
        return []
    document_type = current[0]["document_type"]
    reference_date = current[0]["reference_date"]
    available_from = max(
        (f["available_from"] for f in current if f["available_from"]), default=None
    )
    if available_from is None:
        return []

    period_type = "annual" if document_type == "DFP" else "ytd"
    primary = _flow_slice(current, primary=True)
    rows: list[dict[str, Any]] = []

    def emit(
        name: str, value: Any, doc_ids: list[int], flag: str = "ok", reason: str | None = None
    ) -> None:
        _emit(
            rows,
            instrument_id=instrument_id,
            company_id=company_id,
            reference_date=reference_date,
            available_from=available_from,
            period_type=period_type,
            name=name,
            value=value,
            doc_ids=doc_ids,
            flag=flag,
            reason=reason,
        )

    ebit_f = _match_one(primary, "DRE", EBIT_DESC)
    pretax_f = _match_one(primary, "DRE", PRETAX_DESC)
    tax_f = _match_one(primary, "DRE", INCOME_TAX_DESC)
    equity_f = _match_one(current, "BPP", EQUITY_DESC)
    cash_f = _match_one(current, "BPA", CASH_DESC)
    debt_total, debt_docs = _sum_per_branch(current, "BPP", DEBT_DESC, DEBT_BRANCHES)

    ebit = _scaled(ebit_f) if ebit_f else None
    pretax = _scaled(pretax_f) if pretax_f else None
    tax = _scaled(tax_f) if tax_f else None
    equity = _scaled(equity_f) if equity_f else None
    cash = _scaled(cash_f) if cash_f else None

    da_value, da_docs, da_flag, da_reason = _da_for_slice(primary)
    emit("da", da_value, da_docs, da_flag, da_reason)

    emit(
        "pretax_income",
        pretax,
        _docs(pretax_f),
        *_ok_or_missing(pretax, "conta 'Resultado Antes dos Tributos' não encontrada no DRE"),
    )
    emit(
        "income_tax",
        tax,
        _docs(tax_f),
        *_ok_or_missing(tax, "conta de imposto de renda não encontrada no DRE"),
    )

    if pretax is not None and pretax > 0 and tax is not None:
        etr = abs(tax) / pretax
        emit(
            "effective_tax_rate",
            etr,
            _docs(pretax_f) + _docs(tax_f),
            "ok",
            "derivado: abs(income_tax) / pretax_income",
        )
    else:
        etr = None
        emit(
            "effective_tax_rate", None, [], "missing_input", "requer pretax_income > 0 e income_tax"
        )

    if financial_company:
        for name in _SECTOR_INADEQUATE_FOR_BANKS:
            emit(
                name,
                None,
                [],
                "sector_inadequate",
                "instituição financeira: métrica não se aplica (fase2_plan.md 7, 9)",
            )
        return rows

    if ebit is not None and da_value is not None:
        # herda 'estimated' quando o D&A veio da DVA (bruto), não do add-back do DFC.
        ebitda_flag = "estimated" if da_flag == "estimated" else "ok"
        ebitda_reason = "derivado: EBIT + D&A" + (
            "; D&A estimado (DVA)" if da_flag == "estimated" else ""
        )
        emit("ebitda", ebit + da_value, _docs(ebit_f) + da_docs, ebitda_flag, ebitda_reason)
    else:
        emit("ebitda", None, [], "missing_input", "requer EBIT e D&A")

    if ebit is not None and etr is not None:
        emit(
            "nopat",
            ebit * (Decimal(1) - etr),
            _docs(ebit_f),
            "ok",
            "derivado: EBIT * (1 - effective_tax_rate)",
        )
    else:
        emit("nopat", None, [], "missing_input", "requer EBIT e effective_tax_rate")

    if debt_total is not None and equity is not None and cash is not None:
        invested = debt_total + equity - cash
        emit(
            "invested_capital",
            invested,
            sorted(set(debt_docs) | set(_docs(equity_f)) | set(_docs(cash_f))),
            "ok",
            "derivado: gross_debt + equity - cash",
        )
    else:
        invested = None
        emit("invested_capital", None, [], "missing_input", "requer gross_debt, equity e cash")

    nopat_row = next((r for r in rows if r["metric_name"] == "nopat"), None)
    nopat_val = nopat_row["metric_value"] if nopat_row else None
    if nopat_val is not None and invested is not None and invested != 0:
        emit(
            "roic",
            Decimal(nopat_val) / invested,
            [],
            "ok",
            "derivado: nopat / invested_capital (invested_capital de fim de período)",
        )
    else:
        emit("roic", None, [], "missing_input", "requer nopat e invested_capital != 0")

    return rows


def _ok_or_missing(value: Any, reason: str) -> tuple[str, str | None]:
    return ("ok", None) if value is not None else ("missing_input", reason)


# Métricas de fluxo cujo TTM (soma de 4 trimestres) faz sentido. `revenue`,
# `net_income` e `free_cash_flow` vêm da Fase 1 (via `extra_series`).
_TTM_FLOW_METRICS = ("da", "ebitda", "pretax_income", "income_tax", "nopat")


def _row_to_point(row: dict[str, Any]) -> ttm.Point | None:
    if row.get("metric_value") is None:
        return None
    return ttm.Point(
        Decimal(str(row["metric_value"])), row.get("available_from"), row.get("quality_flag", "ok")
    )


def _series_from_rows(
    rows: list[dict[str, Any]], metric: str
) -> tuple[dict[int, ttm.Point], dict[tuple[int, int], ttm.Point]]:
    annual: dict[int, ttm.Point] = {}
    ytd: dict[tuple[int, int], ttm.Point] = {}
    for r in rows:
        if r["metric_name"] != metric:
            continue
        pt = _row_to_point(r)
        if pt is None:
            continue
        rd: date = r["reference_date"]
        if r["period_type"] == "annual":
            annual[rd.year] = pt
        elif r["period_type"] == "ytd":
            q = ttm.quarter_of(rd)
            if q in (1, 2, 3):
                ytd[(rd.year, q)] = pt
    return annual, ytd


def _derive_ttm_rows(
    base_rows: list[dict[str, Any]],
    *,
    instrument_id: int,
    company_id: int | None,
    extra_series: dict[str, tuple[dict[int, ttm.Point], dict[tuple[int, int], ttm.Point]]]
    | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def emit(name: str, rd: date, pt: ttm.Point, reason: str) -> None:
        _emit(
            out,
            instrument_id=instrument_id,
            company_id=company_id,
            reference_date=rd,
            available_from=pt.available_from,
            period_type="ttm",
            name=name,
            value=pt.value,
            doc_ids=[],
            flag=pt.quality_flag,
            reason=reason,
        )

    series: dict[str, dict[date, ttm.Point]] = {}
    for metric in _TTM_FLOW_METRICS:
        annual, ytd = _series_from_rows(base_rows, metric)
        series[metric] = ttm.assemble_ttm(annual, ytd)
    for metric, (annual, ytd) in (extra_series or {}).items():
        series[metric] = ttm.assemble_ttm(annual, ytd)

    for metric, points in series.items():
        for rd, pt in points.items():
            emit(metric, rd, pt, "soma dos 4 trimestres isolados terminando em " + rd.isoformat())

    # derivadas TTM
    pretax_ttm = series.get("pretax_income", {})
    tax_ttm = series.get("income_tax", {})
    for rd, pre in pretax_ttm.items():
        tx = tax_ttm.get(rd)
        if tx is not None and pre.value > 0:
            af, flag = ttm.combine(pre, tx)
            emit(
                "effective_tax_rate",
                rd,
                ttm.Point(abs(tx.value) / pre.value, af, flag),
                "derivado: abs(income_tax TTM) / pretax_income TTM",
            )

    nopat_ttm = series.get("nopat", {})
    invested_annual, _ = _series_from_rows(base_rows, "invested_capital")
    for rd, npt in nopat_ttm.items():
        inv = _nearest_annual(invested_annual, rd.year)
        if inv is not None and inv.value != 0:
            af, flag = ttm.combine(npt, inv)
            emit(
                "roic",
                rd,
                ttm.Point(npt.value / inv.value, af, flag),
                "derivado: nopat TTM / invested_capital (último anual <= o ano do TTM)",
            )
    return out


def _nearest_annual(annual: dict[int, ttm.Point], year: int) -> ttm.Point | None:
    candidates = [y for y in annual if y <= year]
    return annual[max(candidates)] if candidates else None


def _phase1_ttm_series(
    instrument_id: int,
) -> dict[str, tuple[dict[int, ttm.Point], dict[tuple[int, int], ttm.Point]]]:
    """Séries anuais + YTD de ``net_income``/``free_cash_flow``/``revenue``
    já calculadas pela Fase 1 (``fundamental_metrics_v1``), para montar o TTM
    delas aqui."""
    rows = fetch_all(
        "select distinct on (metric_name, period_type, reference_date) "
        "metric_name, period_type, reference_date, metric_value, available_from, quality_flag "
        "from public.fundamental_metrics "
        "where instrument_id = %s and calculation_version = 'fundamental_metrics_v1' "
        "and metric_name in ('net_income', 'free_cash_flow', 'revenue') "
        "and period_type in ('annual', 'ytd') and metric_value is not null "
        "order by metric_name, period_type, reference_date, available_from desc",
        [instrument_id],
    )
    out: dict[str, tuple[dict[int, ttm.Point], dict[tuple[int, int], ttm.Point]]] = {
        m: ({}, {}) for m in _TTM_FROM_PHASE1
    }
    for r in rows:
        pt = ttm.Point(
            Decimal(str(r["metric_value"])), r["available_from"], r["quality_flag"] or "ok"
        )
        annual, ytd = out[r["metric_name"]]
        rd: date = r["reference_date"]
        if r["period_type"] == "annual":
            annual[rd.year] = pt
        else:
            q = ttm.quarter_of(rd)
            if q in (1, 2, 3):
                ytd[(rd.year, q)] = pt
    return out


def compute_valuation_metrics_for_facts(
    facts: list[dict[str, Any]],
    *,
    instrument_id: int,
    company_id: int | None,
    financial_company: bool,
    extra_ttm_series: dict[str, tuple[dict[int, ttm.Point], dict[tuple[int, int], ttm.Point]]]
    | None = None,
) -> list[dict[str, Any]]:
    """Função pura: fatos consolidados de um instrumento -> linhas de
    ``fundamental_metrics`` com ``calculation_version='valuation_metrics_v1'``,
    incluindo ``period_type='ttm'``.

    ``extra_ttm_series``: séries anuais+YTD já calculadas pela Fase 1
    (``net_income``, ``free_cash_flow``, ``revenue``) para o TTM delas.

    Mesma limitação de ``compute_metrics_for_facts`` da Fase 1: usa a versão
    mais recente de cada ``(reference_date, account_code)`` -- para análise
    point-in-time real, consulte os fatos brutos via ``get_fundamentals_as_of``.
    """
    groups: dict[tuple[Any, str], list[dict[str, Any]]] = {}
    for f in facts:
        groups.setdefault((f["reference_date"], f["version"]), []).append(f)

    by_key: dict[tuple[str, str, Any], dict[str, Any]] = {}
    for key in sorted(groups, key=lambda k: (k[0], k[1])):
        for row in _metrics_for_group(
            groups[key],
            instrument_id=instrument_id,
            company_id=company_id,
            financial_company=financial_company,
        ):
            by_key[(row["period_type"], row["metric_name"], row["reference_date"])] = row

    base = list(by_key.values())
    for row in _derive_ttm_rows(
        base, instrument_id=instrument_id, company_id=company_id, extra_series=extra_ttm_series
    ):
        by_key[(row["period_type"], row["metric_name"], row["reference_date"])] = row
    return list(by_key.values())


def compute_and_store_valuation_metrics(
    ticker: str, *, run_id: int | None = None
) -> dict[str, int]:
    """Recalcula e grava as métricas de valuation para um instrumento (upsert)."""
    instrument = fetch_one(
        "select instrument_id, company_id, financial_company "
        "from public.instruments where ticker = %s and active = true",
        [ticker.upper()],
    )
    if instrument is None:
        raise ValueError(f"instrumento ativo não cadastrado: {ticker}")

    owns_run = run_id is None
    if owns_run:
        run_id = start_run(PIPELINE, ticker=ticker)
    assert run_id is not None
    try:
        facts = fetch_all(
            "select fact_id, document_id, document_type, statement_type, reference_date, "
            "period_start, period_end, version, account_code, account_description, value, scale, "
            "fiscal_year_order, available_from from public.financial_statement_facts "
            "where instrument_id = %s and is_consolidated = true",
            [instrument["instrument_id"]],
        )
        extra = _phase1_ttm_series(instrument["instrument_id"])
        rows = compute_valuation_metrics_for_facts(
            facts,
            instrument_id=instrument["instrument_id"],
            company_id=instrument["company_id"],
            financial_company=bool(instrument["financial_company"]),
            extra_ttm_series=extra,
        )
        for r in rows:
            r["run_id"] = run_id
        stats = (
            upsert_many(
                "fundamental_metrics",
                rows,
                conflict_columns=[
                    "instrument_id",
                    "reference_date",
                    "period_type",
                    "metric_name",
                    "calculation_version",
                ],
                update_columns=[
                    "company_id",
                    "available_from",
                    "metric_value",
                    "unit",
                    "source_document_ids",
                    "quality_flag",
                    "quality_reason",
                    "run_id",
                ],
            )
            if rows
            else {"inserted": 0, "updated": 0, "total": 0}
        )
        if owns_run:
            finish_run(
                run_id, status="success", records_raw=len(facts), records_inserted=stats["total"]
            )
        return {"facts": len(facts), **stats}
    except Exception as exc:
        if owns_run:
            finish_run(run_id, status="failed", error_message=str(exc))
        raise
