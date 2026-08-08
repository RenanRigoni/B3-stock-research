"""Adapter de DFP -- Demonstracoes Financeiras Padronizadas (fase1.md 43).

DFP e a demonstracao ANUAL. Cada ZIP ``dfp_cia_aberta_<ano>.zip`` traz, por
empresa, o ultimo exercicio encerrado (``ORDEM_EXERC=ULTIMO``) e o anterior
(``PENULTIMO``) para comparacao -- ambos com ``DT_INI_EXERC``/``DT_FIM_EXERC``
cobrindo o ano cheio (ou ausentes, no caso do balanco patrimonial, que e uma
posicao e nao um fluxo).
"""

from __future__ import annotations

from pathlib import Path

from stock_research.config import load_settings
from stock_research.sources.fundamentals.base import RawDownload
from stock_research.sources.fundamentals.cvm_common import download_raw

DOCUMENT_TYPE = "DFP"
NAME = "cvm_dfp"


def zip_url(year: int) -> str:
    base = load_settings()["cvm"]["base_url"]
    return f"{base}/DOC/DFP/DADOS/dfp_cia_aberta_{year}.zip"


def download_year(year: int, dest_dir: Path) -> RawDownload:
    """Baixa o ZIP anual de DFP e grava em ``dest_dir`` (raw, sem transformar)."""
    rps = float(load_settings()["providers"]["cvm"].get("requests_per_second", 1.0))
    url = zip_url(year)
    dest_path = dest_dir / f"dfp_cia_aberta_{year}.zip"
    return download_raw(url, dest_path, requests_per_second=rps)
