"""Throttling simples por provedor (fase1.md 68).

Nao e um limitador sofisticado -- sem burst, sem coordenacao entre processos.
Para uso pessoal e sequencial (uma chamada de CLI por vez) isso basta: so
garante o intervalo minimo entre duas chamadas consecutivas ao mesmo provedor.
"""

from __future__ import annotations

import threading
import time

_last_call: dict[str, float] = {}
_lock = threading.Lock()


def throttle(provider: str, requests_per_second: float) -> None:
    """Bloqueia o tempo necessario para respeitar ``requests_per_second``."""
    if requests_per_second <= 0:
        return
    min_interval = 1.0 / requests_per_second
    with _lock:
        last = _last_call.get(provider, 0.0)
        wait = min_interval - (time.monotonic() - last)
        if wait > 0:
            time.sleep(wait)
        _last_call[provider] = time.monotonic()
