"""Adapter da CVM FCA -- Formulario Cadastral (fase3.md 8-10, Handoff v2 §8.2).

O FCA e um formulario ANUAL. A Secao "valor mobiliario" lista, por companhia e
por ano de referencia, cada valor mobiliario (acao ON/PN, debenture, unit, BDR)
com classe, mercado, entidade administradora, codigo de negociacao e datas de
inicio/fim de negociacao e listagem. Daqui sai o ``instrument_lifecycle``.

Arquivos usados dentro de ``fca_cia_aberta_<ano>.zip`` (schema validado contra
2010, 2015, 2016, 2017, 2018, 2019, 2020, 2023 reais -- fase1.md 45,
docs/historical_universe.md §3.2):

* ``fca_cia_aberta_<ano>.csv`` -- indice, enc cp1252, cabecalho padrao DFP
  (``CNPJ_CIA;DT_REFER;VERSAO;...;DT_RECEB;LINK_DOC``). Fonte de
  ``source_available_from`` (``DT_RECEB``).
* ``fca_cia_aberta_valor_mobiliario_<ano>.csv`` -- enc cp1252, cabecalho proprio
  (``CNPJ_Companhia;Data_Referencia;Versao;...``). Header byte-identico
  2010->2023. **``Codigo_Negociacao`` fica vazio 2010-2017** (a coluna existe, o
  valor nao) -- ver docs/historical_universe.md §3.3.
"""

from __future__ import annotations

from pathlib import Path

from stock_research.config import load_settings
from stock_research.sources.fundamentals.base import RawDownload
from stock_research.sources.fundamentals.cvm_common import download_raw

DOCUMENT_TYPE = "FCA"
NAME = "cvm_fca"

# Colunas minimas exigidas (extras toleradas -- fase1.md 45). Falha dura se o
# formato da CVM mudar (padrao do projeto).
REQUIRED_COLUMNS_FCA_INDEX = {"CNPJ_CIA", "DT_REFER", "VERSAO", "CD_CVM", "DT_RECEB"}
REQUIRED_COLUMNS_VALOR_MOBILIARIO = {
    "CNPJ_Companhia",
    "Data_Referencia",
    "Versao",
    "Valor_Mobiliario",
    "Codigo_Negociacao",
    "Mercado",
    "Data_Inicio_Negociacao",
    "Data_Fim_Negociacao",
    "Data_Inicio_Listagem",
    "Data_Fim_Listagem",
}

# Primeiro ano do dataset FCA aberto da CVM.
FIRST_YEAR = 2010


def zip_url(year: int) -> str:
    base = load_settings()["cvm"]["base_url"]
    return f"{base}/DOC/FCA/DADOS/fca_cia_aberta_{year}.zip"


def download_year(year: int, dest_dir: Path) -> RawDownload:
    """Baixa o ZIP anual do FCA (raw, sem transformar)."""
    rps = float(load_settings()["providers"]["cvm"].get("requests_per_second", 1.0))
    dest_path = dest_dir / f"fca_cia_aberta_{year}.zip"
    return download_raw(zip_url(year), dest_path, requests_per_second=rps)


def index_member(year: int) -> str:
    return f"fca_cia_aberta_{year}.csv"


def valor_mobiliario_member(year: int) -> str:
    return f"fca_cia_aberta_valor_mobiliario_{year}.csv"
