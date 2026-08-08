"""``stock-research pipeline``: ponta a ponta para uma empresa (fase1.md
112-113, Milestone 12).

Executa, na ordem, tudo que os Milestones 1-11 construiram: precos ->
retornos (embutido em sync-prices) -> fundamentos -> noticias -> dedup ->
relevancia -> classificacao -> eventos -> event study -> relatorio. Uma
etapa falhando registra o erro e o resumo final, mas NAO aborta as
seguintes que ainda fazem sentido rodar (fase1.md 104) -- exceto quando a
etapa seguinte depende estruturalmente da anterior (eventos precisam de
noticias analisadas; sem preco nao ha o que estudar).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from stock_research.logging import get_logger

logger = get_logger(__name__)


def run_pipeline(ticker: str, *, start: date | None = None) -> dict[str, Any]:
    steps: dict[str, Any] = {}

    steps["prices"] = _step("prices", lambda: _sync_prices(ticker, start))
    steps["fundamentals_registry"] = _step("fundamentals_registry", _sync_registry_once)
    steps["fundamentals"] = _step("fundamentals", _sync_cvm_default)
    steps["news"] = _step("news", lambda: _sync_news(ticker, start))

    if steps["news"]["ok"]:
        steps["news_analysis"] = _step("news_analysis", lambda: _analyze_news(ticker))
        steps["news_classification"] = _step("news_classification", lambda: _classify_news(ticker))
    else:
        steps["news_analysis"] = {"ok": False, "skipped": True}
        steps["news_classification"] = {"ok": False, "skipped": True}

    if steps["news_analysis"].get("ok"):
        steps["events"] = _step("events", lambda: _build_events(ticker))
    else:
        steps["events"] = {"ok": False, "skipped": True}

    if steps["events"].get("ok"):
        steps["event_study"] = _step("event_study", lambda: _run_event_study(ticker))
    else:
        steps["event_study"] = {"ok": False, "skipped": True}

    steps["audit"] = _step("audit", _run_audit)
    steps["report"] = _step("report", lambda: _build_report(ticker))

    failed = [name for name, result in steps.items() if not result.get("ok") and not result.get("skipped")]
    return {"ticker": ticker, "steps": steps, "failed": failed}


def _step(name: str, fn: Any) -> dict[str, Any]:
    """A maioria dos pipelines internos ja captura a propria excecao e
    devolve ``{"status": "failed", ...}`` (fase1.md 104: erro numa empresa
    nao aborta o processo) em vez de deixar a excecao subir -- por isso
    ``ok`` aqui inspeciona o formato do retorno, nao so ``except``. Ainda
    assim mantemos o ``try/except`` como rede de seguranca para qualquer
    etapa que realmente lance (ex.: instrumento nao cadastrado)."""
    try:
        result = fn()
    except Exception as exc:
        logger.error("pipeline: etapa '%s' falhou: %s", name, exc)
        return {"ok": False, "error": str(exc)}

    ok = _result_indicates_success(result)
    if not ok:
        logger.warning("pipeline: etapa '%s' reportou falha: %s", name, result)
    return {"ok": ok, "result": result}


def _result_indicates_success(result: Any) -> bool:
    if not isinstance(result, dict):
        return True
    if result.get("status") == "failed":
        return False
    # "failed": listas de tickers/janelas que falharam (sync_prices --all, etc.).
    return not result.get("failed")


_registry_synced_this_process = False


def _sync_registry_once() -> Any:
    """Cadastro CVM muda pouco -- so precisa rodar uma vez por processo,
    nao uma vez por empresa quando `pipeline --all` existir no futuro."""
    global _registry_synced_this_process
    from stock_research.pipelines.fundamentals import sync_company_registry

    if _registry_synced_this_process:
        return {"skipped": "ja sincronizado nesta execucao"}
    result = sync_company_registry()
    _registry_synced_this_process = True
    return result


def _sync_cvm_default() -> Any:
    """So o ano corrente por padrao -- backfill historico completo
    (``config/settings.yaml cvm.default_from_year`` ate hoje) e uma operacao
    pesada e separada (``sync-cvm --from-year``), nao algo que `pipeline`
    deveria refazer do zero toda vez que roda pra uma empresa. Achado
    rodando de verdade: sem esse limite, `pipeline --ticker X` disparava
    ~16 anos x 2 tipos de documento (DFP/ITR) de download a cada execucao."""
    from datetime import date as _date

    from stock_research.pipelines.fundamentals import sync_cvm

    return sync_cvm(year=_date.today().year)


def _sync_prices(ticker: str, start: date | None) -> Any:
    from stock_research.pipelines.prices import sync_prices

    return sync_prices(ticker=ticker, start=start.isoformat() if start else None)


def _sync_news(ticker: str, start: date | None) -> Any:
    from stock_research.pipelines.news import sync_news

    return sync_news(ticker=ticker, start=start)


def _analyze_news(ticker: str) -> Any:
    from stock_research.pipelines.news_analysis import analyze_news

    return analyze_news(ticker)


def _classify_news(ticker: str) -> Any:
    from stock_research.pipelines.news_classification import classify_news

    return classify_news(ticker)


def _build_events(ticker: str) -> Any:
    from stock_research.pipelines.events import build_events

    return build_events(ticker)


def _run_event_study(ticker: str) -> Any:
    from stock_research.pipelines.event_study import run_event_study

    return run_event_study(ticker)


def _run_audit() -> Any:
    from stock_research.pipelines.audit import run_audit

    return run_audit()


def _build_report(ticker: str) -> Any:
    from stock_research.pipelines.report import build_report

    return build_report(ticker)
