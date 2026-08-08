"""Metricas fundamentalistas derivadas (fase1.md 49-51).

Mapeamento conta->conceito por DESCRICAO normalizada (``DS_CONTA``), nunca por
posicao de ``CD_CONTA``: validado contra o DFP 2024 real de PETR4/VALE3/ITUB4
(fase1.md 45) que o plano de contas da CVM e "elastico" -- o mesmo conceito
economico aparece em codigos diferentes por empresa (ex.: "Caixa e
Equivalentes de Caixa" e ``1.01.01`` em PETR4/VALE3 mas ``1.01`` em ITUB4,
banco). A descricao e estavel; a posicao do codigo nao e.

Formulas (todas documentadas aqui, fase1.md 49):

    net_debt            = gross_debt - cash
    free_cash_flow       = operating_cash_flow + capex   (capex ja vem negativo, convencao da CVM)
    liabilities          = assets - equity                (identidade contabil: Passivo Total = Ativo Total)
    net_margin           = net_income / revenue
    roe                  = net_income / equity             (equity de fim de periodo, nao media -- simplificacao documentada)
    revenue_growth_yoy   = (revenue_atual - revenue_penultimo) / abs(revenue_penultimo)
    net_income_growth_yoy = idem, para net_income

``PENULTIMO`` (o periodo comparativo que a propria CVM entrega no mesmo
arquivo) fornece o "ano anterior" para os _growth_yoy sem precisar de uma
segunda consulta ao banco.

Bancos e seguradoras (``instruments.financial_company``): ``gross_debt``,
``net_debt``, ``capex`` e ``free_cash_flow`` NAO se aplicam (fase1.md 50) --
a linha e gravada com ``quality_flag='sector_inadequate'`` e valor NULL em vez
de calculada, mesmo que uma conta parecida exista. ``ebit`` tambem entra
nessa lista: "resultado antes do resultado financeiro" nao faz sentido para
uma instituicao cujo negocio principal E resultado financeiro.

Trimestre isolado (fase1.md 44): revenue/ebit/net_income usam o trimestre
isolado que a propria CVM ja entrega nas linhas de ITR (ver
``cvm_itr.py``). Para operating_cash_flow/capex, que a CVM so entrega
acumulado, o isolamento e feito por subtracao
(``transforms.fundamentals_facts.derive_isolated_quarter_value``) contra o
acumulado do trimestre anterior do mesmo ano -- so quando esse acumulado
tambem foi ingerido; caso contrario ``quality_flag='missing_input'``.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Any

from stock_research.db import fetch_all, fetch_one, finish_run, start_run, upsert_many
from stock_research.logging import get_logger
from stock_research.transforms.fundamentals_facts import derive_isolated_quarter_value

logger = get_logger(__name__)

PIPELINE = "fundamentals_metrics"
CALCULATION_VERSION = "fundamental_metrics_v1"

REVENUE_DESC_NONFIN = ["Receita de Venda de Bens e/ou Serviços"]
REVENUE_DESC_FIN = ["Receitas da Intermediação Financeira", "Receitas de Intermediação Financeira"]
NET_INCOME_DESC = ["Lucro/Prejuízo Consolidado do Período", "Lucro/Prejuízo do Período"]
EBIT_DESC = ["Resultado Antes do Resultado Financeiro e dos Tributos"]
ASSETS_DESC = ["Ativo Total"]
EQUITY_DESC = ["Patrimônio Líquido Consolidado", "Patrimônio Líquido"]
CASH_DESC = ["Caixa e Equivalentes de Caixa"]
DEBT_DESC = ["Empréstimos e Financiamentos"]
DEBT_BRANCHES = ("2.01", "2.02")
OCF_DESC = ["Caixa Líquido Atividades Operacionais"]
CAPEX_DESC = [
    "Aquisições de ativos imobilizados e intangíveis",
    "Adições ao Imobilizado",
    "Aquisição de Imobilizado",
    "Aquisição de Ativo Imobilizado",
    "Pagamento por Aquisição de Ativos Imobilizados",
]
CAPEX_BRANCH = "6.02"

_SECTOR_INADEQUATE_FOR_BANKS = {"gross_debt", "net_debt", "capex", "free_cash_flow", "ebit"}


def _norm(text: str | None) -> str:
    if not text:
        return ""
    ascii_form = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"\s+", " ", ascii_form).strip().upper()


def _depth(account_code: str) -> int:
    return account_code.count(".")


def _scaled(fact: dict[str, Any] | None) -> Decimal | None:
    """``None``-safe de proposito: ``_match_one`` devolve ``None`` sempre que a
    conta comparativa (PENULTIMO) nao existe -- ex. primeiro ano de dados de
    uma empresa, ou conta ausente so no periodo anterior. Sem essa guarda,
    ``_scaled(_match_one(...))`` quebraria com ``AttributeError`` exatamente
    nesses casos, que fase1.md 104 diz que nao podem abortar o resto."""
    if fact is None or fact.get("value") is None:
        return None
    return Decimal(fact["value"]) * Decimal(fact.get("scale") or 1)


def _match_one(facts: list[dict[str, Any]], statement_type: str, descriptions: list[str]) -> dict[str, Any] | None:
    targets = {_norm(d) for d in descriptions}
    candidates = [f for f in facts if f["statement_type"] == statement_type and _norm(f["account_description"]) in targets]
    return min(candidates, key=lambda f: _depth(f["account_code"])) if candidates else None


def _sum_per_branch(
    facts: list[dict[str, Any]], statement_type: str, descriptions: list[str], branches: tuple[str, ...]
) -> tuple[Decimal | None, list[int]]:
    """Soma o match mais raso de cada ramo (``2.01``, ``2.02``, ...) -- evita
    contar duas vezes quando a mesma descricao se repete num nivel mais fundo
    (ex.: ``2.01.04`` e ``2.01.04.01`` ambos "Emprestimos e Financiamentos")."""
    targets = {_norm(d) for d in descriptions}
    total: Decimal | None = None
    doc_ids: list[int] = []
    for branch in branches:
        candidates = [
            f for f in facts
            if f["statement_type"] == statement_type
            and f["account_code"].startswith(branch)
            and _norm(f["account_description"]) in targets
        ]
        if not candidates:
            continue
        best = min(candidates, key=lambda f: _depth(f["account_code"]))
        total = (total or Decimal(0)) + (_scaled(best) or Decimal(0))
        if best.get("document_id"):
            doc_ids.append(best["document_id"])
    return total, doc_ids


def _sum_distinct_matches(
    facts: list[dict[str, Any]], statement_type: str, branch_prefix: str, descriptions: list[str]
) -> tuple[Decimal | None, list[int]]:
    """Para capex: varias linhas diferentes sob o mesmo ramo podem compor o
    total (ex.: aquisicao de imobilizado + de intangivel, quando reportadas
    separadamente). Agrupa por descricao e usa o match mais raso de cada uma,
    pela mesma razao de ``_sum_per_branch``."""
    targets = {_norm(d) for d in descriptions}
    matches = [
        f for f in facts
        if f["statement_type"] == statement_type
        and f["account_code"].startswith(branch_prefix)
        and _norm(f["account_description"]) in targets
    ]
    if not matches:
        return None, []
    by_desc: dict[str, dict[str, Any]] = {}
    for f in matches:
        key = _norm(f["account_description"])
        if key not in by_desc or _depth(f["account_code"]) < _depth(by_desc[key]["account_code"]):
            by_desc[key] = f
    total = sum((_scaled(f) or Decimal(0)) for f in by_desc.values())
    doc_ids = [f["document_id"] for f in by_desc.values() if f.get("document_id")]
    return total, doc_ids


def _flow_slice(facts: list[dict[str, Any]], *, primary: bool) -> list[dict[str, Any]]:
    """``primary``=True: periodo que comeca em 1/jan (anual no DFP, acumulado
    no ITR). ``primary``=False: trimestre isolado (so existe no ITR, quando a
    propria CVM ja entrega -- ver docstring de ``cvm_itr.py``). BPA/BPP
    (``period_start is None``) contam como primary (sao ponto-no-tempo, nao
    tem "isolado")."""
    out = []
    for f in facts:
        start = f["period_start"]
        is_jan_first = start is None or (start.month == 1 and start.day == 1)
        if (primary and is_jan_first) or (not primary and start is not None and not is_jan_first):
            out.append(f)
    return out


def _quarter_of(reference_date: date | None) -> int | None:
    if reference_date is None:
        return None
    return {3: 1, 6: 2, 9: 3}.get(reference_date.month)


def _docs(fact: dict[str, Any] | None) -> list[int]:
    return [fact["document_id"]] if fact and fact.get("document_id") else []


def _ok_or_missing(value: Any, reason: str) -> tuple[str, str | None]:
    return ("ok", None) if value is not None else ("missing_input", reason)


_RATIO_METRIC_NAMES = {"net_margin", "roe", "revenue_growth_yoy", "net_income_growth_yoy"}


# ---------------------------------------------------------------------------
# Balanco (point_in_time)
# ---------------------------------------------------------------------------


def _balance_metrics(
    current: list[dict[str, Any]], *, instrument_id: int, reference_date: date,
    available_from: Any, financial_company: bool, run_id: int | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def emit(name: str, value: Any, doc_ids: list[int], flag: str = "ok", reason: str | None = None) -> None:
        rows.append({
            "instrument_id": instrument_id, "reference_date": reference_date, "available_from": available_from,
            "period_type": "point_in_time", "metric_name": name, "metric_value": value, "unit": "BRL",
            "calculation_version": CALCULATION_VERSION, "source_document_ids": sorted(set(doc_ids)) or None,
            "quality_flag": flag, "quality_reason": reason, "run_id": run_id,
        })

    assets_f = _match_one(current, "BPA", ASSETS_DESC)
    equity_f = _match_one(current, "BPP", EQUITY_DESC)
    cash_f = _match_one(current, "BPA", CASH_DESC)
    debt_total, debt_doc_ids = _sum_per_branch(current, "BPP", DEBT_DESC, DEBT_BRANCHES)

    assets = _scaled(assets_f) if assets_f else None
    equity = _scaled(equity_f) if equity_f else None
    cash = _scaled(cash_f) if cash_f else None

    emit("assets", assets, _docs(assets_f), *_ok_or_missing(assets, "conta 'Ativo Total' nao encontrada no BPA"))
    emit("equity", equity, _docs(equity_f), *_ok_or_missing(equity, "conta 'Patrimonio Liquido' nao encontrada no BPP"))
    emit("cash", cash, _docs(cash_f), *_ok_or_missing(cash, "conta 'Caixa e Equivalentes de Caixa' nao encontrada no BPA"))

    if assets is not None and equity is not None:
        emit("liabilities", assets - equity, _docs(assets_f) + _docs(equity_f), "ok",
             "derivado: Ativo Total - Patrimonio Liquido (identidade contabil do balanco)")
    else:
        emit("liabilities", None, [], "missing_input", "requer assets e equity")

    if financial_company:
        emit("gross_debt", None, [], "sector_inadequate",
             "instituicao financeira: estrutura de funding nao equivale a 'Emprestimos e Financiamentos' (fase1.md 50)")
        emit("net_debt", None, [], "sector_inadequate", "instituicao financeira: net_debt nao se aplica (fase1.md 50)")
    else:
        emit("gross_debt", debt_total, debt_doc_ids,
             *_ok_or_missing(debt_total, "conta 'Emprestimos e Financiamentos' nao encontrada no BPP"))
        if debt_total is not None and cash is not None:
            emit("net_debt", debt_total - cash, sorted(set(debt_doc_ids) | set(_docs(cash_f))), "ok",
                 "derivado: gross_debt - cash")
        else:
            emit("net_debt", None, [], "missing_input", "requer gross_debt e cash")

    return rows


# ---------------------------------------------------------------------------
# DRE / DFC (fluxo): revenue, ebit, net_income, operating_cash_flow, capex,
# free_cash_flow, revenue_growth_yoy, net_income_growth_yoy
# ---------------------------------------------------------------------------


def _flow_metrics_direct(
    current_slice: list[dict[str, Any]], previous_slice: list[dict[str, Any]], *,
    instrument_id: int, reference_date: date, available_from: Any, period_type: str,
    financial_company: bool, run_id: int | None,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def emit(name: str, value: Any, doc_ids: list[int], flag: str = "ok", reason: str | None = None) -> None:
        rows.append({
            "instrument_id": instrument_id, "reference_date": reference_date, "available_from": available_from,
            "period_type": period_type, "metric_name": name, "metric_value": value,
            "unit": "ratio" if name in _RATIO_METRIC_NAMES else "BRL",
            "calculation_version": CALCULATION_VERSION, "source_document_ids": sorted(set(doc_ids)) or None,
            "quality_flag": flag, "quality_reason": reason, "run_id": run_id,
        })

    revenue_desc = REVENUE_DESC_FIN if financial_company else REVENUE_DESC_NONFIN
    revenue_f = _match_one(current_slice, "DRE", revenue_desc)
    net_income_f = _match_one(current_slice, "DRE", NET_INCOME_DESC)
    ebit_f = _match_one(current_slice, "DRE", EBIT_DESC)
    ocf_f = _match_one(current_slice, "DFC_MI", OCF_DESC) or _match_one(current_slice, "DFC_MD", OCF_DESC)
    capex_total, capex_doc_ids = _sum_distinct_matches(current_slice, "DFC_MI", CAPEX_BRANCH, CAPEX_DESC)
    if capex_total is None:
        capex_total, capex_doc_ids = _sum_distinct_matches(current_slice, "DFC_MD", CAPEX_BRANCH, CAPEX_DESC)

    revenue = _scaled(revenue_f) if revenue_f else None
    net_income = _scaled(net_income_f) if net_income_f else None
    ebit = _scaled(ebit_f) if ebit_f else None
    ocf = _scaled(ocf_f) if ocf_f else None

    emit("revenue", revenue, _docs(revenue_f), *_ok_or_missing(revenue, "conta de receita nao encontrada no DRE"))
    emit("net_income", net_income, _docs(net_income_f),
         *_ok_or_missing(net_income, "conta 'Lucro/Prejuizo do Periodo' nao encontrada no DRE"))

    if financial_company:
        emit("ebit", None, [], "sector_inadequate",
             "instituicao financeira: 'resultado antes do resultado financeiro' nao se aplica -- "
             "o resultado financeiro E o negocio principal (fase1.md 50)")
    else:
        emit("ebit", ebit, _docs(ebit_f), *_ok_or_missing(ebit, "conta EBIT nao encontrada no DRE"))

    emit("operating_cash_flow", ocf, _docs(ocf_f),
         *_ok_or_missing(ocf, "conta 'Caixa Liquido Atividades Operacionais' nao encontrada no DFC"))

    if financial_company:
        emit("capex", None, [], "sector_inadequate", "instituicao financeira: capex nao se aplica (fase1.md 50)")
        emit("free_cash_flow", None, [], "sector_inadequate",
             "instituicao financeira: free_cash_flow nao se aplica (fase1.md 50)")
    else:
        emit("capex", capex_total, capex_doc_ids, *_ok_or_missing(
            capex_total,
            "nenhuma linha de aquisicao de imobilizado/intangivel reconhecida no DFC "
            "(vocabulario curado -- CAPEX_DESC -- pode exigir expansao para esta empresa)",
        ))
        if ocf is not None and capex_total is not None:
            emit("free_cash_flow", ocf + capex_total, sorted(set(_docs(ocf_f)) | set(capex_doc_ids)), "ok",
                 "derivado: operating_cash_flow + capex (capex ja negativo, convencao de sinal da CVM)")
        else:
            emit("free_cash_flow", None, [], "missing_input", "requer operating_cash_flow e capex")

    # growth_yoy usa o comparativo PENULTIMO do mesmo pacote (mesmo DT_REFER,
    # ja entregue pela CVM) -- nunca busca outro documento (fase1.md 47).
    prev_revenue = _scaled(_match_one(previous_slice, "DRE", revenue_desc))
    prev_net_income = _scaled(_match_one(previous_slice, "DRE", NET_INCOME_DESC))
    _emit_growth(emit, "revenue_growth_yoy", revenue, prev_revenue, _docs(revenue_f))
    _emit_growth(emit, "net_income_growth_yoy", net_income, prev_net_income, _docs(net_income_f))

    return rows


def _emit_growth(emit: Any, name: str, current: Decimal | None, previous: Decimal | None, doc_ids: list[int]) -> None:
    if current is not None and previous is not None and previous != 0:
        emit(name, (current - previous) / abs(previous), doc_ids, "ok",
             "derivado: (atual - comparativo PENULTIMO do mesmo pacote) / abs(comparativo)")
    else:
        emit(name, None, [], "missing_input", "requer valor atual e comparativo (PENULTIMO) do mesmo pacote")


# ---------------------------------------------------------------------------
# Indicadores que cruzam DRE x BPP: net_margin, roe
# ---------------------------------------------------------------------------


def _ratio_metrics(rows: list[dict[str, Any]], *, instrument_id: int, run_id: int | None) -> list[dict[str, Any]]:
    equity_by_ref = {r["reference_date"]: r for r in rows if r["metric_name"] == "equity"}
    by_flow_period: dict[tuple[Any, str], dict[str, dict[str, Any]]] = {}
    for r in rows:
        if r["period_type"] == "point_in_time":
            continue
        by_flow_period.setdefault((r["reference_date"], r["period_type"]), {})[r["metric_name"]] = r

    out: list[dict[str, Any]] = []
    for (reference_date, period_type), metrics in by_flow_period.items():
        revenue, net_income = metrics.get("revenue"), metrics.get("net_income")
        equity = equity_by_ref.get(reference_date)
        anchor = revenue or net_income or equity
        if anchor is None or anchor["available_from"] is None:
            continue
        available_from = anchor["available_from"]

        def emit(name: str, value: Any, doc_ids: list[int], flag: str, reason: str | None,
                  _rd=reference_date, _pt=period_type, _af=available_from) -> None:
            out.append({
                "instrument_id": instrument_id, "reference_date": _rd, "available_from": _af,
                "period_type": _pt, "metric_name": name, "metric_value": value, "unit": "ratio",
                "calculation_version": CALCULATION_VERSION, "source_document_ids": sorted(set(doc_ids)) or None,
                "quality_flag": flag, "quality_reason": reason, "run_id": run_id,
            })

        if net_income and revenue and net_income["metric_value"] is not None and revenue["metric_value"]:
            value = Decimal(net_income["metric_value"]) / Decimal(revenue["metric_value"])
            docs = (net_income["source_document_ids"] or []) + (revenue["source_document_ids"] or [])
            emit("net_margin", value, docs, "ok", "derivado: net_income / revenue")
        else:
            emit("net_margin", None, [], "missing_input", "requer net_income e revenue")

        if net_income and equity and net_income["metric_value"] is not None and equity["metric_value"]:
            value = Decimal(net_income["metric_value"]) / Decimal(equity["metric_value"])
            docs = (net_income["source_document_ids"] or []) + (equity["source_document_ids"] or [])
            emit("roe", value, docs, "ok",
                 "derivado: net_income / patrimonio liquido de FIM de periodo (simplificacao -- nao e media "
                 "entre inicio e fim de periodo)")
        else:
            emit("roe", None, [], "missing_input", "requer net_income e equity")

    return out


# ---------------------------------------------------------------------------
# Trimestre isolado por subtracao (fase1.md 44) -- so para operating_cash_flow
# e capex, quando a CVM so entregou o acumulado (ver docstring de cvm_itr.py).
# ---------------------------------------------------------------------------


def _derive_isolated_flows(
    rows: list[dict[str, Any]], *, instrument_id: int, financial_company: bool, run_id: int | None
) -> list[dict[str, Any]]:
    ytd_by_metric: dict[str, dict[tuple[int, int], tuple[Any, list[int]]]] = {"operating_cash_flow": {}, "capex": {}}
    for r in rows:
        if r["period_type"] != "ytd" or r["metric_value"] is None or r["metric_name"] not in ytd_by_metric:
            continue
        quarter = _quarter_of(r["reference_date"])
        if quarter is not None:
            ytd_by_metric[r["metric_name"]][(r["reference_date"].year, quarter)] = (
                r["metric_value"], r["source_document_ids"] or [],
            )

    derived: list[dict[str, Any]] = []
    for r in rows:
        if r["period_type"] != "quarterly" or r["quality_flag"] != "missing_input":
            continue
        if r["metric_name"] not in ytd_by_metric:
            continue
        if r["metric_name"] == "capex" and financial_company:
            continue  # sector_inadequate ja cobre isso -- nao ha o que derivar
        quarter = _quarter_of(r["reference_date"])
        if quarter is None or quarter == 1:
            continue  # Q1 isolado == YTD, a propria CVM nao distingue (sem subtracao a fazer)

        year = r["reference_date"].year
        current = ytd_by_metric[r["metric_name"]].get((year, quarter))
        previous = ytd_by_metric[r["metric_name"]].get((year, quarter - 1))
        if current is None or previous is None:
            continue
        value = derive_isolated_quarter_value(
            statement_type="DFC_MI", current_cumulative=current[0], previous_cumulative=previous[0]
        )
        derived.append({
            **r,
            "metric_value": value,
            "quality_flag": "estimated",
            "quality_reason": (
                f"trimestre isolado derivado por subtracao: acumulado ate T{quarter} menos acumulado ate "
                f"T{quarter - 1} do mesmo ano (fase1.md 44)"
            ),
            "source_document_ids": sorted(set(current[1]) | set(previous[1])) or None,
            "run_id": run_id,
        })
    return derived


# ---------------------------------------------------------------------------
# Orquestracao por pacote (reference_date, version) e API publica
# ---------------------------------------------------------------------------


def _metrics_for_group(
    group: list[dict[str, Any]], *, instrument_id: int, financial_company: bool, run_id: int | None
) -> list[dict[str, Any]]:
    current = [f for f in group if f["fiscal_year_order"] == "ULTIMO"]
    previous = [f for f in group if f["fiscal_year_order"] == "PENULTIMO"]
    if not current:
        return []

    document_type = current[0]["document_type"]
    reference_date = current[0]["reference_date"]
    available_from = max((f["available_from"] for f in current if f["available_from"]), default=None)
    if available_from is None:
        return []  # fundamental_metrics.available_from e NOT NULL -- sem isso, nada e gravado (fase1.md 51)

    rows = _balance_metrics(
        current, instrument_id=instrument_id, reference_date=reference_date,
        available_from=available_from, financial_company=financial_company, run_id=run_id,
    )

    primary_current = _flow_slice(current, primary=True)
    primary_previous = _flow_slice(previous, primary=True)
    primary_period_type = "annual" if document_type == "DFP" else "ytd"
    rows += _flow_metrics_direct(
        primary_current, primary_previous, instrument_id=instrument_id, reference_date=reference_date,
        available_from=available_from, period_type=primary_period_type,
        financial_company=financial_company, run_id=run_id,
    )

    if document_type == "ITR":
        iso_current = _flow_slice(current, primary=False)
        iso_previous = _flow_slice(previous, primary=False)
        if iso_current:
            rows += _flow_metrics_direct(
                iso_current, iso_previous, instrument_id=instrument_id, reference_date=reference_date,
                available_from=available_from, period_type="quarterly",
                financial_company=financial_company, run_id=run_id,
            )
    return rows


def compute_metrics_for_facts(
    facts: list[dict[str, Any]], *, instrument_id: int, financial_company: bool, run_id: int | None = None
) -> list[dict[str, Any]]:
    """Funcao pura: fatos (``financial_statement_facts``, consolidados) de UM
    instrumento -> linhas de ``fundamental_metrics``.

    Limitacao documentada: esta funcao usa a versao MAIS RECENTE de cada
    ``(reference_date, statement_type, account_code)`` conhecida no momento em
    que roda -- nao preserva uma serie ponto-no-tempo por reapresentacao
    (diferente de ``financial_statement_facts``, que preserva todas as
    versoes). Para analise point-in-time real, use ``get_fundamentals_as_of``
    sobre os fatos brutos, nunca ``fundamental_metrics`` (mesma ressalva que
    ``v_fundamentals_latest_restated``).
    """
    groups: dict[tuple[Any, str], list[dict[str, Any]]] = {}
    for f in facts:
        groups.setdefault((f["reference_date"], f["version"]), []).append(f)

    by_key: dict[tuple[str, str, Any], dict[str, Any]] = {}
    for key in sorted(groups, key=lambda k: (k[0], k[1])):
        for row in _metrics_for_group(groups[key], instrument_id=instrument_id,
                                       financial_company=financial_company, run_id=run_id):
            by_key[(row["period_type"], row["metric_name"], row["reference_date"])] = row

    for derived in _derive_isolated_flows(list(by_key.values()), instrument_id=instrument_id,
                                           financial_company=financial_company, run_id=run_id):
        by_key[(derived["period_type"], derived["metric_name"], derived["reference_date"])] = derived

    for ratio_row in _ratio_metrics(list(by_key.values()), instrument_id=instrument_id, run_id=run_id):
        by_key[(ratio_row["period_type"], ratio_row["metric_name"], ratio_row["reference_date"])] = ratio_row

    return list(by_key.values())


def compute_and_store_metrics(ticker: str, *, run_id: int | None = None) -> dict[str, int]:
    """Recalcula e grava ``fundamental_metrics`` para um instrumento (upsert, idempotente)."""
    instrument = fetch_one(
        "select instrument_id, financial_company from public.instruments where ticker = %s", [ticker.upper()]
    )
    if instrument is None:
        raise ValueError(f"instrumento nao cadastrado: {ticker}")

    owns_run = run_id is None
    if owns_run:
        run_id = start_run(PIPELINE, ticker=ticker)
    assert run_id is not None
    try:
        facts = fetch_all(
            "select fact_id, document_id, document_type, statement_type, reference_date, period_start, "
            "period_end, version, account_code, account_description, value, scale, fiscal_year_order, "
            "available_from from public.financial_statement_facts "
            "where instrument_id = %s and is_consolidated = true",
            [instrument["instrument_id"]],
        )
        rows = compute_metrics_for_facts(
            facts, instrument_id=instrument["instrument_id"],
            financial_company=bool(instrument["financial_company"]), run_id=run_id,
        )
        stats = (
            upsert_many(
                "fundamental_metrics", rows,
                conflict_columns=["instrument_id", "reference_date", "period_type", "metric_name", "calculation_version"],
                update_columns=["available_from", "metric_value", "unit", "source_document_ids",
                                 "quality_flag", "quality_reason", "run_id"],
            )
            if rows else {"inserted": 0, "updated": 0, "total": 0}
        )
        if owns_run:
            finish_run(run_id, status="success", records_raw=len(facts), records_inserted=stats["total"])
        return {"facts": len(facts), **stats}
    except Exception as exc:
        if owns_run:
            finish_run(run_id, status="failed", error_message=str(exc))
        raise
