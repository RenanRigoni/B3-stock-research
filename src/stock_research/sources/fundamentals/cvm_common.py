"""Infra compartilhada por cadastro/DFP/ITR (fase1.md 42-46).

Tudo aqui foi validado contra ZIPs reais da CVM antes de ser escrito (fase1.md
45): separador ``;``, encoding cp1252 (nao UTF-8 -- os arquivos tem acentos em
latin-1/cp1252, ex. ``b'\\xe7'`` = "ç"), e um conjunto de colunas minimas por
tipo de arquivo. Nada disso e assumido as cegas -- ``detect_encoding`` e
``detect_delimiter`` inspecionam o arquivo real em vez de so documentar o que
foi visto uma vez.

Os CSVs de demonstracao (BPA/BPP/DRE/... por ano) chegam a centenas de MB.
``iter_csv_rows`` nunca materializa o arquivo inteiro em memoria: abre o
membro do ZIP como stream e usa ``csv.DictReader`` sobre um
``TextIOWrapper``.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from stock_research.logging import get_logger
from stock_research.sources.fundamentals.base import CvmSchemaError, RawDownload
from stock_research.utils.ratelimit import throttle

logger = get_logger(__name__)

PROVIDER = "cvm"
DOWNLOAD_TIMEOUT = httpx.Timeout(180.0, connect=20.0)

# Arquivos de "fato" (linhas por conta contabil) que viram financial_statement_facts.
# composicao_capital e parecer tem estrutura totalmente diferente (nao sao
# CD_CONTA/VL_CONTA) e ficam fora do escopo desta fase -- nao sao descartados
# por acidente, sao ignorados de proposito (nao se aplicam a fatos contabeis).
#
# DMPL fica de fora pelo mesmo motivo, confirmado contra o ZIP real: e uma
# matriz de movimentacao de patrimonio com uma coluna extra (COLUNA_DF) que
# `compute_source_row_hash` nao contempla -- o MESMO account_code (ex. "5.01"
# "Saldos Iniciais") se repete uma vez por componente do patrimonio (Capital
# Social, Reservas de Capital, Reservas de Lucro, ...), todos com identico
# cnpj/reference_date/version/periodo. Sem COLUNA_DF na chave, essas linhas
# colidem no mesmo INSERT em lote e o Postgres rejeita com "ON CONFLICT DO
# UPDATE command cannot affect row a second time". Nenhuma metrica em
# analytics/fundamentals_metrics.py usa DMPL hoje; tratar como fora de escopo
# (como composicao_capital/parecer) e mais correto que inventar uma chave.
STATEMENT_CODES = ("BPA", "BPP", "DRE", "DRA", "DFC_MD", "DFC_MI", "DVA")

# Colunas minimas exigidas por tipo de arquivo. Colunas extras sao toleradas
# (fase1.md 45: "o codigo deve tolerar colunas novas, ordem diferente").
REQUIRED_COLUMNS_METADATA = {"CNPJ_CIA", "DT_REFER", "VERSAO", "DENOM_CIA", "CD_CVM", "DT_RECEB"}
REQUIRED_COLUMNS_STATEMENT = {
    "CNPJ_CIA",
    "DT_REFER",
    "VERSAO",
    "DENOM_CIA",
    "CD_CVM",
    "GRUPO_DFP",
    "MOEDA",
    "ESCALA_MOEDA",
    "ORDEM_EXERC",
    "DT_FIM_EXERC",
    "CD_CONTA",
    "DS_CONTA",
    "VL_CONTA",
}
REQUIRED_COLUMNS_REGISTRY = {"CNPJ_CIA", "DENOM_SOCIAL", "CD_CVM", "SIT"}

# ``dfp_cia_aberta_DRE_con_2024.csv`` -> doc_type=dfp, statement=DRE, consolidado=True, ano=2024.
# ``dfp_cia_aberta_2024.csv`` (sem infixo de demonstracao) -> arquivo de metadados.
_STATEMENT_FILENAME_RE = re.compile(
    r"^(?P<doc>dfp|itr)_cia_aberta_(?P<stmt>"
    + "|".join(STATEMENT_CODES)
    + r")_(?P<con>con|ind)_(?P<year>\d{4})\.csv$",
    re.IGNORECASE,
)
_METADATA_FILENAME_RE = re.compile(
    r"^(?P<doc>dfp|itr)_cia_aberta_(?P<year>\d{4})\.csv$", re.IGNORECASE
)


@dataclass(frozen=True)
class StatementFileInfo:
    member_name: str
    statement_type: str
    is_consolidated: bool
    year: int


def parse_statement_filename(member_name: str) -> StatementFileInfo | None:
    """Decompoe o nome do arquivo. ``None`` se nao for um arquivo de demonstracao
    (ex.: composicao_capital, parecer -- ignorados de proposito)."""
    match = _STATEMENT_FILENAME_RE.match(member_name)
    if not match:
        return None
    return StatementFileInfo(
        member_name=member_name,
        statement_type=match.group("stmt").upper(),
        is_consolidated=match.group("con").lower() == "con",
        year=int(match.group("year")),
    )


def is_metadata_filename(member_name: str) -> bool:
    return bool(_METADATA_FILENAME_RE.match(member_name))


# ---------------------------------------------------------------------------
# Download
# ---------------------------------------------------------------------------


@retry(
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    stop=stop_after_attempt(4),
    wait=wait_exponential_jitter(initial=2, max=30),
    reraise=True,
)
def _get_with_retry(url: str, *, requests_per_second: float) -> httpx.Response:
    throttle(PROVIDER, requests_per_second)
    logger.info("cvm: baixando %s", url)
    with httpx.Client(timeout=DOWNLOAD_TIMEOUT, follow_redirects=True) as client:
        response = client.get(url)
    if response.status_code >= 500 or response.status_code == 429:
        response.raise_for_status()
    if response.status_code >= 400:
        raise RuntimeError(f"CVM {response.status_code} em {url}: {response.text[:300]}")
    return response


def download_raw(url: str, dest_path: Path, *, requests_per_second: float = 1.0) -> RawDownload:
    """Baixa um arquivo da CVM e grava em ``dest_path``.

    Idempotente por conteudo: se ``dest_path`` ja existe, ainda assim
    recalcula o hash do que acabou de vir da rede para detectar reprocessamento
    silencioso da fonte (fase1.md 65) -- quem chama decide se troca o arquivo.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    response = _get_with_retry(url, requests_per_second=requests_per_second)
    payload = response.content
    digest = hashlib.sha256(payload).hexdigest()

    already_existed = dest_path.exists()
    if not already_existed or hashlib.sha256(dest_path.read_bytes()).hexdigest() != digest:
        dest_path.write_bytes(payload)
        already_existed = False

    return RawDownload(
        url=url,
        local_path=dest_path,
        sha256=digest,
        bytes=len(payload),
        downloaded_at=datetime.now(UTC),
        already_existed=already_existed,
    )


# ---------------------------------------------------------------------------
# Encoding / separador -- detectados, nunca presumidos (fase1.md 45)
# ---------------------------------------------------------------------------


def detect_encoding(sample: bytes) -> str:
    """UTF-8 primeiro (superconjunto ASCII, comum em CSV moderno); se a
    amostra nao decodifica como UTF-8 estrito, cai para cp1252 -- foi o que
    ``dfp_cia_aberta_2024.zip`` e ``itr_cia_aberta_2024.zip`` reais usaram
    (acentos em latin-1/cp1252, nao em UTF-8) quando inspecionados
    manualmente antes deste parser existir.
    """
    try:
        sample.decode("utf-8", errors="strict")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1252"


def detect_delimiter(header_line: str) -> str:
    """``csv.Sniffer`` sobre o cabecalho real; cai para ``;`` (o separador
    visto em todos os arquivos da CVM inspecionados) se o sniffer nao
    conseguir decidir com confianca.
    """
    try:
        dialect = csv.Sniffer().sniff(header_line, delimiters=";,\t|")
        return dialect.delimiter
    except csv.Error:
        return ";"


# ---------------------------------------------------------------------------
# Validacao de schema (fase1.md 45)
# ---------------------------------------------------------------------------


def validate_columns(header: set[str], required: set[str], *, context: str) -> None:
    missing = required - header
    if missing:
        raise CvmSchemaError(
            f"{context}: colunas obrigatorias ausentes {sorted(missing)}. "
            f"Cabecalho encontrado: {sorted(header)}. "
            "Schema da CVM pode ter mudado -- arquivo bruto foi preservado, nada foi processado."
        )


# ---------------------------------------------------------------------------
# Leitura em streaming (nunca materializa o CSV inteiro em memoria)
# ---------------------------------------------------------------------------


def open_zip_member_text(zf: zipfile.ZipFile, member_name: str, encoding: str) -> io.TextIOWrapper:
    raw_stream = zf.open(member_name, "r")
    return io.TextIOWrapper(raw_stream, encoding=encoding, newline="")


def sniff_zip_member(zf: zipfile.ZipFile, member_name: str) -> tuple[str, str, list[str]]:
    """Le so o suficiente do membro (cabecalho + 1a linha) para decidir
    encoding/separador sem abrir o arquivo inteiro duas vezes."""
    with zf.open(member_name, "r") as fh:
        sample = fh.read(8192)
    encoding = detect_encoding(sample)
    header_line = sample.decode(encoding, errors="replace").splitlines()[0]
    delimiter = detect_delimiter(header_line)
    columns = next(csv.reader([header_line], delimiter=delimiter))
    return encoding, delimiter, columns


def detect_encoding_full(data: bytes) -> str:
    """Como ``detect_encoding`` mas sobre o arquivo inteiro, nao uma amostra.

    ``detect_encoding`` so olha 8 KB: um CSV cujo cabecalho + primeiras linhas
    sao ASCII (comum na FRE) vira utf-8 por engano e quebra ao achar um byte
    latin-1 (ex.: ``0xC9`` = "E" acentuado) mais adiante. cp1252 mapeia
    praticamente todos os 256 bytes, entao "utf-8 estrito no arquivo todo,
    senao cp1252" e uma deteccao correta -- ao custo de uma passada extra,
    aceitavel so em arquivos pequenos (a FRE tem; DFP/ITR de demonstracao nao).
    """
    try:
        data.decode("utf-8", errors="strict")
        return "utf-8"
    except UnicodeDecodeError:
        return "cp1252"


def iter_csv_rows(
    zf: zipfile.ZipFile, member_name: str, *, full_scan_encoding: bool = False
) -> Iterator[dict[str, Any]]:
    """Gera dicts (uma linha por vez) sem carregar o CSV inteiro em memoria.

    ``full_scan_encoding=True`` decide o encoding lendo o membro inteiro (ver
    ``detect_encoding_full``) em vez da amostra de 8 KB -- so para arquivos
    pequenos (FRE). O streaming linha a linha continua igual depois disso.
    """
    if full_scan_encoding:
        with zf.open(member_name, "r") as raw:
            payload = raw.read()
        encoding = detect_encoding_full(payload)
        _, delimiter, _ = sniff_zip_member(zf, member_name)
        reader = csv.DictReader(
            io.StringIO(payload.decode(encoding, errors="replace")), delimiter=delimiter
        )
        yield from reader
        return
    encoding, delimiter, _ = sniff_zip_member(zf, member_name)
    with open_zip_member_text(zf, member_name, encoding) as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        yield from reader


def sniff_text_file(path: Path) -> tuple[str, str, list[str]]:
    """Mesma logica de ``sniff_zip_member``, para um arquivo solto em disco
    (ex.: o cadastro de companhias, que a CVM serve como CSV puro, sem ZIP).
    """
    with path.open("rb") as fh:
        sample = fh.read(8192)
    encoding = detect_encoding(sample)
    header_line = sample.decode(encoding, errors="replace").splitlines()[0]
    delimiter = detect_delimiter(header_line)
    columns = next(csv.reader([header_line], delimiter=delimiter))
    return encoding, delimiter, columns


def iter_csv_rows_from_path(path: Path) -> Iterator[dict[str, Any]]:
    """Gera dicts de um CSV solto em disco, em streaming."""
    encoding, delimiter, _ = sniff_text_file(path)
    with path.open("r", encoding=encoding, newline="") as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        yield from reader


def load_metadata_index(
    zf: zipfile.ZipFile, member_name: str
) -> dict[tuple[str, str, str], dict[str, Any]]:
    """Le o arquivo de metadados (pequeno, cabe em memoria) e indexa por
    ``(cnpj, dt_refer, versao)`` -- a chave que liga cada linha de conta ao
    documento (``DT_RECEB``) que a originou.
    """
    encoding, delimiter, columns = sniff_zip_member(zf, member_name)
    validate_columns(set(columns), REQUIRED_COLUMNS_METADATA, context=member_name)

    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    with open_zip_member_text(zf, member_name, encoding) as fh:
        reader = csv.DictReader(fh, delimiter=delimiter)
        for row in reader:
            key = (row["CNPJ_CIA"], row["DT_REFER"], row["VERSAO"])
            index[key] = row
    return index
