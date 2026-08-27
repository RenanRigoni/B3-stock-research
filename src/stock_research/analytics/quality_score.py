"""Quality Score não-financeiro -- ``quality_nonfinancial_v1`` (fase2_plan.md 8, 17).

Score 0-100 da qualidade da empresa, **independente de preço/valuation**: nenhum
componente usa preço de mercado, P/L, EV/EBITDA ou margem de segurança.

As bandas (5 marcadores por subitem, pesos, janela, winsorização) vivem em
``config/quality_nonfinancial_v1.yaml`` -- nunca aqui. Este módulo só aplica a
metodologia. Todo resultado carrega ``calibration_status='provisional'``
(bandas calibradas por referência de mercado, não por validação estatística --
universo de 3 empresas).

Bancos: ``score_status='incomplete'`` por desenho -- NIM/eficiência/Basileia/
inadimplência não têm fonte ainda (fase2_plan.md 9, 18).

Janela: últimos ``max_years`` exercícios fiscais anuais (DFP) com
``available_from <= as_of``; mínimo ``min_years`` por subitem/bloco.
"""

from __future__ import annotations

import statistics
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

from stock_research.config import CONFIG_DIR
from stock_research.db import fetch_all, fetch_one, finish_run, start_run, upsert_many
from stock_research.logging import get_logger

logger = get_logger(__name__)

PIPELINE = "quality_score"
METHODOLOGY_VERSION = "quality_nonfinancial_v1"
CALIBRATION_STATUS = "provisional"

# métricas lidas direto de fundamental_metrics (calculation_version, period_type)
_ANNUAL_V1 = ("net_margin", "roe", "free_cash_flow", "revenue", "net_income")
_POINT_IN_TIME_V1 = ("net_debt", "equity")


def _config_path() -> Path:
    return CONFIG_DIR / f"{METHODOLOGY_VERSION}.yaml"


def load_config() -> dict[str, Any]:
    return yaml.safe_load(_config_path().read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Primitivos de pontuação (puros)
# ---------------------------------------------------------------------------


def winsorize(value: float, bounds: list[float] | None) -> float:
    if not bounds:
        return value
    lo, hi = bounds
    return max(lo, min(hi, value))


def score_against_markers(value: float, markers: list[float], *, descending: bool = False) -> float:
    """Interpola linearmente ``value`` para 0-100 entre os 5 marcadores
    (0/25/50/75/100 pts). ``descending``: marcadores em ordem decrescente
    (valor menor = mais pontos, ex.: alavancagem, coeficiente de variação)."""
    pts = [0.0, 25.0, 50.0, 75.0, 100.0]
    if descending:
        # espelha para reaproveitar a lógica ascendente
        markers = [-m for m in markers]
        value = -value
    if value <= markers[0]:
        return 0.0
    if value >= markers[-1]:
        return 100.0
    for i in range(len(markers) - 1):
        lo, hi = markers[i], markers[i + 1]
        if lo <= value <= hi:
            frac = 0.0 if hi == lo else (value - lo) / (hi - lo)
            return pts[i] + frac * (pts[i + 1] - pts[i])
    return 0.0


def cagr(first: float, last: float, years: int) -> float | None:
    if years <= 0 or first <= 0 or last <= 0:
        return None
    return (last / first) ** (1.0 / years) - 1.0


def coefficient_of_variation(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = statistics.fmean(values)
    if mean == 0:
        return None
    return statistics.pstdev(values) / abs(mean)


def classify_trend(first: float, last: float, stable_band: float) -> str:
    """``net_debt/equity`` menor = melhor, então queda = 'melhorando'."""
    if first == 0:
        return "estavel" if abs(last) <= stable_band else ("piorando" if last > 0 else "melhorando")
    change = (last - first) / abs(first)
    if abs(change) <= stable_band:
        return "estavel"
    return "piorando" if change > 0 else "melhorando"


# ---------------------------------------------------------------------------
# Pontuação de um subitem sobre a série anual
# ---------------------------------------------------------------------------


def _subitem_values(
    series: dict[str, dict[int, float]], metric: str, years: list[int]
) -> list[float]:
    per_year = series.get(metric, {})
    return [per_year[y] for y in years if y in per_year and per_year[y] is not None]


def score_subitem(
    sub: dict[str, Any], series: dict[str, dict[int, float]], years: list[int], *, commodity: bool
) -> dict[str, Any]:
    agg = sub["aggregation"]
    metric = sub["metric"]
    vals = _subitem_values(series, metric, years)
    out: dict[str, Any] = {"key": sub["key"], "weight": sub["weight"], "aggregation": agg}

    def done(points: float | None, status: str, **extra: Any) -> dict[str, Any]:
        out.update({"points": points, "status": status, **extra})
        return out

    min_years = int(sub.get("min_years", 3))

    if agg in ("median", "coefficient_of_variation"):
        wb = sub.get("winsorize") or sub.get("winsorize_before_cv")
        wv = [winsorize(v, wb) for v in vals]
        if agg == "median":
            if len(wv) < min_years:
                return done(None, "insufficient_history", n=len(wv))
            value = float(statistics.median(wv))
            if sub.get("negative_anchors_floor") and value <= 0:
                return done(0.0, "ok", value=value)
            return done(
                score_against_markers(
                    value, sub["markers"], descending=bool(sub.get("descending"))
                ),
                "ok",
                value=value,
            )
        cv = coefficient_of_variation(wv)
        if cv is None or len(wv) < min_years:
            return done(None, "insufficient_history", n=len(wv))
        markers = sub["markers_commodity"] if commodity else sub["markers_standard"]
        return done(score_against_markers(cv, markers, descending=True), "ok", value=cv)

    if agg == "last":
        wb = sub.get("winsorize")
        if not vals:
            return done(None, "missing_input")
        value = winsorize(vals[-1], wb)
        return done(
            score_against_markers(value, sub["markers"], descending=bool(sub.get("descending"))),
            "ok",
            value=value,
        )

    if agg == "trend":
        need = int(sub.get("min_years_for_trend", 2))
        if len(vals) < need:
            return done(None, "insufficient_history", n=len(vals))
        label = classify_trend(vals[0], vals[-1], float(sub["trend_stable_band"]))
        return done(float(sub["trend_scores"][label]), "ok", value=label)

    if agg == "positive_fraction":
        present = [series[metric][y] for y in years if y in series.get(metric, {})]
        if len(present) < min_years:
            return done(None, "insufficient_history", n=len(present))
        frac = sum(1 for v in present if v is not None and v > 0) / len(present)
        return done(score_against_markers(frac, sub["markers"]), "ok", value=frac)

    if agg == "cagr":
        ordered = [
            (y, series[metric][y])
            for y in years
            if y in series.get(metric, {}) and series[metric][y] is not None
        ]
        if sub.get("requires_both_endpoints") and len(ordered) < 2:
            return done(None, "missing_input", n=len(ordered))
        (y0, v0), (y1, v1) = ordered[0], ordered[-1]
        rate = cagr(v0, v1, y1 - y0)
        if rate is None:
            return done(None, "missing_input")
        rate = winsorize(rate, sub.get("winsorize"))
        return done(score_against_markers(rate, sub["markers"]), "ok", value=rate)

    return done(None, "missing_input", reason=f"aggregation desconhecida: {agg}")


def score_block(
    block: dict[str, Any], series: dict[str, dict[int, float]], years: list[int], *, commodity: bool
) -> dict[str, Any]:
    subs = [score_subitem(s, series, years, commodity=commodity) for s in block["subitems"]]
    usable = [s for s in subs if s["points"] is not None]
    covered_weight = sum(s["weight"] for s in usable)
    if covered_weight == 0:
        return {
            "score": None,
            "weight": block["weight"],
            "status": "insufficient_history",
            "subitems": subs,
        }
    # média ponderada dos subitens disponíveis, reescalada para o peso do bloco
    weighted = sum(s["points"] / 100.0 * s["weight"] for s in usable)
    block_score = weighted / covered_weight * block["weight"]
    status = "ok" if len(usable) == len(subs) else "partial"
    return {"score": block_score, "weight": block["weight"], "status": status, "subitems": subs}


def compute_quality_score(
    series: dict[str, dict[int, float]], *, commodity_exposed: bool, config: dict[str, Any]
) -> dict[str, Any]:
    """Série anual (``{metric: {ano: valor}}``, já com as derivadas) -> score.

    ``series`` deve conter: ``net_margin``, ``roe``, ``net_income``, ``revenue``,
    ``net_debt_to_equity``, ``fcf_to_revenue``, ``fcf_to_net_income``.
    """
    all_years = sorted({y for per in series.values() for y in per})
    years = all_years[-int(config["window"]["max_years"]) :]
    min_years = int(config["window"]["min_years"])

    if len(years) < min_years:
        return {
            "score": None,
            "score_status": "incomplete",
            "window_years": len(years),
            "weight_covered": 0.0,
            "components": {
                "reason": f"apenas {len(years)} exercício(s) na janela (mínimo {min_years})"
            },
        }

    components: dict[str, Any] = {}
    total = 0.0
    weight_covered = 0.0
    for name, block in config["blocks"].items():
        res = score_block(block, series, years, commodity=commodity_exposed)
        components[name] = res
        if res["score"] is not None:
            total += res["score"]
            weight_covered += res["weight"]

    min_frac = float(config["min_weight_fraction_for_valid"])
    if weight_covered < min_frac * 100:
        return {
            "score": None,
            "score_status": "incomplete",
            "window_years": len(years),
            "weight_covered": round(weight_covered, 2),
            "components": components,
        }

    score = total / weight_covered * 100.0
    return {
        "score": round(score, 2),
        "score_status": "ok",
        "window_years": len(years),
        "weight_covered": round(weight_covered, 2),
        "components": components,
    }


# ---------------------------------------------------------------------------
# I/O -- monta a série a partir de fundamental_metrics
# ---------------------------------------------------------------------------


def _load_metric(
    instrument_id: int, metric: str, period_type: str, calc_version: str, as_of: date
) -> dict[int, float]:
    """``{ano: valor}`` -- a versão mais recente de cada reference_date com
    ``available_from <= as_of`` e ``quality_flag='ok'``."""
    rows = fetch_all(
        "select distinct on (reference_date) reference_date, metric_value "
        "from public.fundamental_metrics "
        "where instrument_id = %s and metric_name = %s and period_type = %s "
        "and calculation_version = %s and available_from <= %s "
        "and quality_flag = 'ok' and metric_value is not null "
        "order by reference_date, available_from desc",
        [instrument_id, metric, period_type, calc_version, as_of],
    )
    return {r["reference_date"].year: float(r["metric_value"]) for r in rows}


def build_series(instrument_id: int, as_of: date) -> dict[str, dict[int, float]]:
    series: dict[str, dict[int, float]] = {}
    for m in _ANNUAL_V1:
        series[m] = _load_metric(instrument_id, m, "annual", "fundamental_metrics_v1", as_of)
    for m in _POINT_IN_TIME_V1:
        series[m] = _load_metric(instrument_id, m, "point_in_time", "fundamental_metrics_v1", as_of)

    def ratio(num: str, den: str) -> dict[int, float]:
        out = {}
        for y, n in series[num].items():
            d = series[den].get(y)
            if d is not None and d != 0:
                out[y] = n / d
        return out

    series["net_debt_to_equity"] = ratio("net_debt", "equity")
    series["fcf_to_revenue"] = ratio("free_cash_flow", "revenue")
    # fcf/net_income: ano com net_income <= 0 fica indefinido -> excluído
    series["fcf_to_net_income"] = {
        y: series["free_cash_flow"][y] / series["net_income"][y]
        for y in series["free_cash_flow"]
        if series["net_income"].get(y, 0) > 0
    }
    return series


def compute_and_store_quality_score(
    ticker: str, *, as_of: date | None = None, run_id: int | None = None
) -> dict[str, Any]:
    as_of = as_of or date.today()
    inst = fetch_one(
        "select instrument_id, company_id, financial_company, commodity_exposed "
        "from public.instruments where ticker = %s and active = true",
        [ticker.upper()],
    )
    if inst is None:
        raise ValueError(f"instrumento ativo não cadastrado: {ticker}")

    owns_run = run_id is None
    if owns_run:
        run_id = start_run(PIPELINE, ticker=ticker)
    assert run_id is not None
    try:
        if bool(inst["financial_company"]):
            result = {
                "score": None,
                "score_status": "incomplete",
                "window_years": None,
                "weight_covered": None,
                "components": {
                    "reason": "perfil banco -- quality_bank_v1 sem dados (fase2_plan.md 9, 18)"
                },
            }
            profile = "bank"
        else:
            config = load_config()
            series = build_series(inst["instrument_id"], as_of)
            result = compute_quality_score(
                series, commodity_exposed=bool(inst["commodity_exposed"]), config=config
            )
            profile = "nonfinancial"

        row = {
            "company_id": inst["company_id"],
            "as_of_date": as_of,
            "profile": profile,
            "methodology_version": METHODOLOGY_VERSION,
            "score": result["score"],
            "score_status": result["score_status"],
            "calibration_status": CALIBRATION_STATUS,
            "window_years": result["window_years"],
            "weight_covered": result["weight_covered"],
            "components": _json(result["components"]),
            "config_version": METHODOLOGY_VERSION,
            "quality_reason": result["components"].get("reason")
            if isinstance(result["components"], dict)
            else None,
            "run_id": run_id,
        }
        stats = upsert_many(
            "quality_scores",
            [row],
            conflict_columns=["company_id", "as_of_date", "profile", "methodology_version"],
            update_columns=[
                "score",
                "score_status",
                "calibration_status",
                "window_years",
                "weight_covered",
                "components",
                "config_version",
                "quality_reason",
                "run_id",
            ],
        )
        if owns_run:
            finish_run(run_id, status="success", records_inserted=stats["total"])
        return {"ticker": ticker, "as_of": as_of.isoformat(), **row, **stats}
    except Exception as exc:
        if owns_run:
            finish_run(run_id, status="failed", error_message=str(exc))
        raise


def _json(obj: Any) -> str:
    import json

    def default(o: Any) -> Any:
        if isinstance(o, Decimal):
            return float(o)
        return str(o)

    return json.dumps(obj, ensure_ascii=False, default=default)
