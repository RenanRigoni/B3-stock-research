"""``stock-research report TICKER``: relatorio por empresa (fase1.md 73,
Milestone 11). So leitura -- valida a engenharia das fases anteriores,
nao produz nenhum dado novo.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stock_research.config import data_dir, project_root
from stock_research.db import fetch_all, fetch_one

REPORT_VERSION = "phase1_report_v1"


def build_report(ticker: str) -> Path:
    instrument = fetch_one("select * from public.instruments where ticker = %s", [ticker.upper()])
    if instrument is None:
        raise ValueError(f"instrumento nao cadastrado: {ticker}")

    coverage = fetch_one("select * from public.v_data_coverage where ticker = %s", [ticker.upper()])
    biggest_gains = _top_returns(instrument["instrument_id"], desc=True)
    biggest_losses = _top_returns(instrument["instrument_id"], desc=False)
    biggest_volumes = _top_volumes(instrument["instrument_id"])
    events = _top_events(instrument["instrument_id"])
    fundamentals = _latest_fundamentals(instrument["instrument_id"])

    body = _render(
        ticker=ticker.upper(), instrument=instrument, coverage=coverage,
        biggest_gains=biggest_gains, biggest_losses=biggest_losses, biggest_volumes=biggest_volumes,
        events=events, fundamentals=fundamentals,
    )

    exports_dir = data_dir() / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    path = exports_dir / f"{ticker.upper()}_phase1_report.md"
    path.write_text(body, encoding="utf-8")
    return path.relative_to(project_root()) if path.is_relative_to(project_root()) else path


def _top_returns(instrument_id: int, *, desc: bool, limit: int = 10) -> list[dict[str, Any]]:
    order = "desc" if desc else "asc"
    return fetch_all(
        f"select trade_date, return_1d_adjusted, benchmark_return_1d, excess_return_1d "
        f"from public.daily_returns where instrument_id = %s and return_1d_adjusted is not null "
        f"order by return_1d_adjusted {order} limit %s",
        [instrument_id, limit],
    )


def _top_volumes(instrument_id: int, limit: int = 10) -> list[dict[str, Any]]:
    return fetch_all(
        "select trade_date, volume, volume_ratio_20 from public.daily_returns "
        "where instrument_id = %s and volume is not null order by volume desc limit %s",
        [instrument_id, limit],
    )


def _top_events(instrument_id: int, limit: int = 15) -> list[dict[str, Any]]:
    return fetch_all(
        "select * from public.v_event_study_summary where instrument_id = %s "
        "order by effective_trade_date desc limit %s",
        [instrument_id, limit],
    )


def _latest_fundamentals(instrument_id: int, limit: int = 10) -> list[dict[str, Any]]:
    return fetch_all(
        "select reference_date, period_type, metric_name, metric_value, quality_flag "
        "from public.fundamental_metrics where instrument_id = %s and quality_flag = 'ok' "
        "order by reference_date desc, metric_name limit %s",
        [instrument_id, limit],
    )


def _render(
    *, ticker: str, instrument: dict[str, Any], coverage: dict[str, Any] | None,
    biggest_gains: list[dict[str, Any]], biggest_losses: list[dict[str, Any]],
    biggest_volumes: list[dict[str, Any]], events: list[dict[str, Any]],
    fundamentals: list[dict[str, Any]],
) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# {ticker} -- Relatorio Fase 1",
        "",
        f"{instrument.get('company_name', '')} -- gerado em {now} ({REPORT_VERSION}).",
        "",
        "> Ferramenta pessoal de pesquisa. Nao constitui recomendacao de investimento "
        "(ver docs/limitations.md).",
        "",
    ]

    lines += ["## Cobertura", ""]
    if coverage:
        periodo = f"{coverage['price_first_date']} a {coverage['price_last_date']}" if coverage["price_first_date"] else "sem dados"
        lines += [
            f"- Precos: {coverage['price_rows']} pregoes ({periodo})",
            f"- Acoes corporativas: {coverage['corporate_actions']}",
            f"- Noticias ligadas: {coverage['news_links']} ({coverage['news_clusters']} clusters unicos)",
            f"- Documentos CVM: {coverage['cvm_documents']} (ultima referencia: {coverage['cvm_last_reference']})",
            f"- Eventos: {coverage['events']} ({coverage['event_studies']} com event study)",
            "",
        ]

    lines += ["## Maiores altas diarias (retorno ajustado)", "", "| Data | Retorno | Benchmark | Excesso |", "|---|---|---|---|"]
    for r in biggest_gains:
        lines.append(_return_row(r))
    lines.append("")

    lines += ["## Maiores quedas diarias (retorno ajustado)", "", "| Data | Retorno | Benchmark | Excesso |", "|---|---|---|---|"]
    for r in biggest_losses:
        lines.append(_return_row(r))
    lines.append("")

    lines += ["## Maiores volumes", "", "| Data | Volume | Ratio vs media 20d |", "|---|---|---|"]
    for r in biggest_volumes:
        ratio = f"{r['volume_ratio_20']:.2f}x" if r.get("volume_ratio_20") is not None else "-"
        lines.append(f"| {r['trade_date']} | {r['volume']:,.0f} | {ratio} |")
    lines.append("")

    lines += ["## Eventos recentes e reacao de preco", "", "| Data efetiva | Tipo | D+1 | D+5 | D+20 | Confundido |", "|---|---|---|---|---|---|"]
    for e in events:
        lines.append(
            f"| {e['effective_trade_date']} | {e['event_type']} | "
            f"{_pct(e.get('return_d1'))} | {_pct(e.get('return_d5'))} | {_pct(e.get('return_d20'))} | "
            f"{'sim' if e.get('is_confounded') else 'nao'} |"
        )
    lines.append("")

    lines += ["## Fundamentos mais recentes (quality_flag=ok)", "", "| Referencia | Periodo | Metrica | Valor |", "|---|---|---|---|"]
    for f in fundamentals:
        lines.append(f"| {f['reference_date']} | {f['period_type']} | {f['metric_name']} | {f['metric_value']} |")
    lines.append("")

    return "\n".join(lines)


def _return_row(r: dict[str, Any]) -> str:
    return (
        f"| {r['trade_date']} | {_pct(r.get('return_1d_adjusted'))} | "
        f"{_pct(r.get('benchmark_return_1d'))} | {_pct(r.get('excess_return_1d'))} |"
    )


def _pct(value: Any) -> str:
    if value is None:
        return "-"
    return f"{float(value) * 100:.2f}%"
