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
        bool,
        typer.Option("--load-universe/--no-load-universe", help="Carrega companies.yaml no banco."),
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

    result = run_sync_prices(
        ticker=ticker, all_tickers=all_tickers, start=start, end=end, force=force
    )
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
    registry: Annotated[
        bool, typer.Option("--registry", help="So o cadastro de companhias.")
    ] = False,
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


@app.command(name="sync-fre")
def sync_fre(
    year: Annotated[int | None, typer.Option("--year")] = None,
    from_year: Annotated[int | None, typer.Option("--from-year")] = None,
) -> None:
    """Download e ingestao da CVM FRE -> quantidade historica de acoes (fase2_plan.md 3)."""
    from stock_research.pipelines.share_count import sync_fre as run_sync_fre

    result = run_sync_fre(year=year, from_year=from_year)
    table = Table(title="sync-fre", show_header=True, header_style="bold")
    for col in ("Ano", "Status", "Detalhe"):
        table.add_column(col)
    for key, outcome in result["results"].items():
        if outcome.get("status") == "failed":
            table.add_row(key, "[red]FALHOU[/]", str(outcome.get("error", "")))
        else:
            detail = (
                f"{outcome.get('documents', 0)} documento(s), "
                f"{outcome.get('share_counts', 0)} linha(s) de acoes, "
                f"{outcome.get('warnings', 0)} aviso(s)"
            )
            table.add_row(key, "[green]OK[/]", detail)
    console.print(table)
    if result["failed"]:
        raise typer.Exit(code=1)


@app.command(name="sync-cvm-lifecycle")
def sync_cvm_lifecycle(
    from_year: Annotated[int | None, typer.Option("--from-year")] = None,
    to_year: Annotated[int | None, typer.Option("--to-year")] = None,
    stage: Annotated[
        str,
        typer.Option("--stage", help="all | company | instrument | seed | instruments"),
    ] = "all",
) -> None:
    """Universo historico (Fase 3 M1/M2): cadastro CVM -> company_lifecycle;
    FCA -> instrument_lifecycle; seed manual (VALE5); instrumentos identificados
    -> instruments (active=false). Ver docs/historical_universe.md."""
    from stock_research.pipelines import historical_universe as hu

    if stage == "company":
        out = {"company": hu.sync_company_lifecycle()}
    elif stage == "instrument":
        out = {"instrument": hu.sync_instrument_lifecycle(from_year=from_year, to_year=to_year)}
    elif stage == "seed":
        out = {"seed": hu.seed_manual_instruments()}
    elif stage == "instruments":
        out = {"instruments": hu.register_identified_instruments()}
    else:
        out = hu.sync_all(from_year=from_year, to_year=to_year)

    for name, res in out.items():
        console.print(f"[bold]{name}[/]: {res}")


@app.command(name="universe-coverage")
def universe_coverage_cmd(
    from_year: Annotated[int, typer.Option("--from-year")] = 2010,
    to_year: Annotated[int, typer.Option("--to-year")] = 2026,
    month: Annotated[int, typer.Option("--month", help="Mes da data anual.")] = 6,
    day: Annotated[int, typer.Option("--day")] = 30,
) -> None:
    """Cobertura do universo por data (Fase 3 M2) -- mede o vies residual.

    Mostra structural -> resolved -> with_prices -> investable, a taxa de
    nao-resolvidos e a banda normativa. `severe` (>60%) e gatilho de escalada."""
    from stock_research.analytics.universe_coverage import (
        coverage_for_dates,
        has_severe,
        yearly_dates,
    )
    from stock_research.config import load_universe_config, thresholds_from_config

    cfg = load_universe_config()
    th = thresholds_from_config(cfg)
    classes = cfg.get("eligibility", {}).get("allowed_share_classes")
    rows = coverage_for_dates(
        yearly_dates(from_year, to_year, month, day),
        thresholds=th,
        allowed_share_classes=frozenset(classes) if classes else None,
    )

    table = Table(title="Cobertura do universo", show_header=True, header_style="bold")
    for col in ("data", "struct_co", "struct_inst", "resolved", "c/preco", "investivel", "unres%", "banda"):
        table.add_column(col, justify="right")
    for r in rows:
        table.add_row(
            str(r.as_of),
            str(r.structural_companies),
            str(r.structural_instruments),
            str(r.resolved_instruments),
            str(r.instruments_with_prices),
            str(r.investable_instruments),
            f"{r.unresolved_rate:.1%}",
            r.unresolved_band,
        )
    console.print(table)

    agg: dict[str, int] = {}
    for r in rows:
        for k, v in r.rejections.items():
            agg[k] = agg.get(k, 0) + v
    console.print("[bold]Motivos de inelegibilidade (soma das datas):[/]")
    for k, v in sorted(agg.items(), key=lambda kv: -kv[1]):
        console.print(f"  {k}: {v}")

    severe = has_severe(rows)
    if severe:
        console.print(
            f"\n[red]ESCALAR PARA OPUS[/]: {len(severe)} data(s) na banda `severe` "
            f"(unresolved_rate > 60%): {', '.join(str(r.as_of) for r in severe)}"
        )


@app.command(name="compute-price-windows")
def compute_price_windows_cmd() -> None:
    """Janela canonica de preco por instrumento (Fase 3 M2.1 Bloco 1).

    price_valid_from = max(company start, class listing_start,
    01/01/source_reference_year_first), salvo excecao de continuidade
    independente (config/price_continuity_exceptions.yaml). NAO baixa preco --
    so calcula limites a partir do lifecycle. O backfill descarta linha do
    provedor fora desta janela (ticker_identity_not_proven)."""
    from stock_research.pipelines.price_window import compute_price_windows

    result = compute_price_windows()
    console.print(
        f"[green]instrument_price_window[/]: {result['written']} linha(s) em "
        f"{result['instruments']} instrumento(s)"
    )
    console.print(f"  from_precision: {result['from_precision']}")
    console.print(f"  to_precision:   {result['to_precision']}")
    console.print(
        f"  caso B (variantes com sucessor): {result['case_b_variants']}  |  "
        f"paralelas mesmo ano: {result['parallel_same_year_variants']}  |  "
        f"excecoes de continuidade: {result['continuity_exceptions']}"
    )
    disc = result["history_discarded_ticker_identity_not_proven"]
    console.print(
        f"  historico descartado (linhas < price_valid_from em instrumentos com serie): "
        f"{disc['total_rows_before_window']}"
    )
    for d in disc["by_instrument"]:
        console.print(
            f"    {d['ticker']}: {d['rows_before']} linha(s) antes de {d['price_valid_from']} "
            f"(serie comeca {d['series_first']})"
        )


@app.command(name="sync-historical-prices")
def sync_historical_prices_cmd(
    label: Annotated[str, typer.Option("--label", help="Nome do lote (nomeia o batch file).")],
    select: Annotated[
        str | None, typer.Option("--select", help="'resolved' -- seleciona do universo resolvido.")
    ] = None,
    instrument_id: Annotated[
        str | None, typer.Option("--instrument-id", help="Lista separada por virgula.")
    ] = None,
    batch_file: Annotated[
        str | None, typer.Option("--batch-file", help="Arquivo commitado com um instrument_id por linha.")
    ] = None,
    as_of: Annotated[str | None, typer.Option("--as-of", help="Data de referencia da resolucao.")] = None,
    limit: Annotated[int | None, typer.Option("--limit")] = None,
    offset: Annotated[int, typer.Option("--offset")] = 0,
    dry_run: Annotated[
        bool, typer.Option("--dry-run/--execute", help="dry-run gera batch file + ledger, sem rede.")
    ] = True,
) -> None:
    """Backfill historico de precos (Fase 3 M2.1) -- fluxo EXPLICITO.

    So instrumento com resolution_status in {resolved, seeded}, ticker valido,
    company_id e instrument_id. NUNCA usa sync-prices --all, NUNCA altera
    instruments.active. Respeita a janela canonica (instrument_price_window):
    linha do provedor fora dela nao entra em daily_prices.

    Bloco 2 desta rodada: so --dry-run (gera o batch file commitavel e o
    ledger do que seria pedido). --execute e o Bloco 3."""
    from stock_research.pipelines.price_backfill import run_backfill

    ids = [int(x) for x in instrument_id.split(",")] if instrument_id else None
    try:
        result = run_backfill(
            label=label,
            select=select,
            instrument_ids=ids,
            batch_file=batch_file,
            as_of=as_of,
            limit=limit,
            offset=offset,
            dry_run=dry_run,
        )
    except NotImplementedError as exc:
        console.print(f"[yellow]{exc}[/]")
        raise typer.Exit(code=2) from exc

    console.print(
        f"[green]dry-run[/] lote [bold]{result['label']}[/]: {result['candidates']} candidato(s), "
        f"{result['seeded']} seeded"
    )
    console.print(f"  batch file: {result['batch_file']}")
    console.print(f"  sha256: {result['batch_file_sha256']}")
    console.print(f"  price_backfill_run_id: {result['backfill_run_id']}")
    if result["without_price_window"]:
        console.print(
            f"  [yellow]sem instrument_price_window[/]: {result['without_price_window']}"
        )


@app.command(name="compute-liquidity")
def compute_liquidity_cmd(
    from_date: Annotated[str | None, typer.Option("--from")] = None,
    to_date: Annotated[str | None, typer.Option("--to")] = None,
) -> None:
    """Liquidez point-in-time (Fase 3 M2) -> liquidity_metrics.

    Volume financeiro = close BRUTO x volume (nunca adj_close). Janelas de
    20/60 pregoes via trading_calendar. NAO baixa preco -- so calcula sobre o
    que ja existe em daily_prices."""
    from stock_research.pipelines.liquidity import compute_liquidity

    result = compute_liquidity(from_date=from_date, to_date=to_date)
    console.print(
        f"[green]liquidity_metrics[/]: {result['rows']} linha(s) em "
        f"{result['instruments']} instrumento(s) "
        f"({result['first_date']} -> {result['last_date']})"
    )
    for ticker, n in sorted(result["by_ticker"].items()):
        console.print(f"  {ticker}: {n}")


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


@app.command(name="compute-metrics")
def compute_metrics(
    ticker: Annotated[str | None, typer.Argument()] = None,
) -> None:
    """Recalcula fundamental_metrics: base (Fase 1) + valuation (Fase 2, EBITDA/ROIC).

    Sem ticker: todas as empresas ativas. Idempotente (upsert por chave natural).
    """
    from stock_research.analytics.fundamentals_metrics import compute_and_store_metrics
    from stock_research.analytics.valuation_metrics import compute_and_store_valuation_metrics
    from stock_research.db import fetch_all as _fetch_all

    if ticker:
        tickers = [ticker.upper()]
    else:
        tickers = [
            r["ticker"]
            for r in _fetch_all(
                "select ticker from public.instruments "
                "where active = true and is_benchmark = false and cnpj is not null order by ticker"
            )
        ]

    table = Table(title="compute-metrics", show_header=True, header_style="bold")
    for col in ("Ticker", "Base", "Valuation"):
        table.add_column(col)
    failed = False
    for tk in tickers:
        try:
            base = compute_and_store_metrics(tk)
            val = compute_and_store_valuation_metrics(tk)
            table.add_row(tk, f"[green]{base['total']}[/]", f"[green]{val['total']}[/]")
        except Exception as exc:  # uma empresa nao aborta as outras
            failed = True
            table.add_row(tk, "[red]FALHOU[/]", str(exc)[:60])
    console.print(table)
    if failed:
        raise typer.Exit(code=1)


@app.command(name="compute-multiples")
def compute_multiples_cmd(
    company: Annotated[str | None, typer.Argument(help="ticker, CNPJ ou vazio p/ todas")] = None,
    as_of: Annotated[str | None, typer.Option("--as-of", help="AAAA-MM-DD, default hoje")] = None,
    basis: Annotated[str, typer.Option("--basis", help="fy | ttm | both")] = "both",
) -> None:
    """Market cap por companhia + múltiplos point-in-time (FY e/ou TTM) -- fase2_plan.md 4-5."""
    from datetime import date as _date

    from stock_research.analytics.valuation_multiples import compute_and_store_multiples
    from stock_research.db import fetch_all as _fetch_all

    as_of_date = _date.fromisoformat(as_of) if as_of else _date.today()
    bases = ["fy", "ttm"] if basis == "both" else [basis]
    if company:
        refs: list[Any] = [company]
    else:
        refs = [
            r["company_id"]
            for r in _fetch_all("select company_id from public.companies order by company_id")
        ]

    table = Table(title=f"compute-multiples ({as_of_date})", show_header=True, header_style="bold")
    for col in ("Companhia", "Base", "Market cap", "P/L", "EV/EBITDA", "P/VP", "DY", "Flag"):
        table.add_column(col)
    failed = False
    for ref in refs:
        for b in bases:
            try:
                r = compute_and_store_multiples(ref, as_of=as_of_date, basis=b)
                mc = f"{float(r['market_cap']) / 1e9:.1f}B" if r["market_cap"] is not None else "--"
                pe = (
                    f"{float(r['price_earnings']):.1f}" if r["price_earnings"] is not None else "--"
                )
                ev = f"{float(r['ev_ebitda']):.1f}" if r["ev_ebitda"] is not None else "--"
                pb = f"{float(r['price_book']):.2f}" if r["price_book"] is not None else "--"
                dy = (
                    f"{float(r['dividend_yield']) * 100:.1f}%"
                    if r["dividend_yield"] is not None
                    else "--"
                )
                table.add_row(str(ref), b, mc, pe, ev, pb, dy, r["quality_flag"])
            except Exception as exc:  # uma companhia nao aborta as outras
                failed = True
                table.add_row(str(ref), b, "[red]FALHOU[/]", "", "", "", "", str(exc)[:40])
    console.print(table)
    if failed:
        raise typer.Exit(code=1)


@app.command(name="compute-quality")
def compute_quality_cmd(
    ticker: Annotated[str | None, typer.Argument()] = None,
    as_of: Annotated[str | None, typer.Option("--as-of", help="AAAA-MM-DD, default hoje")] = None,
) -> None:
    """Quality Score não-financeiro (0-100), independente de preço -- fase2_plan.md 8, 17."""
    from datetime import date as _date

    from stock_research.analytics.quality_score import compute_and_store_quality_score
    from stock_research.db import fetch_all as _fetch_all

    as_of_date = _date.fromisoformat(as_of) if as_of else _date.today()
    if ticker:
        tickers = [ticker.upper()]
    else:
        tickers = [
            r["ticker"]
            for r in _fetch_all(
                "select ticker from public.instruments "
                "where active = true and is_benchmark = false and cnpj is not null order by ticker"
            )
        ]

    table = Table(title=f"compute-quality ({as_of_date})", show_header=True, header_style="bold")
    for col in ("Ticker", "Score", "Status", "Anos", "Peso", "Calibração"):
        table.add_column(col)
    failed = False
    for tk in tickers:
        try:
            r = compute_and_store_quality_score(tk, as_of=as_of_date)
            score = f"{float(r['score']):.1f}" if r["score"] is not None else "--"
            table.add_row(
                tk,
                score,
                r["score_status"],
                str(r["window_years"] or "--"),
                str(r["weight_covered"] or "--"),
                r["calibration_status"],
            )
        except Exception as exc:  # uma empresa nao aborta as outras
            failed = True
            table.add_row(tk, "[red]FALHOU[/]", str(exc)[:50], "", "", "")
    console.print(table)
    if failed:
        raise typer.Exit(code=1)


@app.command(name="compute-dcf")
def compute_dcf_cmd(
    company: Annotated[str | None, typer.Argument(help="ticker/CNPJ ou vazio p/ todas")] = None,
    as_of: Annotated[str | None, typer.Option("--as-of", help="AAAA-MM-DD, default hoje")] = None,
) -> None:
    """DCF FCFF (só não-financeiras) + WACC + cenários + margem de segurança -- fase2_plan.md 10."""
    from datetime import date as _date

    from stock_research.pipelines.valuation_dcf import compute_and_store_dcf, run_dcf

    as_of_date = _date.fromisoformat(as_of) if as_of else _date.today()
    if company:
        outcomes = {company: compute_and_store_dcf(company, as_of=as_of_date)}
        failed = []
    else:
        res = run_dcf(as_of=as_of_date)
        outcomes, failed = res["results"], res["failed"]

    table = Table(title=f"compute-dcf ({as_of_date})", show_header=True, header_style="bold")
    for col in ("Companhia", "Método", "WACC/coe", "FCFF ini", "Fair (base)", "MoS (base)", "Flag"):
        table.add_column(col)
    for ref, o in outcomes.items():
        if o.get("status") == "failed":
            table.add_row(str(ref), "", "[red]FALHOU[/]", "", "", "", str(o.get("error", ""))[:40])
            continue
        method = "residual_income+ddm" if o.get("status") == "bank" else "fcff"
        rate = o.get("wacc") if o.get("wacc") is not None else o.get("cost_of_equity")
        w = f"{float(rate) * 100:.1f}%" if rate is not None else "--"
        fc = f"{float(o['fcff_start']) / 1e9:.1f}B" if o.get("fcff_start") is not None else "--"
        fv = (
            f"R${float(o['fair_value_base']):.2f}" if o.get("fair_value_base") is not None else "--"
        )
        mos = (
            f"{float(o['margin_of_safety_base']) * 100:.0f}%"
            if o.get("margin_of_safety_base") is not None
            else "--"
        )
        table.add_row(
            str(ref),
            method,
            w,
            fc,
            fv,
            mos,
            str(o.get("quality_flag") or o.get("status") or ""),
        )
    console.print(table)
    if failed:
        raise typer.Exit(code=1)


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
    for col in (
        "Ticker",
        "Precos",
        "Periodo",
        "Acoes corp.",
        "Noticias",
        "CVM",
        "Eventos",
        "Studies",
    ):
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
