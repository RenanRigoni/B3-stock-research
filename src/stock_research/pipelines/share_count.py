"""Pipeline de quantidade de acoes (fase2_plan.md 3): CVM FRE -> ``cvm_documents``
(document_type='FRE') + ``share_count_history``.

Mesma disciplina de ``pipelines/fundamentals.py``: ``start_run``/``finish_run``
sempre, um ano com erro nunca aborta os outros, tudo idempotente por chave
natural. Escopo deliberado: so processa CNPJs presentes em ``public.companies``
(as 3 companhias do universo hoje) -- os ZIPs da FRE trazem ~500 empresas.

    sync_fre(year=..., from_year=...)   -- FRE de um ano ou de um intervalo
"""

from __future__ import annotations

import zipfile
from datetime import date
from typing import Any

from stock_research.config import data_dir, load_settings, project_root
from stock_research.db import (
    fetch_all,
    finish_run,
    record_finding,
    start_run,
    upsert_many,
)
from stock_research.logging import get_logger
from stock_research.sources.fundamentals import cvm_fre
from stock_research.sources.fundamentals.base import CvmSchemaError, RawDownload
from stock_research.sources.fundamentals.cvm_common import (
    iter_csv_rows,
    sniff_zip_member,
    validate_columns,
)
from stock_research.transforms.fundamentals_facts import build_document_row
from stock_research.transforms.share_count import build_share_count_rows

logger = get_logger(__name__)

PIPELINE = "share_count"
DOCUMENT_TYPE = cvm_fre.DOCUMENT_TYPE

_DOCUMENT_UPDATE_COLUMNS = [
    "cnpj",
    "company_id",
    "filing_received_at",
    "available_from",
    "situation",
    "source_file",
    "source_url",
    "run_id",
]
_SHARE_COUNT_UPDATE_COLUMNS = [
    "filing_received_at",
    "available_from",
    "shares_issued",
    "free_float_shares",
    "treasury_shares",
    "shares_outstanding",
    "source",
    "source_document_id",
    "calculation_version",
    "quality_flag",
    "quality_reason",
    "run_id",
]

_Key = tuple[str, str, str]  # (cnpj, data_referencia, versao)


def target_companies() -> dict[str, dict[str, Any]]:
    """``cnpj -> {company_id, cvm_code}`` -- as companhias ja formalizadas."""
    rows = fetch_all("select company_id, cnpj, cvm_code from public.companies")
    return {r["cnpj"]: r for r in rows}


def sync_fre(*, year: int | None = None, from_year: int | None = None) -> dict[str, Any]:
    """Backfill da FRE. ``year`` sozinho processa um ano; ``from_year`` processa
    ``from_year..ano_corrente``. Nenhum -> usa ``cvm.default_from_year``."""
    data_dir().mkdir(parents=True, exist_ok=True)
    settings = load_settings()

    if year is not None:
        years = [year]
    else:
        start_year = (
            from_year if from_year is not None else int(settings["cvm"]["default_from_year"])
        )
        years = list(range(start_year, date.today().year + 1))

    results: dict[str, Any] = {}
    for target_year in years:
        results[f"FRE_{target_year}"] = _run_ingest_one(target_year)

    failed = [k for k, r in results.items() if r.get("status") == "failed"]
    return {"results": results, "failed": failed}


def _run_ingest_one(year: int) -> dict[str, Any]:
    run_id = start_run(
        PIPELINE, provider="cvm", params={"document_type": DOCUMENT_TYPE, "year": year}
    )
    try:
        stats = ingest_fre_year(year, run_id=run_id)
        finish_run(
            run_id,
            status="success",
            records_raw=stats["documents"],
            records_inserted=stats["share_counts"],
        )
        return {"status": "success", **stats}
    except Exception as exc:
        logger.error("sync-fre falhou para %d: %s", year, exc)
        finish_run(run_id, status="failed", error_message=str(exc))
        return {"status": "failed", "error": str(exc)}


def ingest_fre_year(year: int, *, run_id: int) -> dict[str, Any]:
    targets = target_companies()
    if not targets:
        raise ValueError("nenhuma companhia em public.companies -- rode a migration da §19 antes")

    raw_dir = project_root() / "data" / "raw" / "cvm" / "fre"
    download = cvm_fre.download_year(year, raw_dir)
    _register_raw_file(download, run_id)
    source_file_rel = str(download.local_path.relative_to(project_root()))

    stats: dict[str, Any] = {"documents": 0, "share_counts": 0, "warnings": 0}
    with zipfile.ZipFile(download.local_path) as zf:
        members = set(zf.namelist())
        index_member = cvm_fre.index_member(year)
        capital_member = cvm_fre.capital_social_member(year)
        distribuicao_member = cvm_fre.distribuicao_member(year)
        for needed in (index_member, capital_member):
            if needed not in members:
                raise CvmSchemaError(f"{needed} nao encontrado em {download.local_path.name}")

        index_by_key = _load_index(zf, index_member, set(targets))
        stats["documents"] = _ingest_documents(index_by_key, targets, source_file_rel, run_id)
        doc_id_map = _load_document_id_map()

        capital_groups = _group_capital_social(zf, capital_member, set(targets))
        distribuicao_by_key = (
            _load_distribuicao(zf, distribuicao_member, set(targets))
            if distribuicao_member in members
            else {}
        )

        batch: list[dict[str, Any]] = []
        for key, capital_rows in capital_groups.items():
            cnpj, data_ref, versao = key
            company = targets[cnpj]
            result = build_share_count_rows(
                reference_date_raw=data_ref,
                version_raw=versao,
                metadata_row=index_by_key.get(key),
                capital_rows=capital_rows,
                distribuicao_row=distribuicao_by_key.get(key),
                source_file=source_file_rel,
                run_id=run_id,
            )
            for warning in result.warnings:
                stats["warnings"] += 1
                record_finding(
                    run_id,
                    PIPELINE,
                    "share_count_row",
                    "WARNING",
                    warning,
                    entity_type="company",
                    entity_id=f"{cnpj}:{data_ref}:{versao}",
                )
            document_id = doc_id_map.get((company["cvm_code"], DOCUMENT_TYPE, data_ref, versao))
            for row in result.rows:
                row["company_id"] = company["company_id"]
                row["source_document_id"] = document_id
                batch.append(row)

        if batch:
            stats["share_counts"] = upsert_many(
                "share_count_history",
                batch,
                conflict_columns=["company_id", "share_class", "reference_date", "version"],
                update_columns=_SHARE_COUNT_UPDATE_COLUMNS,
            )["total"]

    return stats


def _register_raw_file(download: RawDownload, run_id: int) -> None:
    upsert_many(
        "raw_files",
        [
            {
                "file_path": str(download.local_path.relative_to(project_root())),
                "sha256": download.sha256,
                "provider": cvm_fre.NAME,
                "source_url": download.url,
                "content_type": "application/zip",
                "bytes": download.bytes,
                "run_id": run_id,
            }
        ],
        conflict_columns=["file_path", "sha256"],
        update_columns=[],
    )


def _load_index(
    zf: zipfile.ZipFile, member: str, target_cnpjs: set[str]
) -> dict[_Key, dict[str, Any]]:
    _, _, columns = sniff_zip_member(zf, member)
    validate_columns(set(columns), cvm_fre.REQUIRED_COLUMNS_FRE_INDEX, context=member)
    index: dict[_Key, dict[str, Any]] = {}
    for row in iter_csv_rows(zf, member, full_scan_encoding=True):
        cnpj = (row.get("CNPJ_CIA") or "").strip()
        if cnpj not in target_cnpjs:
            continue
        key = (cnpj, (row.get("DT_REFER") or "").strip(), (row.get("VERSAO") or "").strip())
        index[key] = row
    return index


def _ingest_documents(
    index_by_key: dict[_Key, dict[str, Any]],
    targets: dict[str, dict[str, Any]],
    source_file: str,
    run_id: int,
) -> int:
    rows = []
    for (cnpj, _dt_refer, _versao), meta_row in index_by_key.items():
        result = build_document_row(
            meta_row,
            document_type=DOCUMENT_TYPE,
            source_file=source_file,
            source_url=meta_row.get("LINK_DOC"),
            run_id=run_id,
        )
        if result.row is None:
            record_finding(
                run_id,
                PIPELINE,
                "document_metadata",
                "WARNING",
                result.error or "erro desconhecido",
                entity_type="cvm_document",
                entity_id=f"{cnpj}:{meta_row.get('DT_REFER')}",
            )
            continue
        result.row["company_id"] = targets[cnpj]["company_id"]
        rows.append(result.row)

    if not rows:
        return 0
    return upsert_many(
        "cvm_documents",
        rows,
        conflict_columns=["cvm_code", "document_type", "reference_date", "version"],
        update_columns=_DOCUMENT_UPDATE_COLUMNS,
    )["total"]


def _load_document_id_map() -> dict[tuple[str, str, str, str], int]:
    rows = fetch_all(
        "select document_id, cvm_code, document_type, reference_date, version "
        "from public.cvm_documents where document_type = %s",
        [DOCUMENT_TYPE],
    )
    return {
        (r["cvm_code"], r["document_type"], str(r["reference_date"]), r["version"]): r[
            "document_id"
        ]
        for r in rows
    }


def _group_capital_social(
    zf: zipfile.ZipFile, member: str, target_cnpjs: set[str]
) -> dict[_Key, list[dict[str, Any]]]:
    _, _, columns = sniff_zip_member(zf, member)
    validate_columns(set(columns), cvm_fre.REQUIRED_COLUMNS_CAPITAL_SOCIAL, context=member)
    groups: dict[_Key, list[dict[str, Any]]] = {}
    for row in iter_csv_rows(zf, member, full_scan_encoding=True):
        cnpj = (row.get("CNPJ_Companhia") or "").strip()
        if cnpj not in target_cnpjs:
            continue
        key = (
            cnpj,
            (row.get("Data_Referencia") or "").strip(),
            (row.get("Versao") or "").strip(),
        )
        groups.setdefault(key, []).append(row)
    return groups


def _load_distribuicao(
    zf: zipfile.ZipFile, member: str, target_cnpjs: set[str]
) -> dict[_Key, dict[str, Any]]:
    _, _, columns = sniff_zip_member(zf, member)
    validate_columns(set(columns), cvm_fre.REQUIRED_COLUMNS_DISTRIBUICAO, context=member)
    by_key: dict[_Key, dict[str, Any]] = {}
    for row in iter_csv_rows(zf, member, full_scan_encoding=True):
        cnpj = (row.get("CNPJ_Companhia") or "").strip()
        if cnpj not in target_cnpjs:
            continue
        key = (
            cnpj,
            (row.get("Data_Referencia") or "").strip(),
            (row.get("Versao") or "").strip(),
        )
        by_key[key] = row
    return by_key
