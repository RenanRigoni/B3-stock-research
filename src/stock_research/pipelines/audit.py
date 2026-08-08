"""``stock-research audit``: relatorio de qualidade de dados (fase1.md 72,
Milestone 11).

So LE o banco -- nao corrige nada, nao decide nada. Agrega o que os outros
milestones ja foram gravando ao longo do caminho (``quality_findings`` via
``record_finding``, `` v_data_coverage``, ``v_manual_review_queue``) num
unico relatorio Markdown, pra nao precisar rodar dez queries manuais pra
saber se a base esta saudavel.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from stock_research.config import data_dir, project_root
from stock_research.db import fetch_all

REPORT_VERSION = "audit_v1"


def run_audit() -> Path:
    coverage = fetch_all("select * from public.v_data_coverage order by ticker")
    findings_summary = fetch_all(
        "select pipeline, check_name, severity, count(*) as n "
        "from public.quality_findings where resolved_at is null "
        "group by 1, 2, 3 order by severity desc, n desc"
    )
    recent_errors = fetch_all(
        "select pipeline, check_name, message, entity_type, entity_id, detected_at "
        "from public.quality_findings where resolved_at is null and severity = 'ERROR' "
        "order by detected_at desc limit 20"
    )
    review_queue = fetch_all(
        "select reason, count(*) as n from public.v_manual_review_queue group by 1 order by 2 desc"
    )
    price_divergences = fetch_all(
        "select count(*) as n from public.price_validations where status in ('warning', 'error')"
    )
    unmapped = fetch_all(
        "select ticker from public.instruments where active = true and cnpj is null and is_benchmark = false"
    )
    lookahead_violations = fetch_all(
        "select count(*) as n from public.financial_statement_facts "
        "where available_from is not null and available_from > filing_received_at + interval '1 day'"
    )

    body = _render_markdown(
        coverage=coverage,
        findings_summary=findings_summary,
        recent_errors=recent_errors,
        review_queue=review_queue,
        price_divergences=price_divergences[0]["n"] if price_divergences else 0,
        unmapped_tickers=[r["ticker"] for r in unmapped],
        lookahead_violations=lookahead_violations[0]["n"] if lookahead_violations else 0,
    )

    exports_dir = data_dir() / "exports"
    exports_dir.mkdir(parents=True, exist_ok=True)
    path = exports_dir / f"data_quality_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}.md"
    path.write_text(body, encoding="utf-8")
    return path.relative_to(project_root()) if path.is_relative_to(project_root()) else path


def _render_markdown(
    *,
    coverage: list[dict[str, Any]],
    findings_summary: list[dict[str, Any]],
    recent_errors: list[dict[str, Any]],
    review_queue: list[dict[str, Any]],
    price_divergences: int,
    unmapped_tickers: list[str],
    lookahead_violations: int,
) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lines = ["# Relatorio de qualidade de dados", "", f"Gerado em {now} ({REPORT_VERSION}).", ""]

    lines += ["## Look-ahead (fase1.md 63)", ""]
    if lookahead_violations == 0:
        lines.append("Nenhuma violacao encontrada: todo fato tem `available_from` coerente com `filing_received_at`.")
    else:
        lines.append(
            f"**{lookahead_violations} fato(s) com `available_from` suspeito** "
            "(mais de 1 dia depois de `filing_received_at`). Investigar antes de confiar em analises point-in-time."
        )
    lines.append("")

    lines += ["## Cobertura por instrumento", "", "| Ticker | Precos | Periodo | Noticias | CVM docs | Eventos | Studies |",
               "|---|---|---|---|---|---|---|"]
    for r in coverage:
        periodo = f"{r['price_first_date']} a {r['price_last_date']}" if r["price_first_date"] else "-"
        lines.append(
            f"| {r['ticker']} | {r['price_rows']} | {periodo} | {r['news_links']} | "
            f"{r['cvm_documents']} | {r['events']} | {r['event_studies']} |"
        )
    lines.append("")

    lines += ["## Achados de qualidade em aberto (por pipeline/check)", ""]
    if not findings_summary:
        lines.append("Nenhum achado em aberto.")
    else:
        lines.append("| Severidade | Pipeline | Check | Ocorrencias |")
        lines.append("|---|---|---|---|")
        for r in findings_summary:
            lines.append(f"| {r['severity']} | {r['pipeline']} | {r['check_name']} | {r['n']} |")
    lines.append("")

    lines += ["## Erros recentes (ultimos 20)", ""]
    if not recent_errors:
        lines.append("Nenhum erro em aberto.")
    else:
        for r in recent_errors:
            lines.append(f"- `{r['detected_at']}` [{r['pipeline']}/{r['check_name']}] {r['message']}")
    lines.append("")

    lines += ["## Fila de revisao manual", ""]
    if not review_queue:
        lines.append("Vazia.")
    else:
        for r in review_queue:
            lines.append(f"- {r['reason']}: {r['n']}")
    lines.append("")

    lines += ["## Validacao cruzada de precos", "", f"{price_divergences} divergencia(s) warning/error registrada(s).", ""]

    lines += ["## Empresas ativas sem CNPJ mapeado", ""]
    if not unmapped_tickers:
        lines.append("Nenhuma -- todas as empresas ativas tem CNPJ resolvido.")
    else:
        lines.append("Rode `stock-research sync-cvm --registry` e confira `config/company_mapping.yaml`:")
        for t in unmapped_tickers:
            lines.append(f"- {t}")
    lines.append("")

    return "\n".join(lines)
