"""CLI do projeto (fase1.md 70).

Comandos ja funcionais nesta base:
    init     -- cria a arvore de dados e carrega o universo de instrumentos
    doctor   -- diagnostica configuracao e conexao
    status   -- cobertura de dados por instrumento

Os demais sao stubs que falham com a mensagem do milestone que os implementa.
Um stub nunca finge sucesso.
"""

from __future__ import annotations

from typing import Annotated, Any

import typer
from rich.console import Console
from rich.table import Table

from stock_research import __version__
from stock_research.config import (
    MissingConfigError,
    ensure_data_dirs,
    load_companies,
    load_secrets,
    load_settings,
)
from stock_research.logging import setup_logging

app = typer.Typer(
    name="stock-research",
    help="Base historica de acoes da B3: precos, noticias, fundamentos e event study.",
    no_args_is_help=True,
    add_completion=False,
)

console = Console()


def _not_implemented(milestone: int, what: str) -> None:
    """Falha explicita. Prefira erro claro a resultado inventado (fase1.md 123)."""
    console.print(f"[yellow]Ainda nao implementado:[/] {what}")
    console.print(f"[dim]Previsto para o Milestone {milestone} (ver docs/roadmap.md).[/]")
    raise typer.Exit(code=2)


@app.callback()
def main(
    log_level: Annotated[
        str | None, typer.Option("--log-level", help="DEBUG, INFO, WARNING, ERROR.")
    ] = None,
) -> None:
    setup_logging(log_level)


@app.command()
def version() -> None:
    """Mostra a versao do pacote."""
    console.print(f"stock-research {__version__}")


@app.command()
def doctor() -> None:
    """Diagnostica configuracao, segredos e conexao com o banco."""
    secrets = load_secrets()
    table = Table(title="Diagnostico", show_header=True, header_style="bold")
    table.add_column("Item")
    table.add_column("Status")
    table.add_column("Detalhe")

    def row(item: str, ok: bool, detail: str, optional: bool = False) -> None:
        if ok:
            table.add_row(item, "[green]OK[/]", detail)
        elif optional:
            table.add_row(item, "[yellow]opcional[/]", detail)
        else:
            table.add_row(item, "[red]FALTA[/]", detail)

    try:
        settings = load_settings()
        row("config/settings.yaml", True, f"timezone={settings['project']['timezone']}")
    except (MissingConfigError, KeyError) as exc:
        row("config/settings.yaml", False, str(exc))

    try:
        companies = load_companies()
        n = len(companies.get("companies", []))
        b = len(companies.get("benchmarks", []))
        row("config/companies.yaml", True, f"{n} empresas, {b} benchmark(s)")
    except MissingConfigError as exc:
        row("config/companies.yaml", False, str(exc))

    row("SUPABASE_URL", bool(secrets.supabase_url), secrets.supabase_url or "vazio")
    row(
        "SUPABASE_SECRET_KEY",
        bool(secrets.supabase_secret_key or secrets.supabase_service_role_key),
        "presente" if secrets.supabase_secret_key else "ausente",
    )
    row(
        "DATABASE_URL",
        secrets.has_database,
        "configurada (backend rapido)"
        if secrets.has_database
        else "ausente -- usando PostgREST (mais lento, porem funcional)",
        optional=not secrets.has_database,
    )
    row(
        "BRAPI_TOKEN",
        secrets.has_brapi,
        "presente" if secrets.has_brapi else "validacao cruzada sera pulada",
        optional=True,
    )

    try:
        from stock_research.db import backend_name, healthcheck

        info = healthcheck()
        row(
            f"Conexao ({backend_name()})",
            True,
            f"{info['database']}, {info['tables']} tabelas",
        )
    except Exception as exc:
        # Diagnostico existe justamente para mostrar qualquer falha, sem filtrar.
        row("Conexao", False, f"{type(exc).__name__}: {exc}")

    console.print(table)


@app.command()
def init(
    load_universe: Annotated[
        bool, typer.Option("--load-universe/--no-load-universe", help="Carrega companies.yaml no banco.")
    ] = True,
) -> None:
    """Cria a arvore de dados local e carrega o universo de instrumentos."""
    created = ensure_data_dirs()
    console.print(f"[green]Arvore de dados pronta[/] ({len(created)} diretorios).")

    if not load_universe:
        return

    from stock_research.config import MissingConfigError as _MissingConfig
    from stock_research.pipelines.universe import load_universe_from_config

    try:
        result = load_universe_from_config()
    except _MissingConfig as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=2) from exc
    console.print(
        f"[green]Universo carregado[/]: {result['instruments']} instrumentos, "
        f"{result['aliases']} aliases."
    )


@app.command(name="add-company")
def add_company(ticker: str) -> None:
    """Adiciona uma empresa ao universo."""
    _not_implemented(1, f"cadastro incremental de {ticker}")


def _print_pipeline_summary(result: dict[str, Any], title: str) -> None:
    table = Table(title=title, show_header=True, header_style="bold")
    for col in ("Ticker", "Status", "Detalhe"):
        table.add_column(col)
    for ticker, outcome in result["results"].items():
        if outcome.get("status") == "failed":
            table.add_row(ticker, "[red]FALHOU[/]", str(outcome.get("error", "")))
        else:
            detail = (
                f"{outcome.get('rows', 0)} precos, {outcome.get('actions', 0)} acoes corp., "
                f"{outcome.get('quality_findings', 0)} achado(s)"
            )
            table.add_row(ticker, "[green]OK[/]", detail)
    console.print(table)
    if result["failed"]:
        console.print(f"[red]Falharam: {', '.join(result['failed'])}[/]")


@app.command(name="sync-prices")
def sync_prices(
    ticker: Annotated[str | None, typer.Option("--ticker")] = None,
    all_tickers: Annotated[bool, typer.Option("--all")] = False,
    start: Annotated[str | None, typer.Option("--start")] = None,
    end: Annotated[str | None, typer.Option("--end")] = None,
    force: Annotated[bool, typer.Option("--force", help="Ignora cache e rebaixa.")] = False,
) -> None:
    """Backfill de precos historicos (fase1.md 18)."""
    if not ticker and not all_tickers:
        console.print("[red]Informe --ticker TICKER ou --all.[/]")
        raise typer.Exit(code=2)

    from stock_research.pipelines.prices import sync_prices as run_sync_prices

    result = run_sync_prices(ticker=ticker, all_tickers=all_tickers, start=start, end=end, force=force)
    _print_pipeline_summary(result, "sync-prices")
    if result["failed"]:
        raise typer.Exit(code=1)


@app.command(name="update-prices")
def update_prices(
    ticker: Annotated[str | None, typer.Option("--ticker")] = None,
) -> None:
    """Atualizacao incremental de precos (fase1.md 19)."""
    from stock_research.pipelines.prices import update_prices as run_update_prices

    result = run_update_prices(ticker=ticker)
    _print_pipeline_summary(result, "update-prices")
    if result["failed"]:
        raise typer.Exit(code=1)


@app.command(name="validate-prices")
def validate_prices(
    ticker: Annotated[str, typer.Option("--ticker")],
    days: Annotated[int, typer.Option("--days")] = 60,
) -> None:
    """Validacao cruzada de precos entre provedores (fase1.md 21)."""
    from stock_research.pipelines.validation import run_price_validation

    result = run_price_validation(ticker=ticker, days=days)
    if result.get("skipped"):
        console.print(f"[yellow]Validacao pulada:[/] {result['reason']}")
        return

    console.print(f"[green]{result['compared']} data(s) comparada(s)[/] para {ticker.upper()}")
    for status, count in result["by_status"].items():
        console.print(f"  {status}: {count}")


@app.command(name="sync-cvm")
def sync_cvm(
    year: Annotated[int | None, typer.Option("--year")] = None,
    from_year: Annotated[int | None, typer.Option("--from-year")] = None,
    registry: Annotated[bool, typer.Option("--registry", help="So o cadastro de companhias.")] = False,
) -> None:
    """Download e ingestao de DFP/ITR da CVM (fase1.md 42-46)."""
    if registry:
        from stock_research.pipelines.fundamentals import sync_company_registry

        result = sync_company_registry()
        console.print(
            f"[green]Cadastro sincronizado[/]: {result['registry_rows']} companhia(s) no cadastro, "
            f"{result['resolved']} ticker(s) resolvido(s), {result['unresolved']} pendente(s)."
        )
        for note in result["notes"]:
            console.print(f"  {note}")
        console.print(
            "[yellow]Revise config/company_mapping.yaml e marque `confirmed: true` "
            "apos conferir CNPJ/codigo CVM.[/]"
        )
        return

    from stock_research.pipelines.fundamentals import sync_cvm as run_sync_cvm

    result = run_sync_cvm(year=year, from_year=from_year)
    table = Table(title="sync-cvm", show_header=True, header_style="bold")
    for col in ("Documento/Ano", "Status", "Detalhe"):
        table.add_column(col)
    for key, outcome in result["results"].items():
        if outcome.get("status") == "failed":
            table.add_row(key, "[red]FALHOU[/]", str(outcome.get("error", "")))
        else:
            detail = (
                f"{outcome.get('documents', 0)} documento(s), {outcome.get('facts', 0)} fato(s), "
                f"{outcome.get('skipped_rows', 0)} linha(s) descartada(s)"
            )
            table.add_row(key, "[green]OK[/]", detail)
    console.print(table)
    if result["failed"]:
        raise typer.Exit(code=1)


@app.command(name="sync-news")
def sync_news(
    ticker: Annotated[str | None, typer.Option("--ticker")] = None,
    all_tickers: Annotated[bool, typer.Option("--all")] = False,
    start: Annotated[str | None, typer.Option("--start")] = None,
    end: Annotated[str | None, typer.Option("--end")] = None,
) -> None:
    """Coleta de noticias historicas via GDELT (fase1.md 23-31)."""
    from datetime import date as _date

    from stock_research.pipelines.news import sync_news as run_sync_news
    from stock_research.pipelines.news import sync_news_all as run_sync_news_all

    start_date = _date.fromisoformat(start) if start else None
    end_date = _date.fromisoformat(end) if end else None

    if all_tickers:
        result = run_sync_news_all(start=start_date, end=end_date)
        for t, outcome in result["results"].items():
            _print_news_outcome(t, outcome)
        if result["failed"]:
            raise typer.Exit(code=1)
        return

    if not ticker:
        console.print("[red]Informe --ticker ou --all.[/]")
        raise typer.Exit(code=2)

    outcome = run_sync_news(ticker=ticker, start=start_date, end=end_date)
    _print_news_outcome(ticker, outcome)
    if outcome.get("status") == "failed":
        raise typer.Exit(code=1)


def _print_news_outcome(ticker: str, outcome: dict[str, object]) -> None:
    if outcome.get("status") == "failed":
        console.print(f"[red]{ticker} FALHOU[/]: {outcome.get('error')}")
        return
    console.print(
        f"[green]{ticker}[/]: {outcome.get('fetched', 0)} artigo(s) buscado(s), "
        f"{outcome.get('inserted', 0)} novo(s), {outcome.get('updated', 0)} atualizado(s), "
        f"{outcome.get('links', 0)} link(s) empresa-artigo -- janelas: "
        f"{outcome.get('windows_success', 0)} c/ resultado, {outcome.get('windows_empty', 0)} vazias, "
        f"{outcome.get('windows_failed', 0)} falharam, {outcome.get('windows_skipped_backoff', 0)} em backoff, "
        f"{outcome.get('windows_skipped_terminal', 0)} ja resolvidas, "
        f"{outcome.get('windows_out_of_range', 0)} fora da cobertura do GDELT"
    )


@app.command(name="analyze-news")
def analyze_news(
    ticker: Annotated[str, typer.Option("--ticker")],
) -> None:
    """Dedup por similaridade, relevancia e classificacao heuristica (fase1.md 29-37)."""
    from stock_research.pipelines.news_analysis import analyze_news as run_analyze_news
    from stock_research.pipelines.news_classification import classify_news as run_classify_news

    dedup_outcome = run_analyze_news(ticker)
    if dedup_outcome.get("status") == "failed":
        console.print(f"[red]{ticker} FALHOU (dedup/relevancia)[/]: {dedup_outcome.get('error')}")
        raise typer.Exit(code=1)
    console.print(
        f"[green]{ticker}[/]: {dedup_outcome.get('articles_considered', 0)} artigo(s) analisado(s), "
        f"{dedup_outcome.get('clusters', 0)} cluster(s) de duplicata ({dedup_outcome.get('clustered', 0)} artigo(s)), "
        f"{dedup_outcome.get('rescored', 0)} score(s) de relevancia recalculado(s)"
    )

    classify_outcome = run_classify_news(ticker)
    if classify_outcome.get("status") == "failed":
        console.print(f"[red]{ticker} FALHOU (classificacao)[/]: {classify_outcome.get('error')}")
        raise typer.Exit(code=1)
    console.print(
        f"[green]{ticker}[/]: {classify_outcome.get('classified', 0)} noticia(s) classificada(s)"
    )


@app.command(name="build-events")
def build_events(
    ticker: Annotated[str, typer.Option("--ticker")],
) -> None:
    """Agrupa noticias relevantes em eventos e calcula effective_trade_date (fase1.md 38-41)."""
    from stock_research.pipelines.events import build_events as run_build_events

    outcome = run_build_events(ticker)
    if outcome.get("status") == "failed":
        console.print(f"[red]{ticker} FALHOU[/]: {outcome.get('error')}")
        raise typer.Exit(code=1)
    console.print(
        f"[green]{ticker}[/]: {outcome.get('candidates', 0)} candidato(s), "
        f"{outcome.get('events', 0)} evento(s), {outcome.get('confounded', 0)} confundido(s) "
        f"(mais de um evento no mesmo pregao)"
    )


@app.command(name="run-event-study")
def run_event_study(
    ticker: Annotated[str, typer.Option("--ticker")],
) -> None:
    """Calcula retornos, retorno anormal e CAR para cada evento (fase1.md 53-61)."""
    from stock_research.pipelines.event_study import run_event_study as run_study

    outcome = run_study(ticker)
    if outcome.get("status") == "failed":
        console.print(f"[red]{ticker} FALHOU[/]: {outcome.get('error')}")
        raise typer.Exit(code=1)
    console.print(
        f"[green]{ticker}[/]: {outcome.get('events', 0)} evento(s) com effective_trade_date, "
        f"{outcome.get('studies', 0)} event stud(y/ies) calculado(s)"
    )


@app.command()
def audit() -> None:
    """Relatorio de qualidade de dados (fase1.md 72)."""
    from stock_research.pipelines.audit import run_audit

    path = run_audit()
    console.print(f"[green]Relatorio gerado:[/] {path}")


@app.command()
def report(ticker: str) -> None:
    """Relatorio da empresa (fase1.md 73)."""
    from stock_research.pipelines.report import build_report

    try:
        path = build_report(ticker)
    except ValueError as exc:
        console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=2) from exc
    console.print(f"[green]Relatorio gerado:[/] {path}")


@app.command()
def pipeline(
    ticker: Annotated[str, typer.Option("--ticker")],
    start: Annotated[str | None, typer.Option("--start")] = None,
) -> None:
    """Pipeline completo ponta a ponta (fase1.md 112-113)."""
    from datetime import date as _date

    from stock_research.pipelines.pipeline import run_pipeline

    start_date = _date.fromisoformat(start) if start else None
    outcome = run_pipeline(ticker, start=start_date)

    table = Table(title=f"PHASE 1 -- {ticker}", show_header=True, header_style="bold")
    table.add_column("Etapa")
    table.add_column("Status")
    for name, step in outcome["steps"].items():
        if step.get("skipped"):
            table.add_row(name, "[yellow]PULADA (dependencia falhou)[/]")
        elif step.get("ok"):
            table.add_row(name, "[green]OK[/]")
        else:
            table.add_row(name, f"[red]FALHOU[/]: {step.get('error', '')}")
    console.print(table)

    if outcome["failed"]:
        console.print(f"[yellow]Etapas com falha: {', '.join(outcome['failed'])}[/]")
        raise typer.Exit(code=1)


@app.command()
def backup() -> None:
    """Copia data/raw e config para backups/ (fase1.md 100)."""
    from stock_research.pipelines.backup import run_backup

    dest = run_backup()
    console.print(f"[green]Backup criado em:[/] {dest}")


@app.command()
def status(
    ticker: Annotated[str | None, typer.Argument()] = None,
) -> None:
    """Cobertura de dados por instrumento."""
    from stock_research.db import fetch_all

    query = "select * from public.v_data_coverage"
    params: list[str] = []
    if ticker:
        query += " where ticker = %s"
        params.append(ticker.upper())
    query += " order by ticker"

    rows = fetch_all(query, params or None)
    if not rows:
        console.print("[yellow]Nenhum instrumento cadastrado.[/] Rode `stock-research init`.")
        raise typer.Exit(code=1)

    table = Table(title="Cobertura de dados", show_header=True, header_style="bold")
    for col in ("Ticker", "Precos", "Periodo", "Acoes corp.", "Noticias", "CVM", "Eventos", "Studies"):
        table.add_column(col)

    for r in rows:
        periodo = (
            f"{r['price_first_date']} -> {r['price_last_date']}" if r["price_first_date"] else "-"
        )
        table.add_row(
            str(r["ticker"]),
            f"{r['price_rows']:,}".replace(",", "."),
            periodo,
            str(r["corporate_actions"]),
            str(r["news_links"]),
            str(r["cvm_documents"]),
            str(r["events"]),
            str(r["event_studies"]),
        )

    console.print(table)


if __name__ == "__main__":
    app()
