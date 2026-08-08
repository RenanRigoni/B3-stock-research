"""Interface comum a qualquer provedor de precos diarios (fase1.md 9).

Trocar de provedor (yfinance -> outro) nao deve exigir tocar em
``pipelines/prices.py`` -- so uma nova implementacao desta interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class PriceFetchResult:
    """Resposta de um provedor, ja achatada em colunas simples (sem MultiIndex).

    ``frame`` e o dado como a fonte devolveu -- e tambem o que vai para o
    arquivo raw em disco, antes de qualquer normalizacao para as tabelas
    curated (fase1.md 27, 65).
    """

    source: str
    symbol: str
    requested_start: date
    requested_end: date  # exclusivo, como enviado ao provedor
    fetched_at: datetime
    frame: pd.DataFrame
    metadata: dict[str, Any] = field(default_factory=dict)


class PriceSource(ABC):
    """Adapter de precos diarios. Uma implementacao por provedor."""

    name: str

    @abstractmethod
    def fetch_daily_history(self, symbol: str, start: date, end: date) -> PriceFetchResult:
        """Busca OHLCV em ``[start, end)``. ``end`` e exclusivo -- quem chama decide o +1 dia."""

    def is_available(self) -> bool:
        """Provedores opcionais (ex.: brapi sem token) retornam ``False`` sem lancar excecao."""
        return True
