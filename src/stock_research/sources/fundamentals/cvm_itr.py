"""Adapter de ITR -- Informacoes Trimestrais (fase1.md 44).

Mesma arquitetura do DFP (``cvm_dfp.py``), mas trimestral. Cada ZIP
``itr_cia_aberta_<ano>.zip`` traz os tres trimestres do ano (Q4 vira o DFP
anual, nao aparece aqui).

Achado validado contra o ZIP real antes de escrever qualquer logica de
derivacao (fase1.md 44): para DRE/DRA/DVA a CVM ja entrega o trimestre
ISOLADO e o ACUMULADO no mesmo arquivo, diferenciados por
``DT_INI_EXERC``/``DT_FIM_EXERC`` -- ex. Q3 (``DT_REFER=2024-09-30``) traz uma
linha com ``DT_INI_EXERC=2024-07-01`` (trimestre isolado) e outra com
``DT_INI_EXERC=2024-01-01`` (acumulado 9M). Para DFC (MD/MI) e DMPL a CVM
entrega SOMENTE o acumulado -- isolar o trimestre exigiria subtracao
explicita (``transforms/fundamentals_facts.derive_isolated_quarter``),
valida apenas para contas de fluxo aditivas (fase1.md 44: "somente para
contas/demonstracoes onde essa logica e valida").
"""

from __future__ import annotations

from pathlib import Path

from stock_research.config import load_settings
from stock_research.sources.fundamentals.base import RawDownload
from stock_research.sources.fundamentals.cvm_common import download_raw

DOCUMENT_TYPE = "ITR"
NAME = "cvm_itr"


def zip_url(year: int) -> str:
    base = load_settings()["cvm"]["base_url"]
    return f"{base}/DOC/ITR/DADOS/itr_cia_aberta_{year}.zip"


def download_year(year: int, dest_dir: Path) -> RawDownload:
    """Baixa o ZIP anual de ITR e grava em ``dest_dir`` (raw, sem transformar)."""
    rps = float(load_settings()["providers"]["cvm"].get("requests_per_second", 1.0))
    url = zip_url(year)
    dest_path = dest_dir / f"itr_cia_aberta_{year}.zip"
    return download_raw(url, dest_path, requests_per_second=rps)
