"""Pipeline de DCF FCFF (fase2_plan.md 10, 11, 12, 21).

Amarra: risk-free (Tesouro Prefixado) + ERP (snapshot Damodaran curado) + beta
(regressão vs IBOV) + custo de dívida (despesa financeira / dívida bruta) ->
WACC (§21.6) -> DCF FCFF 5 anos + Gordon terminal, 3 cenários -> margem de
segurança. Grava `risk_free_assumptions`, `equity_risk_premium_assumptions`,
`wacc_assumptions`, `valuation_snapshots`.

DCF V1 é NOMINAL em BRL. Só não-financeiras (PETR4, VALE3) -- bancos exigem
Residual Income / DDM, fora do escopo da V1 (§10).

FCFF ≈ **média dos últimos 3 exercícios** de ``NOPAT + D&A + capex`` (capex
negativo). Ignora variação de capital de giro -- refinamento futuro,
documentado. Média de 3 anos porque um único ano (capex pesado, write-off)
distorce demais o ponto de partida.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import yaml

from stock_research.analytics import beta as beta_mod
from stock_research.analytics.dcf import compute_dcf_scenarios
from stock_research.analytics.fundamentals_metrics import _sum_per_branch
from stock_research.analytics.wacc import compute_wacc
from stock_research.config import CONFIG_DIR
from stock_research.db import fetch_all, fetch_one, finish_run, start_run, upsert_many
from stock_research.logging import get_logger
from stock_research.sources.macro.tesouro import download_csv, iter_prefixado
from stock_research.transforms.risk_free import compute_risk_free

logger = get_logger(__name__)

PIPELINE = "valuation_dcf"
FCFF_AVG_YEARS = 3
_FIN_EXPENSE_DESC = ["Despesas Financeiras"]
_FIN_EXPENSE_BRANCH = ("3.06.02",)


def _wacc_config() -> dict[str, Any]:
    return yaml.safe_load((CONFIG_DIR / "wacc_v1.yaml").read_text(encoding="utf-8"))


def _erp_snapshot_as_of(as_of: date) -> dict[str, Any] | None:
    """Snapshot de ERP mais recente com ``available_from <= as_of`` (§21.4)."""
    data = yaml.safe_load(
        (CONFIG_DIR / "equity_risk_premium_snapshots.yaml").read_text(encoding="utf-8")
    )
    eligible = [s for s in data.get("snapshots", []) if _as_date(s["available_from"]) <= as_of]
    if not eligible:
        return None
    return max(eligible, key=lambda s: _as_date(s["available_from"]))


def _as_date(v: Any) -> date:
    if isinstance(v, date):
        return v
    return date.fromisoformat(str(v)[:10])


def _prices(ticker: str, since: str = "2021-01-01") -> dict[date, float]:
    rows = fetch_all(
        "select trade_date, adj_close from public.daily_prices dp "
        "join public.instruments i using (instrument_id) "
        "where i.ticker = %s and dp.source = 'yfinance' and dp.adj_close is not null "
        "and trade_date >= %s order by trade_date",
        [ticker, since],
    )
    return {r["trade_date"]: float(r["adj_close"]) for r in rows}


def _company_beta(ticker: str, cfg: dict[str, Any]) -> dict[str, Any]:
    bcfg = cfg["beta"]
    asset = beta_mod.simple_returns(beta_mod.weekly_last_price(_prices(ticker)))
    bench = beta_mod.simple_returns(beta_mod.weekly_last_price(_prices(bcfg["benchmark_ticker"])))
    xa, xb = beta_mod.align(asset, bench)
    return beta_mod.compute_beta(xa, xb, min_observations=int(bcfg["min_observations"]))


def _latest_annual(
    instrument_id: int, metric: str, calc_version: str, as_of: date
) -> list[dict[str, Any]]:
    return fetch_all(
        "select distinct on (reference_date) reference_date, metric_value, available_from "
        "from public.fundamental_metrics where instrument_id = %s and metric_name = %s "
        "and period_type = 'annual' and calculation_version = %s and available_from <= %s "
        "and metric_value is not null and quality_flag in ('ok','estimated') "
        "order by reference_date desc, available_from desc",
        [instrument_id, metric, calc_version, as_of],
    )


def _fcff_avg(instrument_id: int, as_of: date) -> tuple[float | None, list[int], str]:
    nopat = {
        r["reference_date"].year: float(r["metric_value"])
        for r in _latest_annual(instrument_id, "nopat", "valuation_metrics_v1", as_of)
    }
    da = {
        r["reference_date"].year: float(r["metric_value"])
        for r in _latest_annual(instrument_id, "da", "valuation_metrics_v1", as_of)
    }
    capex = {
        r["reference_date"].year: float(r["metric_value"])
        for r in _latest_annual(instrument_id, "capex", "fundamental_metrics_v1", as_of)
    }
    years = sorted(set(nopat) & set(da) & set(capex), reverse=True)[:FCFF_AVG_YEARS]
    if len(years) < 2:
        return None, years, f"apenas {len(years)} exercício(s) com NOPAT+D&A+capex"
    fcff_by_year = [nopat[y] + da[y] + capex[y] for y in years]
    return (
        sum(fcff_by_year) / len(fcff_by_year),
        years,
        f"média de {len(years)} anos ({min(years)}-{max(years)})",
    )


def _financial_expense_over_debt(
    instrument_id: int, as_of: date, gross_debt: float | None
) -> tuple[float | None, str]:
    cfg = _wacc_config()["cost_of_debt"]
    if not gross_debt or gross_debt <= 0:
        return None, "sem dívida bruta"
    facts = fetch_all(
        "select statement_type, account_code, account_description, value, scale, document_id "
        "from public.financial_statement_facts "
        "where instrument_id = %s and is_consolidated = true and fiscal_year_order = 'ULTIMO' "
        "and document_type = 'DFP' and statement_type = 'DRE' and account_code like '3.06.02%' "
        "and reference_date = (select max(reference_date) from public.financial_statement_facts "
        "  where instrument_id = %s and document_type = 'DFP') ",
        [instrument_id, instrument_id],
    )
    total, _docs = _sum_per_branch(facts, "DRE", _FIN_EXPENSE_DESC, _FIN_EXPENSE_BRANCH)
    if total is None:
        return None, "conta 'Despesas Financeiras' não encontrada"
    rate = abs(float(total)) / gross_debt
    rate = max(float(cfg["floor"]), min(float(cfg["cap"]), rate))
    return rate, f"despesa financeira / dívida bruta = {rate:.4f} (com piso/teto)"


def compute_and_store_dcf(
    company_ref: str | int, *, as_of: date | None = None, run_id: int | None = None
) -> dict[str, Any]:
    as_of = as_of or date.today()
    cfg = _wacc_config()
    company = _resolve_company(company_ref)
    company_id = company["company_id"]

    owns_run = run_id is None
    if owns_run:
        run_id = start_run(PIPELINE, params={"company_id": company_id, "as_of": as_of.isoformat()})
    assert run_id is not None
    try:
        result = _run_one(company, as_of, cfg, run_id)
        if owns_run:
            finish_run(run_id, status="success")
        return result
    except Exception as exc:
        if owns_run:
            finish_run(run_id, status="failed", error_message=str(exc))
        raise


def _resolve_company(company_ref: str | int) -> dict[str, Any]:
    if isinstance(company_ref, int):
        row = fetch_one(
            "select company_id, financial_company, commodity_exposed from public.companies where company_id = %s",
            [company_ref],
        )
    else:
        row = fetch_one(
            "select c.company_id, c.financial_company, c.commodity_exposed from public.companies c "
            "left join public.instruments i on i.company_id = c.company_id "
            "where c.cnpj = %s or upper(i.ticker) = %s limit 1",
            [company_ref, company_ref.upper()],
        )
    if row is None:
        raise ValueError(f"companhia não encontrada: {company_ref}")
    return row


def _primary_instrument(company_id: int) -> dict[str, Any]:
    row = fetch_one(
        "select instrument_id, ticker from public.instruments "
        "where company_id = %s and active = true order by instrument_id limit 1",
        [company_id],
    )
    if row is None:
        raise ValueError(f"companhia {company_id} sem instrumento ativo")
    return row


def _run_one(
    company: dict[str, Any], as_of: date, cfg: dict[str, Any], run_id: int
) -> dict[str, Any]:
    company_id = company["company_id"]
    inst = _primary_instrument(company_id)
    ticker = inst["ticker"]

    # --- risk-free + ERP (comuns; gravados uma vez por as_of) -----------------
    erp = _erp_snapshot_as_of(as_of)
    default_spread = float(erp["country_default_spread"]) if erp else None
    rf_dir = Path("data/raw/macro/tesouro")
    quotes: list[Any] = []
    try:
        path, _sha = download_csv(rf_dir)
        quotes = iter_prefixado(path)
    except Exception as exc:  # rede pode falhar -- registra e segue com missing
        logger.warning("valuation_dcf: download do Tesouro falhou: %s", exc)
    rf = compute_risk_free(
        quotes,
        as_of,
        country_default_spread=default_spread,
        target_maturity_years=float(cfg["risk_free"]["target_maturity_years"]),
    )
    _store_risk_free(rf, run_id)
    if erp:
        _store_erp(erp, run_id)

    if bool(company["financial_company"]):
        snap = _empty_snapshot(
            company_id,
            as_of,
            "sector_inadequate",
            "perfil banco -- FCFF DCF não se aplica; usar Residual Income/DDM (§10)",
        )
        _store_snapshots(
            company_id, as_of, {"base": snap}, wacc=None, run_id=run_id, erp=erp, rf=rf
        )
        return {"ticker": ticker, "as_of": as_of.isoformat(), "status": "sector_inadequate"}

    # --- insumos da empresa --------------------------------------------------
    bres = _company_beta(ticker, cfg)
    tax_rows = _latest_annual(
        inst["instrument_id"], "effective_tax_rate", "valuation_metrics_v1", as_of
    )
    tax = float(tax_rows[0]["metric_value"]) if tax_rows else None
    net_debt = _pit(inst["instrument_id"], "net_debt", as_of)
    gross_debt = _pit(inst["instrument_id"], "gross_debt", as_of)
    mcap_row = fetch_one(
        "select market_cap from public.valuation_multiples where company_id = %s and basis = 'fy' "
        "order by as_of_date desc limit 1",
        [company_id],
    )
    market_cap = (
        float(mcap_row["market_cap"]) if mcap_row and mcap_row["market_cap"] is not None else None
    )
    pretax_cod, cod_reason = _financial_expense_over_debt(inst["instrument_id"], as_of, gross_debt)
    fcff, fcff_years, fcff_reason = _fcff_avg(inst["instrument_id"], as_of)

    wacc = compute_wacc(
        risk_free_nominal_brl=rf.get("risk_free_rate"),
        beta=bres.get("beta"),
        mature_market_erp=float(erp["mature_market_erp"]) if erp else None,
        country_risk_premium=float(erp["country_risk_premium"]) if erp else None,
        pretax_cost_of_debt=pretax_cod,
        tax_rate=tax,
        market_cap=market_cap,
        gross_debt=gross_debt,
    )
    wacc["beta_observations"] = bres.get("observations")
    wacc["cost_of_debt_reason"] = cod_reason
    _store_wacc(company_id, as_of, wacc, run_id)

    shares_row = fetch_one(
        "select shares_issued from public.share_count_history sh "
        "where sh.company_id = %s and sh.share_class = 'TOTAL' and sh.shares_issued is not null "
        "and sh.available_from <= %s order by sh.available_from desc, sh.reference_date desc limit 1",
        [company_id, as_of],
    )
    shares = float(shares_row["shares_issued"]) if shares_row else None
    prices = _prices(ticker, since=(as_of.replace(year=as_of.year - 1)).isoformat())
    price = None
    if prices:
        last = max(d for d in prices if d <= as_of) if any(d <= as_of for d in prices) else None
        price = prices.get(last) if last else None

    dcfg = cfg["dcf"]
    scenarios = compute_dcf_scenarios(
        fcff_start=fcff,
        wacc=wacc.get("wacc"),
        terminal_growth=float(dcfg["terminal_growth_nominal"]),
        net_debt=net_debt,
        shares=shares,
        market_price_per_share=price,
        scenarios=dcfg["scenarios"],
        forecast_years=int(dcfg["forecast_years"]),
    )
    for res in scenarios.values():
        res.setdefault("fcff_years", fcff_years)
        res.setdefault("fcff_reason", fcff_reason)
    _store_snapshots(company_id, as_of, scenarios, wacc=wacc, run_id=run_id, erp=erp, rf=rf)

    base = scenarios.get("base", {})
    return {
        "ticker": ticker,
        "as_of": as_of.isoformat(),
        "wacc": wacc.get("wacc"),
        "fcff_start": fcff,
        "fair_value_base": base.get("fair_value_per_share"),
        "margin_of_safety_base": base.get("margin_of_safety"),
        "quality_flag": base.get("quality_flag"),
        "quality_reason": base.get("quality_reason"),
    }


def _pit(instrument_id: int, metric: str, as_of: date) -> float | None:
    row = fetch_one(
        "select metric_value from public.fundamental_metrics where instrument_id = %s "
        "and metric_name = %s and period_type = 'point_in_time' and available_from <= %s "
        "and metric_value is not null and quality_flag = 'ok' "
        "order by available_from desc, reference_date desc limit 1",
        [instrument_id, metric, as_of],
    )
    return float(row["metric_value"]) if row else None


# --- gravação --------------------------------------------------------------


def _store_risk_free(rf: dict[str, Any], run_id: int) -> None:
    upsert_many(
        "risk_free_assumptions",
        [
            {
                "as_of_date": rf["as_of_date"],
                "government_yield": rf.get("government_yield"),
                "default_spread": rf.get("default_spread"),
                "risk_free_rate": rf.get("risk_free_rate"),
                "bond_maturity": rf.get("bond_maturity"),
                "bond_base_date": rf.get("bond_base_date"),
                "bond_type": rf.get("bond_type"),
                "quality_flag": rf.get("quality_flag", "ok"),
                "quality_reason": rf.get("quality_reason"),
                "run_id": run_id,
            }
        ],
        conflict_columns=["as_of_date", "calculation_version"],
        update_columns=[
            "government_yield",
            "default_spread",
            "risk_free_rate",
            "bond_maturity",
            "bond_base_date",
            "bond_type",
            "quality_flag",
            "quality_reason",
            "run_id",
        ],
    )


def _store_erp(erp: dict[str, Any], run_id: int) -> None:
    upsert_many(
        "equity_risk_premium_assumptions",
        [
            {
                "snapshot_date": _as_date(erp["snapshot_date"]),
                "available_from": datetime.combine(
                    _as_date(erp["available_from"]), datetime.min.time(), tzinfo=UTC
                ),
                "country": erp.get("country", "Brazil"),
                "mature_market_erp": erp["mature_market_erp"],
                "country_default_spread": erp["country_default_spread"],
                "country_risk_premium": erp["country_risk_premium"],
                "total_equity_risk_premium": erp["total_equity_risk_premium"],
                "quality_flag": "ok",
                "run_id": run_id,
            }
        ],
        conflict_columns=["snapshot_date", "country", "calculation_version"],
        update_columns=[
            "available_from",
            "mature_market_erp",
            "country_default_spread",
            "country_risk_premium",
            "total_equity_risk_premium",
            "quality_flag",
            "run_id",
        ],
    )


def _store_wacc(company_id: int, as_of: date, wacc: dict[str, Any], run_id: int) -> None:
    upsert_many(
        "wacc_assumptions",
        [
            {
                "company_id": company_id,
                "as_of_date": as_of,
                "risk_free_nominal_brl": wacc.get("risk_free_nominal_brl"),
                "beta": wacc.get("beta"),
                "beta_observations": wacc.get("beta_observations"),
                "mature_market_erp": wacc.get("mature_market_erp"),
                "country_risk_premium": wacc.get("country_risk_premium"),
                "cost_of_equity": wacc.get("cost_of_equity"),
                "pretax_cost_of_debt": wacc.get("pretax_cost_of_debt"),
                "company_credit_spread": wacc.get("company_credit_spread"),
                "tax_rate": wacc.get("tax_rate"),
                "cost_of_debt": wacc.get("cost_of_debt"),
                "equity_weight": wacc.get("equity_weight"),
                "debt_weight": wacc.get("debt_weight"),
                "wacc": wacc.get("wacc"),
                "inputs": json.dumps({k: _j(v) for k, v in wacc.items()}, ensure_ascii=False),
                "quality_flag": wacc.get("quality_flag", "ok"),
                "quality_reason": wacc.get("quality_reason"),
                "run_id": run_id,
            }
        ],
        conflict_columns=["company_id", "as_of_date", "calculation_version"],
        update_columns=[
            "risk_free_nominal_brl",
            "beta",
            "beta_observations",
            "mature_market_erp",
            "country_risk_premium",
            "cost_of_equity",
            "pretax_cost_of_debt",
            "company_credit_spread",
            "tax_rate",
            "cost_of_debt",
            "equity_weight",
            "debt_weight",
            "wacc",
            "inputs",
            "quality_flag",
            "quality_reason",
            "run_id",
        ],
    )


def _empty_snapshot(company_id: int, as_of: date, flag: str, reason: str) -> dict[str, Any]:
    return {"quality_flag": flag, "quality_reason": reason, "fair_value_per_share": None}


def _store_snapshots(
    company_id: int,
    as_of: date,
    scenarios: dict[str, dict[str, Any]],
    *,
    wacc: dict[str, Any] | None,
    run_id: int,
    erp: dict[str, Any] | None,
    rf: dict[str, Any],
) -> None:
    rows = []
    for scenario, res in scenarios.items():
        rows.append(
            {
                "company_id": company_id,
                "as_of_date": as_of,
                "valuation_method": "fcff",
                "scenario": scenario,
                "fair_value_per_share": res.get("fair_value_per_share"),
                "market_price_per_share": res.get("market_price_per_share"),
                "margin_of_safety": res.get("margin_of_safety"),
                "enterprise_value": res.get("enterprise_value"),
                "equity_value": res.get("equity_value"),
                "terminal_value_share": res.get("terminal_value_share_of_ev"),
                "wacc": (wacc or {}).get("wacc"),
                "assumptions": json.dumps(
                    {
                        "dcf": {k: _j(v) for k, v in res.items()},
                        "wacc": {k: _j(v) for k, v in (wacc or {}).items()},
                        "risk_free": {k: _j(v) for k, v in rf.items()},
                        "erp": erp,
                    },
                    ensure_ascii=False,
                ),
                "quality_flag": res.get("quality_flag", "ok"),
                "quality_reason": res.get("quality_reason"),
                "run_id": run_id,
            }
        )
    upsert_many(
        "valuation_snapshots",
        rows,
        conflict_columns=[
            "company_id",
            "as_of_date",
            "valuation_method",
            "scenario",
            "calculation_version",
        ],
        update_columns=[
            "fair_value_per_share",
            "market_price_per_share",
            "margin_of_safety",
            "enterprise_value",
            "equity_value",
            "terminal_value_share",
            "wacc",
            "assumptions",
            "quality_flag",
            "quality_reason",
            "run_id",
        ],
    )


def _j(v: Any) -> Any:
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, float | int | str | bool) or v is None:
        return v
    return str(v)


def run_dcf(*, as_of: date | None = None) -> dict[str, Any]:
    """DCF para todas as companhias."""
    as_of = as_of or date.today()
    results: dict[str, Any] = {}
    for row in fetch_all("select company_id from public.companies order by company_id"):
        try:
            results[str(row["company_id"])] = compute_and_store_dcf(row["company_id"], as_of=as_of)
        except Exception as exc:  # uma companhia não aborta as outras
            results[str(row["company_id"])] = {"status": "failed", "error": str(exc)}
    failed = [k for k, v in results.items() if v.get("status") == "failed"]
    return {"as_of": as_of.isoformat(), "results": results, "failed": failed}
