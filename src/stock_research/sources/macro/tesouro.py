"""Tesouro Transparente -- preços e taxas do Tesouro Direto (fase2_plan.md 21.2).

Um CSV único (~14 MB) com todos os títulos e datas. Validado contra o arquivo
real em 2026-08-27: encoding latin-1/cp1252, separador ``;``, decimal com
vírgula, datas ``dd/mm/aaaa``. Cabeçalho:

    Tipo Titulo;Data Vencimento;Data Base;Taxa Compra Manha;Taxa Venda Manha;
    PU Compra Manha;PU Venda Manha;PU Base Manha

Interessa só ``Tesouro Prefixado`` e ``Tesouro Prefixado com Juros Semestrais``
(risk-free NOMINAL do DCF V1 -- §21.2).
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from stock_research.logging import get_logger
from stock_research.utils.ratelimit import throttle

logger = get_logger(__name__)

PROVIDER = "tesouro_transparente"
CSV_URL = (
    "https://www.tesourotransparente.gov.br/ckan/dataset/"
    "df56aa42-484a-4a59-8184-7676580c81e3/resource/"
    "796d2059-14e9-44e3-80c9-2d9e30b405c1/download/PrecoTaxaTesouroDireto.csv"
)
ENCODING = "latin-1"
DELIMITER = ";"
PREFIXADO_TYPES = ("Tesouro Prefixado", "Tesouro Prefixado com Juros Semestrais")
REQUIRED_COLUMNS = {
    "Tipo Titulo",
    "Data Vencimento",
    "Data Base",
    "Taxa Compra Manha",
    "Taxa Venda Manha",
}
DOWNLOAD_TIMEOUT = httpx.Timeout(180.0, connect=20.0)


@dataclass(frozen=True)
class PrefixadoQuote:
    tipo: str
    maturity: date
    base_date: date
    taxa_compra: float
    taxa_venda: float

    @property
    def taxa_media(self) -> float:
        return (self.taxa_compra + self.taxa_venda) / 2.0


def _parse_br_date(text: str) -> date | None:
    try:
        return datetime.strptime(text.strip(), "%d/%m/%Y").date()
    except ValueError:
        return None


def _parse_br_decimal(text: str) -> float | None:
    try:
        return float(text.strip().replace(".", "").replace(",", ".")) / 100.0
    except (ValueError, AttributeError):
        return None


def download_csv(dest_dir: Path, *, requests_per_second: float = 0.5) -> tuple[Path, str]:
    """Baixa o CSV único e grava em disco. Devolve ``(caminho, sha256)``."""
    throttle(PROVIDER, requests_per_second)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "PrecoTaxaTesouroDireto.csv"
    logger.info("tesouro: baixando %s", CSV_URL)
    with httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        response = client.get(CSV_URL)
    response.raise_for_status()
    payload = response.content
    dest.write_bytes(payload)
    return dest, hashlib.sha256(payload).hexdigest()


def iter_prefixado(path: Path) -> list[PrefixadoQuote]:
    """Lê o CSV e devolve só as cotações de Tesouro Prefixado válidas."""
    text = path.read_text(encoding=ENCODING)
    lines = text.splitlines()
    header = set(next(csv.reader([lines[0]], delimiter=DELIMITER)))
    missing = REQUIRED_COLUMNS - header
    if missing:
        raise ValueError(
            f"PrecoTaxaTesouroDireto.csv: colunas ausentes {sorted(missing)} -- "
            "schema da fonte pode ter mudado"
        )

    quotes: list[PrefixadoQuote] = []
    for row in csv.DictReader(lines, delimiter=DELIMITER):
        tipo = (row.get("Tipo Titulo") or "").strip()
        if tipo not in PREFIXADO_TYPES:
            continue
        maturity = _parse_br_date(row.get("Data Vencimento", ""))
        base_date = _parse_br_date(row.get("Data Base", ""))
        compra = _parse_br_decimal(row.get("Taxa Compra Manha", ""))
        venda = _parse_br_decimal(row.get("Taxa Venda Manha", ""))
        if maturity is None or base_date is None or compra is None or venda is None:
            continue
        quotes.append(PrefixadoQuote(tipo, maturity, base_date, compra, venda))
    return quotes


def latest_downloaded_at() -> datetime:
    return datetime.now(UTC)
