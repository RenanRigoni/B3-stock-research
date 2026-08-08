"""Interface comum a provedores de noticias (fase1.md 24-28).

Isola o HTTP puro do parsing -- mesma razao de ``sources/fundamentals/base.py``:
o adapter de um provedor especifico (GDELT) nao deve vazar para
``pipelines/news.py``, que so conhece esta interface. Trocar de provedor no
futuro nao deveria tocar no pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class RawNewsResponse:
    """Uma pagina de resultados ja gravada em disco, com o payload parseado.

    ``raw_path`` aponta pro JSON bruto (fase1.md 27: toda resposta arquivada
    antes da transformacao). ``articles`` e a lista de dicts como o provedor
    devolveu, sem normalizacao nenhuma ainda.
    """

    provider: str
    query: str
    articles: list[dict[str, Any]]
    raw_path: Path
    fetched_at: datetime
    request_url: str
