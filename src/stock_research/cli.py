"""CLI do projeto (fase1.md 70).

Comandos ja funcionais nesta base:
    init     -- cria a arvore de dados e carrega o universo de instrumentos
    doctor   -- diagnostica configuracao e conexao
    status   -- cobertura de dados por instrumento

Os demais sao stubs que falham com a mensagem do milestone que os implementa.
Um stub nunca finge sucesso.
"""

from __future__ import annotations

from typing import Annotated

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


@app.command(name="sync-prices")
def sync_prices(
    ticker: Annotated[str | None, typer.Option("--ticker")] = None,
    all_tickers: Annotated[bool, typer.Option("--all")] = False,
    start: Annotated[str | None, typer.Option("--start")] = None,
    end: Annotated[str | None, typer.Option("--end")] = None,
    force: Annotated[bool, typer.Option("--force", help="Ignora cache e rebaixa.")] = False,
) -> None:
    """Backfill de precos historicos."""
    _not_implemented(1, "backfill de precos via yfinance")


@app.command(name="update-prices")
def update_prices(
    ticker: Annotated[str | None, typer.Option("--ticker")] = None,
) -> None:
    """Atualizacao incremental de precos."""
    _not_implemented(1, "update incremental de precos")


@app.command(name="validate-prices")
def validate_prices(
    ticker: Annotated[str, typer.Option("--ticker")],
    days: Annotated[int, typer.Option("--days")] = 60,
) -> None:
    """Validacao cruzada de precos entre provedores."""
    _not_implemented(3, "validacao cruzada yfinance x brapi")


@app.command(name="sync-cvm")
def sync_cvm(
    year: Annotated[int | None, typer.Option("--year")] = None,
    from_year: Annotated[int | None, typer.Option("--from-year")] = None,
    registry: Annotated[bool, typer.Option("--registry", help="So o cadastro de companhias.")] = False,
) -> None:
    """Download e ingestao de DFP/ITR da CVM."""
    _not_implemented(4, "ingestao de dados da CVM")


@app.command(name="sync-news")
def sync_news(
    ticker: Annotated[str, typer.Option("--ticker")],
    start: Annotated[str | None, typer.Option("--start")] = None,
    end: Annotated[str | None, typer.Option("--end")] = None,
) -> None:
    """Coleta de noticias historicas via GDELT."""
    _not_implemented(6, "coleta de noticias via GDELT")


@app.command(name="analyze-news")
def analyze_news(
    ticker: Annotated[str, typer.Option("--ticker")],
) -> None:
    """Deduplicacao, ligacao com empresas e classificacao de noticias."""
    _not_implemented(7, "deduplicacao e classificacao de noticias")


@app.command(name="build-events")
def build_events(
    ticker: Annotated[str, typer.Option("--ticker")],
) -> None:
    """Agrupa noticias em eventos e calcula effective_trade_date."""
    _not_implemented(9, "clustering de eventos")


@app.command(name="run-event-study")
def run_event_study(
    ticker: Annotated[str, typer.Option("--ticker")],
) -> None:
    """Calcula retornos, retorno anormal e CAR para cada evento."""
    _not_implemented(10, "event study")


@app.command()
def audit() -> None:
    """Relatorio de qualidade de dados."""
    _not_implemented(11, "relatorio de auditoria")


@app.command()
def report(ticker: str) -> None:
    """Relatorio HTML da empresa."""
    _not_implemented(11, f"relatorio de {ticker}")


@app.command()
def pipeline(
    ticker: Annotated[str, typer.Option("--ticker")],
    start: Annotated[str | None, typer.Option("--start")] = None,
) -> None:
    """Pipeline completo ponta a ponta."""
    _not_implemented(12, "pipeline completo")


@app.command()
def backup() -> None:
    """Dump do banco e dos dados brutos."""
    _not_implemented(11, "backup")


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
