"""Adapter de precos via brapi (fase1.md 20).

Fonte SECUNDARIA e opcional. Plano gratuito cobre ~3 meses de historico e
cotacoes com defasagem -- serve so para validacao cruzada recente, NUNCA para
backfill (README, fase1.md 20). Sem ``BRAPI_TOKEN``, ``is_available()``
retorna ``False`` e quem chama pula a validacao com aviso, sem quebrar nada.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import httpx
import pandas as pd
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from stock_research.config import load_secrets, load_settings
from stock_research.logging import get_logger
from stock_research.sources.prices.base import PriceFetchResult, PriceSource
from stock_research.utils.ratelimit import throttle

logger = get_logger(__name__)

BASE_URL = "https://brapi.dev/api"
# Plano gratuito nao cobre mais que isso (fase1.md 20) -- validado em 08/2026.
BRAPI_FREE_PLAN_MAX_DAYS = 90


class BrapiUnavailableError(RuntimeError):
    """BRAPI_TOKEN ausente ou resposta invalida/inesperada."""


class BrapiPriceSource(PriceSource):
    name = "brapi"

    def __init__(self) -> None:
        self._cfg = load_settings()["providers"]["brapi"]

    def is_available(self) -> bool:
        return bool(load_secrets().brapi_token)

    def fetch_daily_history(self, symbol: str, start: date, end: date) -> PriceFetchResult:
        token = load_secrets().brapi_token
        if not token:
            raise BrapiUnavailableError("BRAPI_TOKEN nao configurado")

        range_param = _range_for_window(start, end)
        payload = self._fetch_with_retry()(symbol, range_param, token)
        frame = parse_historical(payload, start, end)
        return PriceFetchResult(
            source=self.name,
            symbol=symbol,
            requested_start=start,
            requested_end=end,
            fetched_at=datetime.now(UTC),
            frame=frame,
            metadata={"range": range_param},
        )

    def _fetch_with_retry(self):
        max_attempts = int(self._cfg.get("max_retries", 3))
        timeout = int(self._cfg.get("timeout_seconds", 20))
        rps = float(self._cfg.get("requests_per_second", 1.0))

        @retry(
            retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
            stop=stop_after_attempt(max_attempts),
            wait=wait_exponential_jitter(initial=1, max=15),
            reraise=True,
        )
        def _fetch(symbol: str, range_param: str, token: str) -> dict[str, Any]:
            throttle(self.name, rps)
            logger.info("brapi: buscando %s (range=%s)", symbol, range_param)
            url = f"{BASE_URL}/quote/{symbol}"
            params = {"range": range_param, "interval": "1d", "token": token}
            with httpx.Client(timeout=timeout) as client:
                response = client.get(url, params=params)
            if response.status_code >= 500 or response.status_code == 429:
                response.raise_for_status()
            if response.status_code >= 400:
                raise BrapiUnavailableError(f"brapi {response.status_code}: {response.text[:300]}")
            return response.json()

        return _fetch


def _range_for_window(start: date, end: date) -> str:
    days = max((end - start).days, 1)
    if days <= 5:
        return "5d"
    if days <= 30:
        return "1mo"
    return "3mo"  # plano gratuito nao cobre mais que isso


def parse_historical(payload: dict[str, Any], start: date, end: date) -> pd.DataFrame:
    """Extrai ``historicalDataPrice`` da resposta da brapi para colunas simples.

    Funcao pura (sem I/O) para ser testavel offline com um payload de fixture.
    """
    results = payload.get("results") or []
    if not results:
        raise BrapiUnavailableError(f"resposta da brapi sem 'results': {payload}")

    history = results[0].get("historicalDataPrice") or []
    rows: list[dict[str, Any]] = []
    for item in history:
        trade_date = datetime.fromtimestamp(item["date"], tz=UTC).date()
        if not (start <= trade_date < end):
            continue
        rows.append(
            {
                "trade_date": trade_date,
                "open": item.get("open"),
                "high": item.get("high"),
                "low": item.get("low"),
                "close": item.get("close"),
                "volume": item.get("volume"),
            }
        )
    return pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "volume"])
