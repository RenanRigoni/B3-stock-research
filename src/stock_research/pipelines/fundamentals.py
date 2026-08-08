"""Pipeline de fundamentos CVM (fase1.md 42-52): cadastro + DFP + ITR.

Duas entradas, mesma disciplina de ``pipelines/prices.py``: ``start_run`` /
``finish_run`` sempre, erro em um ano/documento nunca aborta os outros
(fase1.md 104), tudo idempotente por chave natural.

    sync_company_registry()              -- cadastro CVM -> company_mapping.yaml + instruments
    sync_cvm(year=..., from_year=...)    -- DFP/ITR de um ano ou de um intervalo
"""

from __future__ import annotations

from datetime import date
from typing import Any

from stock_research.config import (
    data_dir,
    load_companies,
    load_company_mapping,
    load_settings,
    project_root,
)
from stock_research.db import execute, fetch_one, finish_run, start_run, upsert_many
from stock_research.logging import get_logger
from stock_research.pipelines.fundamentals_ingest import PIPELINE, ingest_document_type
from stock_research.sources.fundamentals import company_registry

logger = get_logger(__name__)

DOCUMENT_TYPES = ("DFP", "ITR")


# ---------------------------------------------------------------------------
# Cadastro (fase1.md 52)
# ---------------------------------------------------------------------------


def sync_company_registry() -> dict[str, Any]:
    """Baixa o cadastro oficial, resolve ticker -> CNPJ/codigo CVM e grava:

    * ``config/company_mapping.yaml``  -- sugestao versionada, ``confirmed: false``;
    * ``instruments.cnpj``/``cvm_code`` -- necessario para o restante do
      pipeline filtrar as linhas certas nos ZIPs de DFP/ITR (fase1.md 61-63).

    Nunca sobrescreve uma entrada com ``confirmed: true`` (conferencia humana).
    """
    run_id = start_run(PIPELINE, provider="cvm_registry")
    try:
        raw_dir = project_root() / "data" / "raw" / "cvm" / "registry"
        download = company_registry.download_registry(raw_dir)
        upsert_many(
            "raw_files",
            [{
                "file_path": str(download.local_path.relative_to(project_root())),
                "sha256": download.sha256,
                "provider": "cvm_registry",
                "source_url": download.url,
                "content_type": "text/csv",
                "bytes": download.bytes,
                "run_id": run_id,
            }],
            conflict_columns=["file_path", "sha256"],
            update_columns=[],
        )
        registry_rows = company_registry.parse_registry(download.local_path)

        companies_cfg = load_companies()
        companies = companies_cfg.get("companies") or []
        try:
            current_mapping = (load_company_mapping().get("mappings")) or {}
        except Exception:  # arquivo pode nao existir ainda na primeira execucao
            current_mapping = {}

        new_mapping, notes = company_registry.resolve_mapping(companies, current_mapping, registry_rows)
        company_registry.update_company_mapping_file(new_mapping)
        load_company_mapping.cache_clear()

        instrument_stats = _sync_instrument_identifiers(new_mapping)

        for note in notes:
            logger.info("sync-cvm --registry: %s", note)

        finish_run(
            run_id, status="success", records_raw=len(registry_rows),
            records_updated=instrument_stats["updated"],
        )
        return {
            "registry_rows": len(registry_rows),
            "resolved": sum(1 for m in new_mapping.values() if m.get("resolved_by")),
            "unresolved": sum(1 for m in new_mapping.values() if not m.get("resolved_by")),
            "notes": notes,
        }
    except Exception as exc:
        finish_run(run_id, status="failed", error_message=str(exc))
        raise


def _sync_instrument_identifiers(mapping: dict[str, Any]) -> dict[str, int]:
    """Propaga CNPJ/codigo CVM resolvidos para ``instruments`` -- e o que
    permite ``fundamentals_ingest`` filtrar os CSVs da CVM pelas empresas do
    universo em vez de carregar ~500 companhias irrelevantes.

    UPDATE puro, nunca upsert: o instrumento sempre ja existe (criado por
    ``pipelines/universe.py`` a partir de ``companies.yaml``). Um upsert com
    payload parcial (sem ``company_name``) quebraria mesmo em conflito real --
    o Postgres valida NOT NULL na tupla do INSERT tentado *antes* de decidir
    que vai cair no ``DO UPDATE``, entao colunas obrigatorias ausentes do
    payload derrubam a chamada mesmo quando a linha ja existe.
    """
    targets = {
        ticker: fields
        for ticker, fields in mapping.items()
        if fields.get("cnpj") and fields.get("cvm_code")
    }
    if not targets:
        return {"updated": 0, "missing": 0}

    updated = 0
    missing: list[str] = []
    for ticker, fields in targets.items():
        found = fetch_one("select instrument_id from public.instruments where ticker = %s and exchange = 'B3'", [ticker])
        if found is None:
            missing.append(ticker)
            continue
        execute(
            "update public.instruments set cnpj = %s, cvm_code = %s where instrument_id = %s",
            [fields["cnpj"], fields["cvm_code"], found["instrument_id"]],
        )
        updated += 1

    if missing:
        logger.warning(
            "sync-cvm --registry: %d ticker(s) resolvido(s) mas ausente(s) em instruments: %s "
            "(rode `stock-research init` antes)", len(missing), missing,
        )
    return {"updated": updated, "missing": len(missing)}


# ---------------------------------------------------------------------------
# DFP / ITR (fase1.md 43, 44)
# ---------------------------------------------------------------------------


def sync_cvm(*, year: int | None = None, from_year: int | None = None) -> dict[str, Any]:
    """Backfill de DFP/ITR. ``year`` sozinho processa um ano; ``from_year``
    processa ``from_year..ano_corrente``. Nenhum dos dois -> usa
    ``cvm.default_from_year`` de ``settings.yaml``."""
    data_dir().mkdir(parents=True, exist_ok=True)
    settings = load_settings()
    doc_types = settings["cvm"]["documents"]

    if year is not None:
        years = [year]
    else:
        start_year = from_year if from_year is not None else int(settings["cvm"]["default_from_year"])
        years = list(range(start_year, date.today().year + 1))

    results: dict[str, Any] = {}
    for target_year in years:
        for doc_type in doc_types:
            key = f"{doc_type}_{target_year}"
            results[key] = _run_ingest_one(doc_type, target_year)

    failed = [k for k, r in results.items() if r.get("status") == "failed"]
    return {"results": results, "failed": failed}


def _run_ingest_one(document_type: str, year: int) -> dict[str, Any]:
    run_id = start_run(PIPELINE, provider="cvm", params={"document_type": document_type, "year": year})
    try:
        stats = ingest_document_type(document_type, year, run_id=run_id)
        finish_run(
            run_id, status="success",
            records_raw=stats["documents"], records_inserted=stats["facts"],
            records_rejected=stats["skipped_rows"],
        )
        return {"status": "success", **stats}
    except Exception as exc:
        logger.error("sync-cvm falhou para %s/%d: %s", document_type, year, exc)
        finish_run(run_id, status="failed", error_message=str(exc))
        return {"status": "failed", "error": str(exc)}
