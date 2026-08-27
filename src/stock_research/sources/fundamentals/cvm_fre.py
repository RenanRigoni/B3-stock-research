"""Adapter de FRE -- Formulario de Referencia (fase2_plan.md 3, 13.1-13.2).

A FRE e um documento ANUAL, com varias reapresentacoes ao longo do ano (a FRE
de referencia 2024-12-31 da Petrobras teve 28+ versoes -- fase2_plan.md 13.1),
cada uma com seu proprio ``DT_RECEB``. Daqui sai a quantidade historica de
acoes por classe.

Arquivos usados dentro do ZIP ``fre_cia_aberta_<ano>.zip`` (validados contra
2024 e 2013 reais antes deste modulo existir -- fase1.md 45):

* ``fre_cia_aberta_<ano>.csv`` -- indice (cabecalho no padrao DFP:
  ``CNPJ_CIA;DT_REFER;VERSAO;...;DT_RECEB;LINK_DOC``), enc utf-8. Fonte de
  ``available_from``.
* ``fre_cia_aberta_capital_social_<ano>.csv`` -- enc cp1252, colunas com nomes
  proprios (``CNPJ_Companhia``, ``Data_Referencia``, ``Versao`` -- NAO o padrao
  DFP). Varias linhas por ``(cnpj, data_ref, versao)``, uma por ``Tipo_Capital``
  (Capital Emitido / Subscrito / Integralizado). ``Capital Integralizado`` e a
  base economicamente correta para ``shares_issued`` (fase2_plan.md 13.1).
* ``fre_cia_aberta_distribuicao_capital_<ano>.csv`` -- enc cp1252. Traz
  ``Quantidade_Acoes_*_Circulacao`` = FREE FLOAT (exclui o bloco de controle),
  NAO "emitidas menos tesouraria" (achado 2026-08-27, fase2_plan.md 24).
"""

from __future__ import annotations

from pathlib import Path

from stock_research.config import load_settings
from stock_research.sources.fundamentals.base import RawDownload
from stock_research.sources.fundamentals.cvm_common import download_raw

DOCUMENT_TYPE = "FRE"
NAME = "cvm_fre"

# Encoding varia por arquivo E por ano na FRE (fase2_plan.md 27): o indice pode
# ser utf-8 num ano e cp1252 noutro (nome de empresa com acento);
# capital_social/distribuicao sao cp1252 mas com cabecalho + primeiras linhas
# ASCII, o que engana a deteccao por amostra. Os 3 arquivos sao pequenos, entao
# a ingestao usa `iter_csv_rows(..., full_scan_encoding=True)`.

# Colunas minimas exigidas por arquivo (extras sao toleradas -- fase1.md 45).
REQUIRED_COLUMNS_FRE_INDEX = {"CNPJ_CIA", "DT_REFER", "VERSAO", "CD_CVM", "DT_RECEB"}
REQUIRED_COLUMNS_CAPITAL_SOCIAL = {
    "CNPJ_Companhia",
    "Data_Referencia",
    "Versao",
    "Tipo_Capital",
    "Quantidade_Acoes_Ordinarias",
    "Quantidade_Acoes_Preferenciais",
    "Quantidade_Total_Acoes",
}
REQUIRED_COLUMNS_DISTRIBUICAO = {
    "CNPJ_Companhia",
    "Data_Referencia",
    "Versao",
    "Quantidade_Acoes_Ordinarias_Circulacao",
    "Quantidade_Acoes_Preferenciais_Circulacao",
    "Quantidade_Total_Acoes_Circulacao",
}


def zip_url(year: int) -> str:
    base = load_settings()["cvm"]["base_url"]
    return f"{base}/DOC/FRE/DADOS/fre_cia_aberta_{year}.zip"


def download_year(year: int, dest_dir: Path) -> RawDownload:
    """Baixa o ZIP anual de FRE e grava em ``dest_dir`` (raw, sem transformar)."""
    rps = float(load_settings()["providers"]["cvm"].get("requests_per_second", 1.0))
    dest_path = dest_dir / f"fre_cia_aberta_{year}.zip"
    return download_raw(zip_url(year), dest_path, requests_per_second=rps)


def index_member(year: int) -> str:
    return f"fre_cia_aberta_{year}.csv"


def capital_social_member(year: int) -> str:
    return f"fre_cia_aberta_capital_social_{year}.csv"


def distribuicao_member(year: int) -> str:
    return f"fre_cia_aberta_distribuicao_capital_{year}.csv"
