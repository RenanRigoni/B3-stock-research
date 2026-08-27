"""Pipeline de DCF FCFF (fase2_plan.md 10, 11, 12, 21).

Amarra: risk-free (Tesouro Prefixado) + ERP (snapshot Damodaran curado) + beta
(regressão vs IBOV) + custo de dívida (despesa financeira / dívida bruta) ->
WACC (§21.6) -> DCF FCFF 5 anos + Gordon terminal, 3 cenários -> margem de
segurança. Grava `risk_free_assumptions`, `equity_risk_premium_assumptions`,
`wacc_assumptions`, `valuation_snapshots`.

DCF V1 é NOMINAL em BRL. Não-financeiras (PETR4, VALE3): FCFF DCF. Bancos
(ITUB4): Residual Income Model + Dividend Discount Model (`analytics.residual_income`,
`analytics.ddm`) -- FCFF não se aplica (§10). Ambos gravam em `valuation_snapshots`
com `valuation_method` distinto.

FCFF ~= media dos ultimos 3 exercicios de ``NOPAT + D&A + capex - delta_WC``
(capex negativo; delta_WC = variacao do capital de giro OPERACIONAL, fase2_plan
§35). Custo de divida usa juros puros (DRE 3.06.02.01), com fallback ao nivel 2
(que inclui cambio) marcado. Media de 3 anos porque um unico ano (capex pesado,
write-off) distorce demais o ponto de partida.
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
# `3.06.02.01` = juros puros. `3.06.02` (nível 2) inclui variação cambial e
# monetária -- só como fallback, com flag (fase2_plan §35).
_INTEREST_BRANCH = ("3.06.02.01",)
_FIN_EXPENSE_BRANCH = ("3.06.02",)


# Severidade crescente: o resultado herda o PIOR flag dos insumos (§36).
_FLAG_SEVERITY = {"ok": 0, "estimated": 1, "incomplete": 2, "missing_input": 3}


def _merge_quality(*parts: tuple[str, str | None]) -> tuple[str, str | None]:
    """Pior flag entre os insumos + concatenação dos motivos que degradaram."""
    worst = max(parts, key=lambda p: _FLAG_SEVERITY.get(p[0], 0))[0]
    reasons = [r for f, r in parts if f != "ok" and r]
    return worst, "; ".join(reasons) if reasons else None


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
        "select distinct on (reference_date) reference_date, metric_value, quality_flag, "
        "available_from from public.fundamental_metrics where instrument_id = %s "
        "and metric_name = %s and period_type = 'annual' and calculation_version = %s "
        "and available_from <= %s and metric_value is not null "
        "and quality_flag in ('ok','estimated') order by reference_date desc, available_from desc",
        [instrument_id, metric, calc_version, as_of],
    )


def _fcff_avg(instrument_id: int, as_of: date) -> tuple[float | None, list[int], str, str]:
    """FCFF ~= media de 3 anos de ``NOPAT + D&A + capex - delta_WC_operacional``.

    ``capex`` ja vem NEGATIVO (convencao da CVM) -- e somado. ``delta_WC = WC_y
    - WC_{y-1}``: aumento de capital de giro = uso de caixa -> subtrai.

    Qualidade (§36): delta_WC assumido 0 por ``working_capital`` ausente e uma
    SUPOSICAO, nao um dado observado -- degrada para ``estimated``. Idem para
    qualquer insumo do ano que ja venha ``estimated`` (ex.: capex de linha
    combinada da VALE3, §35). Retorna ``(valor, anos, motivo, quality_flag)``.
    """

    def by_year(metric: str, cv: str) -> dict[int, tuple[float, str]]:
        return {
            r["reference_date"].year: (float(r["metric_value"]), str(r["quality_flag"]))
            for r in _latest_annual(instrument_id, metric, cv, as_of)
        }

    nopat = by_year("nopat", "valuation_metrics_v1")
    da = by_year("da", "valuation_metrics_v1")
    capex = by_year("capex", "fundamental_metrics_v1")
    wc = by_year("working_capital", "valuation_metrics_v1")

    years = sorted(set(nopat) & set(da) & set(capex), reverse=True)[:FCFF_AVG_YEARS]
    if len(years) < 2:
        return (
            None,
            years,
            f"apenas {len(years)} exercício(s) com NOPAT+D&A+capex",
            "missing_input",
        )

    fcff_by_year: list[float] = []
    no_wc_years: list[int] = []
    estimated_inputs: set[str] = set()
    for y in years:
        base = nopat[y][0] + da[y][0] + capex[y][0]
        for name, src in (("nopat", nopat), ("da", da), ("capex", capex)):
            if src[y][1] == "estimated":
                estimated_inputs.add(f"{name} {y}")
        if y in wc and (y - 1) in wc:
            base -= wc[y][0] - wc[y - 1][0]  # delta WC
            if wc[y][1] == "estimated" or wc[y - 1][1] == "estimated":
                estimated_inputs.add(f"working_capital {y}")
        else:
            no_wc_years.append(y)
        fcff_by_year.append(base)

    reason = f"média de {len(years)} anos ({min(years)}-{max(years)}), FCFF = NOPAT+D&A+capex menos deltaWC"
    flag = "ok"
    if no_wc_years:
        flag = "estimated"
        reason += (
            f"; deltaWC ASSUMIDO 0 em {sorted(no_wc_years)} (working_capital ausente) "
            "-- suposição, não dado observado"
        )
    if estimated_inputs:
        flag = "estimated"
        reason += f"; insumos estimados: {', '.join(sorted(estimated_inputs))}"
    return sum(fcff_by_year) / len(fcff_by_year), years, reason, flag


def _financial_expense_over_debt(
    instrument_id: int, as_of: date, gross_debt: float | None, risk_free_rate: float | None
) -> tuple[float | None, str, str]:
    """Custo de dívida pré-imposto. Retorna ``(taxa, motivo, quality_flag)``.

    ``estimated`` quando a taxa usada não é a observada: fallback para a conta
    agregada `3.06.02` (inclui variação cambial) ou piso do risk-free (§36).
    """
    cfg = _wacc_config()["cost_of_debt"]
    if not gross_debt or gross_debt <= 0:
        return None, "sem dívida bruta", "missing_input"
    facts = fetch_all(
        "select statement_type, account_code, account_description, value, scale, document_id "
        "from public.financial_statement_facts "
        "where instrument_id = %s and is_consolidated = true and fiscal_year_order = 'ULTIMO' "
        "and document_type = 'DFP' and statement_type = 'DRE' and account_code like '3.06.02%' "
        "and reference_date = (select max(reference_date) from public.financial_statement_facts "
        "  where instrument_id = %s and document_type = 'DFP') ",
        [instrument_id, instrument_id],
    )
    interest, _di = _sum_per_branch(facts, "DRE", _FIN_EXPENSE_DESC, _INTEREST_BRANCH)
    combined, _dc = _sum_per_branch(facts, "DRE", _FIN_EXPENSE_DESC, _FIN_EXPENSE_BRANCH)
    # Zero em `3.06.02.01` NÃO é observação: a VALE3 declara a linha vazia e só
    # preenche o nível 2 (§36). Empresa com dívida bruta positiva não paga juro
    # zero -- trata como ausente e cai no fallback, nunca assume 0.
    zeroed = interest is not None and float(interest) == 0.0
    flag = "ok"
    if interest is not None and not zeroed:
        total, src = interest, "juros puros (DRE 3.06.02.01)"
    elif combined is not None and float(combined) != 0.0:
        detalhe = " (3.06.02.01 declarada com valor zero)" if zeroed else ""
        total = combined
        src = f"despesa financeira nível 2 (inclui não-juros -- fallback){detalhe}"
        flag = "estimated"  # não é juros puros: proxy, não observação limpa
    else:
        return (
            None,
            "despesa financeira não observada (3.06.02.01 e 3.06.02 ausentes ou zeradas)",
            "missing_input",
        )

    raw = abs(float(total)) / gross_debt
    # Piso econômico: uma empresa não capta abaixo do soberano. O piso do config
    # (4%) só vale quando não há risk-free; havendo, o piso é o risk-free.
    floor = risk_free_rate if risk_free_rate is not None else float(cfg["floor"])
    rate = max(floor, min(float(cfg["cap"]), raw))
    base = f"{src} / dívida bruta = {raw:.4f}"
    if rate != raw:
        # a taxa gravada deixou de ser a medida -- passa a ser premissa
        piso = "risk-free" if risk_free_rate is not None else "config"
        return (
            rate,
            f"{base}, SUBSTITUÍDO por {rate:.4f} (piso = {piso}) -- premissa, não observação",
            "estimated",
        )
    return rate, base, flag


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
        return _run_bank(company, inst, as_of, cfg, run_id, erp=erp, rf=rf)

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
    pretax_cod, cod_reason, cod_flag = _financial_expense_over_debt(
        inst["instrument_id"], as_of, gross_debt, rf.get("risk_free_rate")
    )
    fcff, fcff_years, fcff_reason, fcff_flag = _fcff_avg(inst["instrument_id"], as_of)

    wacc = compute_wacc(
        risk_free_nominal_brl=rf.get("risk_free_rate"),
        beta=bres.get("beta"),
        mature_market_erp=float(erp["mature_market_erp"]) if erp else None,
        country_risk_premium=float(erp["country_risk_premium"]) if erp else None,
        pretax_cost_of_debt=pretax_cod,
        tax_rate=tax,
        market_cap=market_cap,
        gross_debt=gross_debt,
        cost_of_debt_quality_flag=cod_flag if cod_flag != "missing_input" else "ok",
        cost_of_debt_quality_reason=cod_reason if cod_flag == "estimated" else None,
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

    # Qualidade do DCF = pior entre FCFF e WACC (§36): premissa em qualquer
    # perna contamina o fair value inteiro.
    dcf_flag, dcf_reason = _merge_quality(
        (fcff_flag, f"FCFF: {fcff_reason}"),
        (str(wacc.get("quality_flag", "ok")), f"WACC: {wacc.get('quality_reason')}"),
    )

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
        input_quality_flag=dcf_flag,
        input_quality_reason=dcf_reason,
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


def _run_bank(
    company: dict[str, Any],
    inst: dict[str, Any],
    as_of: date,
    cfg: dict[str, Any],
    run_id: int,
    *,
    erp: dict[str, Any] | None,
    rf: dict[str, Any],
) -> dict[str, Any]:
    """Bancos: Residual Income + DDM (§10). FCFF não se aplica."""
    from stock_research.analytics.ddm import compute_ddm
    from stock_research.analytics.residual_income import (
        compute_residual_income,
        cost_of_equity,
    )

    company_id = company["company_id"]
    ticker = inst["ticker"]
    pid = inst["instrument_id"]

    bres = _company_beta(ticker, cfg)
    coe = cost_of_equity(
        risk_free_nominal_brl=rf.get("risk_free_rate"),
        beta=bres.get("beta"),
        mature_market_erp=float(erp["mature_market_erp"]) if erp else None,
        country_risk_premium=float(erp["country_risk_premium"]) if erp else None,
    )

    equity = _pit(pid, "equity", as_of)
    ni_rows = _latest_annual(pid, "net_income", "fundamental_metrics_v1", as_of)
    net_income = float(ni_rows[0]["metric_value"]) if ni_rows else None

    mult = fetch_one(
        "select dividends_ttm, market_cap from public.valuation_multiples "
        "where company_id = %s and basis = 'fy' order by as_of_date desc limit 1",
        [company_id],
    )
    dividends_ttm_total = (
        float(mult["dividends_ttm"]) if mult and mult["dividends_ttm"] is not None else None
    )
    shares_row = fetch_one(
        "select shares_issued from public.share_count_history "
        "where company_id = %s and share_class = 'TOTAL' and shares_issued is not null "
        "and available_from <= %s order by available_from desc, reference_date desc limit 1",
        [company_id, as_of],
    )
    shares = float(shares_row["shares_issued"]) if shares_row else None
    div_ps = (
        dividends_ttm_total / shares
        if dividends_ttm_total is not None and shares and shares > 0
        else None
    )
    payout_observed = dividends_ttm_total is not None and net_income is not None and net_income > 0
    if payout_observed:
        assert dividends_ttm_total is not None and net_income is not None
        payout = max(0.0, min(1.0, dividends_ttm_total / net_income))
        ri_flag, ri_reason = "ok", None
    else:
        # payout default é PREMISSA, não observação -- degrada o RIM (§36)
        payout = 0.5
        ri_flag = "estimated"
        ri_reason = (
            "payout_ratio ASSUMIDO 0,5 (dividends_ttm ou lucro positivo ausentes) "
            "-- suposição, não dado observado"
        )

    prices = _prices(ticker, since=(as_of.replace(year=as_of.year - 1)).isoformat())
    price = None
    if prices and any(d <= as_of for d in prices):
        price = prices[max(d for d in prices if d <= as_of)]

    dcfg = cfg["dcf"]
    tg = float(dcfg["terminal_growth_nominal"])
    ri_scen: dict[str, dict[str, Any]] = {}
    ddm_scen: dict[str, dict[str, Any]] = {}
    for name, scfg in dcfg["scenarios"].items():
        g = float(scfg["forecast_growth"])
        ri_scen[name] = compute_residual_income(
            equity_start=equity,
            net_income_start=net_income,
            coe=coe,
            net_income_growth=g,
            terminal_growth=tg,
            payout_ratio=payout,
            shares=shares,
            market_price_per_share=price,
            forecast_years=int(dcfg["forecast_years"]),
            input_quality_flag=ri_flag,
            input_quality_reason=ri_reason,
        )
        ddm_scen[name] = compute_ddm(
            dividend_ttm_per_share=div_ps,
            coe=coe,
            dividend_growth=g,
            terminal_growth=tg,
            market_price_per_share=price,
            forecast_years=int(dcfg["forecast_years"]),
        )

    _store_snapshots(
        company_id,
        as_of,
        ri_scen,
        wacc={"wacc": coe},
        run_id=run_id,
        erp=erp,
        rf=rf,
        method="residual_income",
    )
    _store_snapshots(
        company_id,
        as_of,
        ddm_scen,
        wacc={"wacc": coe},
        run_id=run_id,
        erp=erp,
        rf=rf,
        method="ddm",
    )
    base = ri_scen.get("base", {})
    return {
        "ticker": ticker,
        "as_of": as_of.isoformat(),
        "status": "bank",
        "cost_of_equity": coe,
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


def _store_snapshots(
    company_id: int,
    as_of: date,
    scenarios: dict[str, dict[str, Any]],
    *,
    wacc: dict[str, Any] | None,
    run_id: int,
    erp: dict[str, Any] | None,
    rf: dict[str, Any],
    method: str = "fcff",
) -> None:
    rows = []
    for scenario, res in scenarios.items():
        rows.append(
            {
                "company_id": company_id,
                "as_of_date": as_of,
                "valuation_method": method,
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
                        "erp": _j(erp),
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
    if isinstance(v, dict):
        return {str(k): _j(x) for k, x in v.items()}
    if isinstance(v, list | tuple):
        return [_j(x) for x in v]
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
