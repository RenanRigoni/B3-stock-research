"""Roda o backfill de noticias (fase1.1) para uma lista de tickers, em ordem,
ate cada um esgotar a cobertura esperada ou o orcamento de tempo do processo
acabar. Pensado pra rodar num job de CI (GitHub Actions) que reinicia
periodicamente via cron -- cada invocacao retoma do checkpoint salvo no
Supabase, entao interromper e recomecar nunca perde trabalho.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import date

from stock_research.db.rest import fetch_all
from stock_research.pipelines.news import _get_instrument, _languages, _weekly_chunks, sync_news

_NONTERMINAL_FAILURE_STATUSES = {"rate_limited", "timeout", "parse_error", "http_error"}
_TERMINAL_STATUSES = {"success_with_results", "success_empty", "unsupported_date_range"}


def _expected_windows() -> int:
    return len(_weekly_chunks(date(2017, 1, 1), date.today())) * len(_languages())


def _is_done(ticker: str) -> tuple[bool, int, int]:
    instrument = _get_instrument(ticker)
    rows = fetch_all(
        "select status, count(*) as n from public.news_backfill_checkpoints "
        "where instrument_id = %s group by 1",
        [instrument["instrument_id"]],
    )
    counts = {r["status"]: r["n"] for r in rows}
    pending = sum(counts.get(s, 0) for s in _NONTERMINAL_FAILURE_STATUSES)
    terminal = sum(counts.get(s, 0) for s in _TERMINAL_STATUSES)
    expected = _expected_windows()
    return pending == 0 and terminal >= expected, terminal, expected


def run(tickers: list[str], budget_seconds: int) -> None:
    deadline = time.monotonic() + budget_seconds
    for ticker in tickers:
        while time.monotonic() < deadline:
            done, terminal, expected = _is_done(ticker)
            print(f"[{ticker}] terminal={terminal}/{expected}", flush=True)
            if done:
                print(f"[{ticker}] COMPLETO", flush=True)
                break
            result = sync_news(ticker=ticker, start=date(2017, 1, 1), end=date.today())
            print(f"[{ticker}] ciclo: {result}", flush=True)
            time.sleep(5)
        else:
            print(f"orcamento de tempo esgotado antes de terminar {ticker}", flush=True)
            return
    print("todos os tickers completos", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tickers", nargs="+", help="tickers em ordem de prioridade")
    parser.add_argument(
        "--budget-minutes", type=int, default=340,
        help="orcamento de tempo total do processo antes de sair pro job de CI reiniciar",
    )
    args = parser.parse_args()
    run(args.tickers, args.budget_minutes * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
