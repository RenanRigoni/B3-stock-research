"""Market cap por companhia e múltiplos point-in-time (fase2_plan.md 4, 5).

``market_cap(companhia, as_of) = soma_classe [ preco(ticker_classe, as_of) *
shares_issued(companhia, classe, as_of) ]`` -- precos com ``trade_date <=
as_of`` e quantidade de acoes com ``available_from <= as_of``, sem excecao.

V1: base **FY** (último exercício anual disponível point-in-time). ``basis='ttm'``
fica para o incremento seguinte (§5 pede TTM como padrão, mas exige EBITDA
trimestral isolado).

Nenhuma função aqui inventa número: preço, quantidade de ações ou fundamento
ausente vira ``quality_flag`` degradado e o múltiplo dependente fica ``None``
(fase2_plan.md 6, 11). Todos os insumos são preservados em ``price_inputs`` e
nas colunas ``*_ref`` para o cálculo ser reproduzível.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from stock_research.db import fetch_all, fetch_one, finish_run, start_run, upsert_many
from stock_research.logging import get_logger

logger = get_logger(__name__)

PIPELINE = "valuation_multiples"
CALCULATION_VERSION = "valuation_multiples_v1"
PRICE_SOURCE = "yfinance"
DIVIDEND_TYPES = ("dividend", "jcp")
# Acima disso, as classes foram precificadas em pregões distantes demais para o
# market cap somado ser tratado como 'ok'.
MAX_PRICE_DATE_SPREAD_DAYS = 3


def _price_as_of(instrument_id: int, as_of: date) -> dict[str, Any] | None:
    return fetch_one(
        "select close, trade_date from public.daily_prices "
        "where instrument_id = %s and source = %s and trade_date <= %s and close is not null "
        "order by trade_date desc limit 1",
        [instrument_id, PRICE_SOURCE, as_of],
    )


def _shares_issued_as_of(company_id: int, share_class: str, as_of: date) -> Decimal | None:
    row = fetch_one(
        "select shares_issued from public.share_count_history "
        "where company_id = %s and share_class = %s and available_from <= %s "
        "and shares_issued is not null "
        "order by available_from desc, reference_date desc limit 1",
        [company_id, share_class, as_of],
    )
    return Decimal(row["shares_issued"]) if row else None


def _metric_as_of(
    instrument_id: int, metric_name: str, period_type: str, calc_version: str, as_of: date
) -> dict[str, Any] | None:
    return fetch_one(
        "select metric_value, reference_date, available_from from public.fundamental_metrics "
        "where instrument_id = %s and metric_name = %s and period_type = %s "
        "and calculation_version = %s and available_from <= %s and metric_value is not null "
        "order by available_from desc, reference_date desc limit 1",
        [instrument_id, metric_name, period_type, calc_version, as_of],
    )


def _dividends_ttm_per_share(instrument_id: int, as_of: date) -> Decimal:
    # `in (%s, %s)` explicito, nao `= any(%s)`: o backend REST (exec_sql RPC)
    # nao materializa lista Python como array Postgres (mesma ressalva de
    # pipelines/fundamentals_ingest.py).
    placeholders = ", ".join(["%s"] * len(DIVIDEND_TYPES))
    row = fetch_one(
        "select coalesce(sum(value), 0) as total from public.corporate_actions "
        f"where instrument_id = %s and action_type in ({placeholders}) "
        "and action_date > %s and action_date <= %s",
        [instrument_id, *DIVIDEND_TYPES, as_of - timedelta(days=365), as_of],
    )
    return Decimal(row["total"]) if row and row["total"] is not None else Decimal(0)


def _company_instruments(company_id: int) -> list[dict[str, Any]]:
    """Instrumentos da companhia que têm classe definida (ON/PN/...), com ou sem
    ``active`` -- market cap soma todas as classes negociadas."""
    return fetch_all(
        "select instrument_id, ticker, share_class from public.instruments "
        "where company_id = %s and share_class is not null and share_class in ('ON', 'PN', 'PNA', 'PNB') "
        "order by ticker",
        [company_id],
    )


def _div(numer: Decimal | None, denom: Decimal | None) -> Decimal | None:
    if numer is None or denom is None or denom == 0:
        return None
    return numer / denom


def compute_multiples(company_id: int, as_of: date, *, basis: str = "fy") -> dict[str, Any]:
    """Calcula a linha de ``valuation_multiples`` para uma companhia.

    ``basis='fy'``: denominadores = último exercício anual disponível.
    ``basis='ttm'``: net_income/ebitda/fcf = soma dos últimos 4 trimestres
    (``period_type='ttm'``, ``valuation_metrics_v1``); equity/net_debt continuam
    a última posição de balanço.
    """
    if basis not in ("fy", "ttm"):
        raise ValueError(f"basis inválido: {basis}")
    company = fetch_one(
        "select company_id, cnpj, financial_company from public.companies where company_id = %s",
        [company_id],
    )
    if company is None:
        raise ValueError(f"company_id não encontrado: {company_id}")
    is_financial = bool(company["financial_company"])

    instruments = _company_instruments(company_id)
    warnings: list[str] = []
    price_inputs: dict[str, Any] = {}
    market_cap = Decimal(0)
    dividends_ttm = Decimal(0)
    legs_ok = 0
    trade_dates: list[date] = []

    for inst in instruments:
        price = _price_as_of(inst["instrument_id"], as_of)
        shares = _shares_issued_as_of(company_id, inst["share_class"], as_of)
        entry: dict[str, Any] = {"share_class": inst["share_class"]}
        if price is not None:
            entry["price"] = float(price["close"])
            entry["trade_date"] = price["trade_date"].isoformat()
        if shares is not None:
            entry["shares_issued"] = int(shares)
        price_inputs[inst["ticker"]] = entry

        if price is None or shares is None:
            warnings.append(
                f"{inst['ticker']}: "
                + ("sem preço" if price is None else "sem shares_issued")
                + f" com data <= {as_of} -- classe fora do market cap"
            )
            continue
        market_cap += Decimal(str(price["close"])) * shares
        dividends_ttm += _dividends_ttm_per_share(inst["instrument_id"], as_of) * shares
        trade_dates.append(price["trade_date"])
        legs_ok += 1

    # Múltiplas classes precificadas em pregões diferentes -> o market cap mistura
    # preços de datas distintas. Não é erro, mas degrada a qualidade.
    price_date_spread = (max(trade_dates) - min(trade_dates)).days if len(trade_dates) > 1 else 0
    if price_date_spread > MAX_PRICE_DATE_SPREAD_DAYS:
        warnings.append(
            f"preços das classes em pregões distintos (spread de {price_date_spread} dias)"
        )

    if legs_ok == 0:
        market_cap_val: Decimal | None = None
        warnings.append("nenhuma classe com preço e shares_issued -- market cap indeterminado")
    else:
        market_cap_val = market_cap

    # Fundamentos FY point-in-time. Usa o instrumento primário (ativo) da companhia
    # -- fundamental_metrics é chaveado por instrument_id (Fase 1), mas os valores
    # são da companhia (fase2_plan.md 13.4).
    primary = fetch_one(
        "select instrument_id from public.instruments "
        "where company_id = %s and active = true order by instrument_id limit 1",
        [company_id],
    )
    ni = ebitda = fcf = equity = net_debt = None
    fundamentals_ref: date | None = None
    if primary is not None:
        pid = primary["instrument_id"]
        if basis == "ttm":
            # net_income/ebitda/fcf TTM ficam todos sob valuation_metrics_v1.
            ni_r = _metric_as_of(pid, "net_income", "ttm", "valuation_metrics_v1", as_of)
            ebitda_r = _metric_as_of(pid, "ebitda", "ttm", "valuation_metrics_v1", as_of)
            fcf_r = _metric_as_of(pid, "free_cash_flow", "ttm", "valuation_metrics_v1", as_of)
        else:
            ni_r = _metric_as_of(pid, "net_income", "annual", "fundamental_metrics_v1", as_of)
            ebitda_r = _metric_as_of(pid, "ebitda", "annual", "valuation_metrics_v1", as_of)
            fcf_r = _metric_as_of(pid, "free_cash_flow", "annual", "fundamental_metrics_v1", as_of)
        equity_r = _metric_as_of(pid, "equity", "point_in_time", "fundamental_metrics_v1", as_of)
        nd_r = _metric_as_of(pid, "net_debt", "point_in_time", "fundamental_metrics_v1", as_of)
        ni = Decimal(ni_r["metric_value"]) if ni_r else None
        ebitda = Decimal(ebitda_r["metric_value"]) if ebitda_r else None
        fcf = Decimal(fcf_r["metric_value"]) if fcf_r else None
        equity = Decimal(equity_r["metric_value"]) if equity_r else None
        net_debt = Decimal(nd_r["metric_value"]) if nd_r else None
        fundamentals_ref = ni_r["reference_date"] if ni_r else None

    enterprise_value = (
        market_cap_val + net_debt if market_cap_val is not None and net_debt is not None else None
    )

    # P/L só faz sentido com lucro positivo; idem EV/EBITDA.
    pe = _div(market_cap_val, ni) if (ni is not None and ni > 0) else None
    ev_ebitda = _div(enterprise_value, ebitda) if (ebitda is not None and ebitda > 0) else None

    # Prioridade: missing_input > incomplete > sector_inadequate > estimated > ok.
    if market_cap_val is None:
        flag = "missing_input"
    elif legs_ok < len(instruments) or fundamentals_ref is None:
        flag = "incomplete"
    elif is_financial and ev_ebitda is None and fcf is None:
        flag = "sector_inadequate"
    elif price_date_spread > MAX_PRICE_DATE_SPREAD_DAYS:
        flag = "estimated"
    else:
        flag = "ok"

    return {
        "company_id": company_id,
        "as_of_date": as_of,
        "basis": basis,
        "market_cap": market_cap_val,
        "enterprise_value": enterprise_value,
        "price_earnings": pe,
        "ev_ebitda": ev_ebitda,
        "fcf_yield": _div(fcf, market_cap_val) if not is_financial else None,
        "earnings_yield": _div(ni, market_cap_val),
        "price_book": _div(market_cap_val, equity),
        "dividend_yield": _div(dividends_ttm, market_cap_val) if legs_ok else None,
        "net_income_ref": ni,
        "ebitda_ref": ebitda,
        "fcf_ref": fcf,
        "equity_ref": equity,
        "net_debt_ref": net_debt,
        "dividends_ttm": dividends_ttm if legs_ok else None,
        "fundamentals_ref_date": fundamentals_ref,
        "price_inputs": json.dumps(price_inputs, ensure_ascii=False),
        "calculation_version": CALCULATION_VERSION,
        "quality_flag": flag,
        "quality_reason": "; ".join(warnings) or None,
        "run_id": None,
    }


def compute_and_store_multiples(
    company_ref: str | int,
    *,
    as_of: date | None = None,
    basis: str = "fy",
    run_id: int | None = None,
) -> dict[str, Any]:
    """``company_ref``: company_id (int) ou ticker/CNPJ (str). ``as_of`` default = hoje.
    ``basis``: ``'fy'`` (default) ou ``'ttm'``."""
    as_of = as_of or date.today()
    if isinstance(company_ref, int):
        company_id = company_ref
    else:
        row = fetch_one(
            "select c.company_id from public.companies c "
            "left join public.instruments i on i.company_id = c.company_id "
            "where c.cnpj = %s or upper(i.ticker) = %s limit 1",
            [company_ref, company_ref.upper()],
        )
        if row is None:
            raise ValueError(f"companhia não encontrada: {company_ref}")
        company_id = row["company_id"]

    owns_run = run_id is None
    if owns_run:
        run_id = start_run(
            PIPELINE,
            params={"company_id": company_id, "as_of": as_of.isoformat(), "basis": basis},
        )
    assert run_id is not None
    try:
        row = compute_multiples(company_id, as_of, basis=basis)
        row["run_id"] = run_id
        stats = upsert_many(
            "valuation_multiples",
            [row],
            conflict_columns=["company_id", "as_of_date", "basis", "calculation_version"],
            update_columns=[
                "market_cap",
                "enterprise_value",
                "price_earnings",
                "ev_ebitda",
                "fcf_yield",
                "earnings_yield",
                "price_book",
                "dividend_yield",
                "net_income_ref",
                "ebitda_ref",
                "fcf_ref",
                "equity_ref",
                "net_debt_ref",
                "dividends_ttm",
                "fundamentals_ref_date",
                "price_inputs",
                "quality_flag",
                "quality_reason",
                "run_id",
            ],
        )
        if owns_run:
            finish_run(run_id, status="success", records_inserted=stats["total"])
        return {"company_id": company_id, "as_of": as_of.isoformat(), **row, **stats}
    except Exception as exc:
        if owns_run:
            finish_run(run_id, status="failed", error_message=str(exc))
        raise
