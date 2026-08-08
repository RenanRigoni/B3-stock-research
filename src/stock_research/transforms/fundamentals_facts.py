"""Transformacoes puras: linha CSV da CVM -> fato normalizado (fase1.md 46-48).

Nenhuma funcao aqui faz I/O. Isso torna a logica mais delicada do projeto --
point-in-time e a derivacao de trimestre isolado -- testavel sem rede e sem
banco.

Politica de ``available_from`` (fase1.md 47, documentada aqui porque e onde a
decisao e tomada):

    A CVM informa ``DT_RECEB`` (data de recebimento do documento) no arquivo
    de metadados, mas so a DATA, sem hora. Como nao sabemos se o documento
    ficou publico as 8h ou as 19h daquele dia, tratamos o dado como
    disponivel a partir do FIM do dia de recebimento (23:59:59, horario de
    Brasilia). E a mesma logica conservadora usada no ``date_only_policy`` de
    eventos (fase1.md 39): quando so a data e conhecida, arredondar para o
    lado que nunca inventa disponibilidade cedo demais.

    Offset fixo de -03:00 (nao ``zoneinfo``/tzdata) de proposito: o Brasil nao
    tem horario de verao desde 2019, e a precisao aqui ja e de um dia inteiro
    -- a diferenca de 1h em anos com DST (pre-2019) nao muda o dia calendario
    em que o fato se torna elegivel numa consulta point-in-time.

    Quando ``DT_RECEB`` esta ausente, ``available_from`` fica ``None`` -- o
    documento so passa a contar em consultas point-in-time apos alguem
    resolver isso manualmente (nunca estimamos, fase1.md 47).
"""

from __future__ import annotations

import hashlib
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from stock_research.sources.fundamentals.cvm_common import StatementFileInfo

BRT = timezone(timedelta(hours=-3))

_SCALE_BY_LABEL = {"MIL": 1000, "MILHAO": 1_000_000, "UNIDADE": 1}

# Estatisticas de posicao (balanco patrimonial): nao sao fluxo, isolar
# "trimestre" nao faz sentido -- e uma fotografia em ``DT_FIM_EXERC``.
_POINT_IN_TIME_STATEMENTS = {"BPA", "BPP"}

# Demonstracoes de fluxo aditivas onde "isolado = acumulado_atual -
# acumulado_anterior" e contabilmente valido (fase1.md 44). DRE/DRA ficam de
# fora porque a propria CVM ja entrega o trimestre isolado nessas (validado
# contra o ZIP real: ver cvm_itr.py). DMPL fica de fora porque e uma matriz de
# movimentacao de patrimonio (colunas por componente), nao um valor unico por
# conta -- subtrair exigiria reconciliar ``COLUNA_DF``, fora do escopo desta
# fase.
ADDITIVE_FLOW_STATEMENTS = {"DFC_MD", "DFC_MI", "DVA"}


def end_of_day_brt(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=BRT)


def parse_date(text: str | None) -> date | None:
    if not text or not text.strip():
        return None
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def parse_decimal(text: str | None) -> Decimal | None:
    if text is None or not text.strip():
        return None
    try:
        return Decimal(text.strip())
    except InvalidOperation:
        return None


def parse_scale(label: str | None) -> int | None:
    """``None`` quando o rotulo nao e reconhecido -- quem chama decide
    descartar a linha e registrar o achado, nunca assumir escala 1 (fase1.md
    123: dado incorreto e pior que dado ausente)."""
    if not label:
        return None
    return _SCALE_BY_LABEL.get(label.strip().upper())


def normalize_fiscal_year_order(raw: str | None) -> str | None:
    """``ÚLTIMO``/``PENÚLTIMO`` chegam com acento (as vezes mal decodificado
    dependendo do encoding real do arquivo) -- normaliza para ASCII estavel.

    NFKD antes do encode/ignore e obrigatorio: sem decompor o acento em
    marca combinante separada, ``encode("ascii", "ignore")`` descarta a letra
    INTEIRA (``"ÚLTIMO"`` -> ``"LTIMO"``, 5 letras) em vez de so o acento
    (-> ``"ULTIMO"``), quebrando o `in` logo abaixo.
    """
    if not raw:
        return None
    ascii_form = (
        unicodedata.normalize("NFKD", raw).encode("ascii", errors="ignore").decode("ascii").upper()
    )
    if "ULTIMO" in ascii_form and "PEN" in ascii_form:
        return "PENULTIMO"
    if "ULTIMO" in ascii_form:
        return "ULTIMO"
    return raw.strip().upper() or None


def infer_period_type(
    *, document_type: str, statement_type: str, period_start: date | None, period_end: date | None
) -> str:
    """``annual`` | ``quarterly`` | ``ytd`` | ``point_in_time`` | ``unknown``.

    Regra validada contra ZIPs reais (fase1.md 44, ver docstring de
    ``cvm_itr.py``): ITR traz tanto o trimestre isolado quanto o acumulado
    para DRE/DRA/DVA na mesma DT_REFER, diferenciados por DT_INI_EXERC. Nunca
    hardcoda por nome de demonstracao sozinho -- usa as datas reais da linha.
    """
    if statement_type in _POINT_IN_TIME_STATEMENTS:
        return "point_in_time"
    if period_start is None or period_end is None:
        return "unknown"
    if document_type == "DFP":
        return "annual"
    if period_start.month == 1 and period_start.day == 1:
        return "ytd"
    return "quarterly"


def compute_source_row_hash(
    *,
    cnpj: str,
    document_type: str,
    statement_type: str,
    is_consolidated: bool,
    reference_date: date,
    version: str,
    period_start: date | None,
    period_end: date | None,
    account_code: str,
) -> str:
    """Identidade estavel da linha (fase1.md 46). Deliberadamente NAO inclui
    ``value``: se a CVM corrigir um numero sob a mesma versao (raro -- o
    mecanismo normal de correcao e a reapresentacao, que incrementa
    ``VERSAO``), reprocessar atualiza o valor no lugar em vez de duplicar a
    linha, espelhando como ``daily_prices`` trata correcao de fonte
    (``pipelines/prices.py:_detect_data_changes``).
    """
    key = "|".join(
        [
            cnpj,
            document_type,
            statement_type,
            "1" if is_consolidated else "0",
            reference_date.isoformat(),
            str(version),
            period_start.isoformat() if period_start else "",
            period_end.isoformat() if period_end else "",
            account_code,
        ]
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class FactBuildResult:
    row: dict[str, Any] | None
    error: str | None = None


def availability_from_metadata(metadata_row: dict[str, Any] | None) -> tuple[datetime | None, datetime | None]:
    """``(filing_received_at, available_from)`` a partir da linha do arquivo
    de metadados (``DT_RECEB``). Ver politica no docstring do modulo."""
    if metadata_row is None:
        return None, None
    received = parse_date(metadata_row.get("DT_RECEB"))
    if received is None:
        return None, None
    filing_received_at = datetime(received.year, received.month, received.day, 0, 0, 0, tzinfo=BRT)
    return filing_received_at, end_of_day_brt(received)


def build_fact_row(
    csv_row: dict[str, Any],
    *,
    document_type: str,
    statement_info: StatementFileInfo,
    source_file: str,
    run_id: int | None,
    metadata_row: dict[str, Any] | None,
) -> FactBuildResult:
    """Uma linha de ``CD_CONTA``/``VL_CONTA`` -> linha de ``financial_statement_facts``.

    Falha graciosamente (``FactBuildResult.error``) por linha -- um CNPJ mal
    formado numa linha nao pode abortar as 200 mil linhas seguintes
    (fase1.md 104).
    """
    cnpj = (csv_row.get("CNPJ_CIA") or "").strip()
    account_code = (csv_row.get("CD_CONTA") or "").strip()
    if not cnpj or not account_code:
        return FactBuildResult(None, "linha sem CNPJ_CIA ou CD_CONTA")

    reference_date = parse_date(csv_row.get("DT_REFER"))
    if reference_date is None:
        return FactBuildResult(None, f"DT_REFER invalida: {csv_row.get('DT_REFER')!r}")

    version = (csv_row.get("VERSAO") or "").strip()
    if not version:
        return FactBuildResult(None, "linha sem VERSAO")

    scale = parse_scale(csv_row.get("ESCALA_MOEDA"))
    if scale is None:
        return FactBuildResult(None, f"ESCALA_MOEDA desconhecida: {csv_row.get('ESCALA_MOEDA')!r}")

    period_start = parse_date(csv_row.get("DT_INI_EXERC"))
    period_end = parse_date(csv_row.get("DT_FIM_EXERC"))

    row_hash = compute_source_row_hash(
        cnpj=cnpj,
        document_type=document_type,
        statement_type=statement_info.statement_type,
        is_consolidated=statement_info.is_consolidated,
        reference_date=reference_date,
        version=version,
        period_start=period_start,
        period_end=period_end,
        account_code=account_code,
    )
    filing_received_at, available_from = availability_from_metadata(metadata_row)
    moeda = (csv_row.get("MOEDA") or "").strip().upper()

    return FactBuildResult(
        {
            "cvm_code": (csv_row.get("CD_CVM") or "").strip(),
            "cnpj": cnpj,
            "document_type": document_type,
            "statement_type": statement_info.statement_type,
            "reference_date": reference_date,
            "period_start": period_start,
            "period_end": period_end,
            "filing_received_at": filing_received_at,
            "available_from": available_from,
            "version": version,
            "account_code": account_code,
            "account_description": csv_row.get("DS_CONTA"),
            "value": parse_decimal(csv_row.get("VL_CONTA")),
            "currency": "BRL" if moeda == "REAL" else (moeda or "BRL"),
            "scale": scale,
            "fiscal_year_order": normalize_fiscal_year_order(csv_row.get("ORDEM_EXERC")),
            "is_consolidated": statement_info.is_consolidated,
            "source_file": source_file,
            "source_row_hash": row_hash,
            "run_id": run_id,
        }
    )


def build_document_row(
    metadata_row: dict[str, Any], *, document_type: str, source_file: str, source_url: str | None, run_id: int | None
) -> FactBuildResult:
    """Uma linha do arquivo de metadados -> linha de ``cvm_documents``."""
    cnpj = (metadata_row.get("CNPJ_CIA") or "").strip()
    reference_date = parse_date(metadata_row.get("DT_REFER"))
    version = (metadata_row.get("VERSAO") or "").strip()
    cvm_code = (metadata_row.get("CD_CVM") or "").strip()
    if not cnpj or not cvm_code or reference_date is None or not version:
        return FactBuildResult(None, "metadados incompletos: falta CNPJ/CD_CVM/DT_REFER/VERSAO")

    filing_received_at, available_from = availability_from_metadata(metadata_row)
    return FactBuildResult(
        {
            "cvm_code": cvm_code,
            "cnpj": cnpj,
            "document_type": document_type,
            "reference_date": reference_date,
            "filing_received_at": filing_received_at,
            "available_from": available_from,
            "version": version,
            "situation": None,  # a CVM nao publica esse campo neste dataset (fase1.md 123).
            "source_file": source_file,
            "source_url": source_url,
            "run_id": run_id,
        }
    )


# ---------------------------------------------------------------------------
# Trimestre isolado por subtracao (fase1.md 44) -- usado so no calculo de
# metricas (``analytics/fundamentals.py``), nunca grava linha sintetica em
# financial_statement_facts (que preserva so o que a CVM reportou).
# ---------------------------------------------------------------------------


def derive_isolated_quarter_value(
    *, statement_type: str, current_cumulative: Decimal | None, previous_cumulative: Decimal | None
) -> Decimal | None:
    """``isolado = acumulado_atual - acumulado_anterior``.

    Valido apenas para ``ADDITIVE_FLOW_STATEMENTS``. Devolve ``None`` (nunca
    inventa) quando falta um dos dois lados ou a demonstracao nao aceita essa
    logica -- quem chama decide o ``quality_flag`` apropriado.
    """
    if statement_type not in ADDITIVE_FLOW_STATEMENTS:
        return None
    if current_cumulative is None or previous_cumulative is None:
        return None
    return current_cumulative - previous_cumulative
