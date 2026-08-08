"""Ingestao de um (document_type, ano) da CVM: ZIP -> raw -> staging -> curated.

Extraido de ``pipelines/fundamentals.py`` so para caber no limite de 400
linhas por arquivo -- e o mesmo pipeline logico, so a parte pesada (streaming
dos CSVs de demonstracao) mora aqui.

Escopo deliberado: so processamos linhas cujo CNPJ esta em ``instruments``
(populado por ``sync_company_registry``). Os ZIPs da CVM trazem ~500
companhias; filtrar cedo, linha a linha durante o streaming, e o que torna
viavel gravar no Supabase pelo backend REST (fase1.md 61-63 na doc de
arquitetura) sem baixar milhoes de linhas irrelevantes para o projeto.
"""

from __future__ import annotations

import zipfile
from typing import Any

from stock_research.config import project_root
from stock_research.db import fetch_all, record_finding, upsert_many
from stock_research.logging import get_logger
from stock_research.sources.fundamentals import cvm_dfp, cvm_itr
from stock_research.sources.fundamentals.base import CvmSchemaError, RawDownload
from stock_research.sources.fundamentals.cvm_common import (
    REQUIRED_COLUMNS_STATEMENT,
    StatementFileInfo,
    iter_csv_rows,
    load_metadata_index,
    parse_statement_filename,
    sniff_zip_member,
    validate_columns,
)
from stock_research.transforms.fundamentals_facts import build_document_row, build_fact_row

logger = get_logger(__name__)

PIPELINE = "fundamentals"
FACT_BATCH_SIZE = 4000

DOCUMENT_UPDATE_COLUMNS = [
    "cnpj", "instrument_id", "filing_received_at", "available_from",
    "situation", "source_file", "source_url", "run_id",
]
FACT_UPDATE_COLUMNS = [
    "cvm_code", "cnpj", "instrument_id", "document_id", "document_type", "statement_type",
    "reference_date", "period_start", "period_end", "filing_received_at", "available_from",
    "version", "account_code", "account_description", "value", "currency", "scale",
    "fiscal_year_order", "is_consolidated", "source_file", "run_id",
]


def target_instruments() -> dict[str, dict[str, Any]]:
    """``cnpj -> {instrument_id, ticker, cvm_code}`` para as empresas ja
    resolvidas em ``instruments`` (via ``sync_company_registry``)."""
    rows = fetch_all(
        "select instrument_id, ticker, cnpj, cvm_code from public.instruments where cnpj is not null"
    )
    return {r["cnpj"]: r for r in rows}


def ingest_document_type(document_type: str, year: int, *, run_id: int) -> dict[str, Any]:
    targets = target_instruments()
    if not targets:
        raise ValueError(
            "nenhum instrumento com CNPJ resolvido -- rode `sync-cvm --registry` antes de `--year`"
        )

    source = cvm_dfp if document_type == "DFP" else cvm_itr
    raw_dir = project_root() / "data" / "raw" / "cvm" / document_type.lower()
    download = source.download_year(year, raw_dir)
    _register_raw_file(download, source.NAME, run_id)
    source_file_rel = str(download.local_path.relative_to(project_root()))

    stats: dict[str, Any] = {"documents": 0, "facts": 0, "skipped_rows": 0, "skipped_files": []}
    with zipfile.ZipFile(download.local_path) as zf:
        metadata_member = f"{document_type.lower()}_cia_aberta_{year}.csv"
        if metadata_member not in zf.namelist():
            raise CvmSchemaError(f"{metadata_member} nao encontrado em {download.local_path.name}")

        metadata_index = load_metadata_index(zf, metadata_member)
        target_cnpjs = set(targets)

        stats["documents"] = _ingest_documents(
            metadata_index, target_cnpjs, targets, document_type, source_file_rel, run_id
        )
        doc_id_map = _load_document_id_map(document_type)

        for member in zf.namelist():
            info = parse_statement_filename(member)
            if info is None or info.year != year:
                continue
            try:
                file_stats = _ingest_statement_file(
                    zf, member, info, document_type, metadata_index, targets,
                    doc_id_map, source_file_rel, run_id,
                )
            except CvmSchemaError as exc:
                logger.error("cvm: schema invalido em %s: %s", member, exc)
                record_finding(
                    run_id, PIPELINE, "schema_validation", "ERROR", str(exc),
                    entity_type="cvm_file", entity_id=member,
                )
                stats["skipped_files"].append(member)
                continue
            stats["facts"] += file_stats["facts"]
            stats["skipped_rows"] += file_stats["skipped_rows"]

    return stats


def _register_raw_file(download: RawDownload, provider: str, run_id: int) -> None:
    upsert_many(
        "raw_files",
        [{
            "file_path": str(download.local_path.relative_to(project_root())),
            "sha256": download.sha256,
            "provider": provider,
            "source_url": download.url,
            "content_type": "application/zip",
            "bytes": download.bytes,
            "run_id": run_id,
        }],
        conflict_columns=["file_path", "sha256"],
        update_columns=[],
    )


def _ingest_documents(
    metadata_index: dict[tuple[str, str, str], dict[str, Any]],
    target_cnpjs: set[str],
    targets: dict[str, dict[str, Any]],
    document_type: str,
    source_file: str,
    run_id: int,
) -> int:
    rows = []
    for (cnpj, _dt_refer, _versao), meta_row in metadata_index.items():
        if cnpj not in target_cnpjs:
            continue
        result = build_document_row(
            meta_row, document_type=document_type, source_file=source_file,
            source_url=meta_row.get("LINK_DOC"), run_id=run_id,
        )
        if result.row is None:
            record_finding(
                run_id, PIPELINE, "document_metadata", "WARNING", result.error or "erro desconhecido",
                entity_type="cvm_document", entity_id=f"{cnpj}:{meta_row.get('DT_REFER')}",
            )
            continue
        result.row["instrument_id"] = targets[cnpj]["instrument_id"]
        rows.append(result.row)

    if not rows:
        return 0
    stats = upsert_many(
        "cvm_documents", rows,
        conflict_columns=["cvm_code", "document_type", "reference_date", "version"],
        update_columns=DOCUMENT_UPDATE_COLUMNS,
    )
    return stats["total"]


def _load_document_id_map(document_type: str) -> dict[tuple[str, str, str, str], int]:
    """So existem documentos das empresas-alvo no banco (escopo do pipeline),
    entao filtrar por ``document_type`` basta -- sem parametro de array, que o
    backend REST nao suporta em ``= any(...)`` (fase1.md 61)."""
    rows = fetch_all(
        "select document_id, cvm_code, document_type, reference_date, version "
        "from public.cvm_documents where document_type = %s",
        [document_type],
    )
    return {
        (r["cvm_code"], r["document_type"], str(r["reference_date"]), r["version"]): r["document_id"]
        for r in rows
    }


def _ingest_statement_file(
    zf: zipfile.ZipFile,
    member: str,
    info: StatementFileInfo,
    document_type: str,
    metadata_index: dict[tuple[str, str, str], dict[str, Any]],
    targets: dict[str, dict[str, Any]],
    doc_id_map: dict[tuple[str, str, str, str], int],
    source_file: str,
    run_id: int,
) -> dict[str, int]:
    _, _, columns = sniff_zip_member(zf, member)
    validate_columns(set(columns), REQUIRED_COLUMNS_STATEMENT, context=member)

    target_cnpjs = set(targets)
    batch: list[dict[str, Any]] = []
    total = 0
    skipped = 0

    for csv_row in iter_csv_rows(zf, member):
        cnpj = (csv_row.get("CNPJ_CIA") or "").strip()
        if cnpj not in target_cnpjs:
            continue

        meta_row = metadata_index.get((cnpj, csv_row.get("DT_REFER"), csv_row.get("VERSAO")))
        result = build_fact_row(
            csv_row, document_type=document_type, statement_info=info,
            source_file=source_file, run_id=run_id, metadata_row=meta_row,
        )
        if result.row is None:
            skipped += 1
            continue

        row = result.row
        row["instrument_id"] = targets[cnpj]["instrument_id"]
        row["document_id"] = doc_id_map.get(
            (row["cvm_code"], document_type, str(row["reference_date"]), row["version"])
        )
        batch.append(row)
        if len(batch) >= FACT_BATCH_SIZE:
            total += _flush_facts(batch)
            batch = []

    if batch:
        total += _flush_facts(batch)

    if skipped:
        record_finding(
            run_id, PIPELINE, "fact_row", "WARNING",
            f"{skipped} linha(s) descartada(s) em {member} (dado incompleto ou invalido)",
            entity_type="cvm_file", entity_id=member, details={"skipped": skipped},
        )
    return {"facts": total, "skipped_rows": skipped}


def _flush_facts(rows: list[dict[str, Any]]) -> int:
    stats = upsert_many(
        "financial_statement_facts", rows,
        conflict_columns=["source_row_hash"], update_columns=FACT_UPDATE_COLUMNS,
    )
    return stats["total"]
