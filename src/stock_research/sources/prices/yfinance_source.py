"""Adapter de precos via yfinance (fase1.md 9-10).

A semantica de ``end`` foi validada empiricamente contra a API real instalada
(yfinance 1.5.2) antes de fixar esta chamada: e exclusivo, como a
documentacao afirma. Ver ``tests/integration/test_yfinance_integration.py``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
import yfinance as yf
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from stock_research.config import load_settings
from stock_research.logging import get_logger
from stock_research.sources.prices.base import PriceFetchResult, PriceSource
from stock_research.transforms.prices import flatten_yfinance_frame
from stock_research.utils.ratelimit import throttle

logger = get_logger(__name__)


class YFinanceError(RuntimeError):
    """Falha ao consultar o yfinance apos esgotar as tentativas de retry."""


def ticker_to_yahoo_symbol(ticker: str, *, exchange: str = "B3") -> str:
    """Converte um ticker da B3 para o simbolo do Yahoo Finance (fase1.md 9).

    Indices (``^BVSP``) e simbolos ja sufixados passam direto; o sufixo
    ``.SA`` so se aplica a acoes negociadas na B3.
    """
    ticker = ticker.strip().upper()
    if ticker.startswith("^") or ticker.endswith(".SA"):
        return ticker
    if exchange.upper() != "B3":
        return ticker
    return f"{ticker}.SA"


class YFinancePriceSource(PriceSource):
    name = "yfinance"

    def __init__(self) -> None:
        cfg = load_settings()
        self._prices_cfg = cfg["prices"]
        self._provider_cfg = cfg["providers"]["yfinance"]

    def fetch_daily_history(self, symbol: str, start: date, end: date) -> PriceFetchResult:
        raw = self._fetch_with_retry()(symbol, start, end)
        frame = flatten_yfinance_frame(raw, symbol) if not raw.empty else raw
        return PriceFetchResult(
            source=self.name,
            symbol=symbol,
            requested_start=start,
            requested_end=end,
            fetched_at=datetime.now(UTC),
            frame=frame,
            metadata={
                "interval": self._prices_cfg["interval"],
                "auto_adjust": self._prices_cfg["auto_adjust"],
                "repair": self._prices_cfg["repair"],
            },
        )

    def _fetch_with_retry(self) -> Callable[[str, date, date], pd.DataFrame]:
        max_attempts = int(self._provider_cfg.get("max_retries", 3))
        timeout = int(self._provider_cfg.get("timeout_seconds", 30))
        rps = float(self._provider_cfg.get("requests_per_second", 1.0))
        prices_cfg = self._prices_cfg

        @retry(
            retry=retry_if_exception_type(Exception),
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential_jitter(initial=1, max=20),
            reraise=True,
        )
        def _fetch(symbol: str, start: date, end: date) -> pd.DataFrame:
            throttle(self.name, rps)
            logger.info("yfinance: buscando %s entre %s e %s (end exclusivo)", symbol, start, end)
            frame = _download(symbol, start, end, timeout, prices_cfg)
            if frame is None:
                raise YFinanceError(f"yfinance retornou None para {symbol}")
            return frame

        return _fetch


def _download(symbol: str, start: date, end: date, timeout: int, prices_cfg: dict[str, Any]) -> pd.DataFrame:
    return yf.download(
        tickers=symbol,
        start=start,
        end=end,
        interval=prices_cfg["interval"],
        auto_adjust=prices_cfg["auto_adjust"],
        actions=prices_cfg["actions"],
        repair=prices_cfg["repair"],
        keepna=prices_cfg["keepna"],
        progress=False,
        timeout=timeout,
    )
