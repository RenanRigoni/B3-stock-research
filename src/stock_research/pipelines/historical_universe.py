"""Pipeline do universo historico (Fase 3 M1, Handoff v2 §14 passos 11-13).

    cad_cia_aberta.csv          -> public.companies (+)  +  public.company_lifecycle
    fca_..._valor_mobiliario_*  -> public.instrument_lifecycle
    seed manual (VALE5, ...)    -> public.instruments (active=false) + ticker_aliases

Mesma disciplina dos outros pipelines: `start_run`/`finish_run` sempre, um ano
com erro nao aborta os outros, idempotente. Idempotencia:

* `company_lifecycle`: upsert pela chave natural.
* `instrument_lifecycle` (fonte `cvm_fca`): delete-por-fonte + insert -- e um
  rebuild de um conjunto fixo de arquivos anuais, nao incremental; e o `ticker`
  NULL (2010-2017) nao colapsa em UNIQUE. As linhas `seed_manual` nao sao
  tocadas.

REGRA BITEMPORAL: nada aqui filtra por `source_available_from`/`source_observed_at`.
Essas colunas so guardam proveniencia.
"""

from __future__ import annotations

import zipfile
from datetime import UTC, date, datetime
from typing import Any

from stock_research.db import (
    execute,
    fetch_all,
    finish_run,
    insert_many,
    record_finding,
    start_run,
    upsert_many,
)
from stock_research.logging import get_logger
from stock_research.sources.fundamentals import cvm_fca
from stock_research.sources.fundamentals.company_registry import download_registry, parse_registry
from stock_research.sources.fundamentals.cvm_common import (
    iter_csv_rows,
    sniff_zip_member,
    validate_columns,
)
from stock_research.transforms.company_lifecycle import build_company_lifecycle
from stock_research.transforms.fundamentals_facts import parse_date
from stock_research.transforms.instrument_lifecycle import (
    build_instrument_candidate,
    merge_instrument_intervals,
)

logger = get_logger(__name__)

PIPELINE = "historical_universe"
_DIVERGENCE_DAYS = 180

_CL_UPDATE = [
    "valid_to",
    "event_date",
    "cvm_registration_date",
    "cvm_cancel_date",
    "registration_status",
    "issuer_status",
    "reason",
    "reason_category",
    "successor_company_id",
    "predecessor_company_id",
    "source_document_ref",
    "source_available_from",
    "source_observed_at",
    "run_id",
    "quality_flag",
    "quality_reason",
]


# ---------------------------------------------------------------------------
# Company lifecycle
# ---------------------------------------------------------------------------


def sync_company_lifecycle(*, refresh: bool = True) -> dict[str, Any]:
    run_id = start_run(PIPELINE, provider="cvm", params={"stage": "company_lifecycle"})
    try:
        stats = _ingest_company_lifecycle(run_id=run_id, refresh=refresh)
        finish_run(run_id, status="success", records_inserted=stats["lifecycle_rows"])
        return {"status": "success", **stats}
    except Exception as exc:
        logger.error("sync_company_lifecycle falhou: %s", exc)
        finish_run(run_id, status="failed", error_message=str(exc))
        raise


def _ingest_company_lifecycle(*, run_id: int, refresh: bool) -> dict[str, Any]:
    from stock_research.config import project_root

    raw_dir = project_root() / "data" / "raw" / "cvm" / "registry"
    if refresh:
        download_registry(raw_dir)
    path = raw_dir / "cad_cia_aberta.csv"
    rows = parse_registry(path)
    observed_at = datetime.now(UTC)

    # Um CNPJ pode ter MAIS DE UMA linha no cadastro (re-registro apos
    # cancelamento, registros historicos distintos). Uma linha de `companies`
    # por CNPJ (preferindo a mais "viva" -- ATIVO > SUSPENSO > CANCELADA);
    # TODAS as linhas de lifecycle sao mantidas (intervalos distintos), a
    # chave natural (company_id, event_type, valid_from, source) desduplica o
    # que de fato coincide.
    _RANK = {"registered": 0, "suspended": 1, "canceled": 2}
    company_by_cnpj: dict[str, dict[str, Any]] = {}
    company_rank: dict[str, int] = {}
    lifecycle_candidates: list[dict[str, Any]] = []
    warnings = 0
    for cad_row in rows:
        built = build_company_lifecycle(cad_row, source_observed_at=observed_at, run_id=run_id)
        for w in built.warnings:
            warnings += 1
            if warnings <= 50:
                record_finding(
                    run_id, PIPELINE, "company_lifecycle_row", "WARNING", w, entity_type="company"
                )
        if built.company_upsert is None or built.lifecycle_row is None:
            continue
        cnpj = built.company_upsert["cnpj"]
        rank = _RANK.get(built.lifecycle_row["registration_status"], 3)
        if cnpj not in company_by_cnpj or rank < company_rank[cnpj]:
            company_by_cnpj[cnpj] = built.company_upsert
            company_rank[cnpj] = rank
        lifecycle_candidates.append(built.lifecycle_row)

    # companies: insere as que faltam, nunca modifica as existentes (as 3 curadas).
    upsert_many(
        "companies",
        list(company_by_cnpj.values()),
        conflict_columns=["cnpj"],
        update_columns=[],
    )
    cnpj_to_id = {
        r["cnpj"]: r["company_id"]
        for r in fetch_all("select company_id, cnpj from public.companies")
    }

    batch: list[dict[str, Any]] = []
    seen_keys: set[tuple[Any, ...]] = set()
    for row in lifecycle_candidates:
        cid = cnpj_to_id.get(row["cnpj"])
        if cid is None:
            continue
        row = {k: v for k, v in row.items() if k != "cnpj"}
        row["company_id"] = cid
        key = (cid, row["event_type"], row["valid_from"], row["source"])
        if key in seen_keys:  # dois registros identicos do mesmo CNPJ
            continue
        seen_keys.add(key)
        batch.append(row)

    inserted = upsert_many(
        "company_lifecycle",
        batch,
        conflict_columns=["company_id", "event_type", "valid_from", "source"],
        update_columns=_CL_UPDATE,
    )["total"]

    return {
        "companies": len(company_by_cnpj),
        "lifecycle_rows": inserted,
        "warnings": warnings,
        "multi_record_cnpjs": len(lifecycle_candidates) - len(company_by_cnpj),
        "by_status": _count_by(batch, "registration_status"),
        "by_reason": _count_by([r for r in batch if r.get("reason_category")], "reason_category"),
    }


# ---------------------------------------------------------------------------
# Instrument lifecycle (FCA)
# ---------------------------------------------------------------------------


def sync_instrument_lifecycle(
    *, from_year: int | None = None, to_year: int | None = None
) -> dict[str, Any]:
    start_year = from_year or cvm_fca.FIRST_YEAR
    end_year = to_year or date.today().year
    run_id = start_run(
        PIPELINE,
        provider="cvm",
        params={"stage": "instrument_lifecycle", "from": start_year, "to": end_year},
    )
    try:
        stats = _ingest_instrument_lifecycle(start_year, end_year, run_id=run_id)
        finish_run(run_id, status="success", records_inserted=stats["lifecycle_rows"])
        return {"status": "success", **stats}
    except Exception as exc:
        logger.error("sync_instrument_lifecycle falhou: %s", exc)
        finish_run(run_id, status="failed", error_message=str(exc))
        raise


def _ingest_instrument_lifecycle(
    start_year: int, end_year: int, *, run_id: int
) -> dict[str, Any]:
    from stock_research.config import project_root

    raw_dir = project_root() / "data" / "raw" / "cvm" / "fca"
    raw_dir.mkdir(parents=True, exist_ok=True)

    candidates: list[dict[str, Any]] = []
    years_done: list[int] = []
    for year in range(start_year, end_year + 1):
        try:
            candidates.extend(_read_fca_year(year, raw_dir, run_id))
            years_done.append(year)
        except Exception as exc:
            logger.warning("FCA %d ignorado: %s", year, exc)
            record_finding(
                run_id, PIPELINE, "fca_year", "WARNING", f"{year}: {exc}", entity_type="fca"
            )
    if not years_done:
        raise RuntimeError("nenhum ano de FCA pode ser lido")
    latest_year = max(years_done)

    merged = merge_instrument_intervals(candidates)
    cnpj_to_id = {r["cnpj"]: r["company_id"] for r in fetch_all("select company_id, cnpj from public.companies")}
    ticker_to_instrument = {
        r["ticker"]: r["instrument_id"] for r in fetch_all("select instrument_id, ticker from public.instruments")
    }
    company_cancel: dict[int, date | None] = {}
    company_start: dict[int, date | None] = {}
    for r in fetch_all(
        "select company_id, valid_from, cvm_cancel_date from public.company_lifecycle "
        "where source = 'cvm_cad'"
    ):
        cancel = parse_date(str(r["cvm_cancel_date"])[:10]) if r["cvm_cancel_date"] else None
        start = parse_date(str(r["valid_from"])[:10]) if r["valid_from"] else None
        if cancel is not None:
            company_cancel[r["company_id"]] = cancel
        prev = company_start.get(r["company_id"])
        if start is not None and (prev is None or start < prev):
            company_start[r["company_id"]] = start

    rows: list[dict[str, Any]] = []
    not_eligible_data = 0
    fallback_start = 0
    divergences = 0
    derived_ends = 0
    for m in merged:
        cid = cnpj_to_id.get(m["cnpj"])
        if cid is None:
            continue
        m = {k: v for k, v in m.items() if k != "cnpj"}
        m["company_id"] = cid
        m["instrument_id"] = ticker_to_instrument.get(m["ticker"]) if m["ticker"] else None

        if m["valid_from"] is None:
            # Handoff §5.1: NULL em data efetiva nunca cai no filtro em silencio.
            # (a) fallback documentado = company.valid_from, quality_flag=estimated;
            # (b) se nem isso -> NOT_ELIGIBLE_DATA, contabilizado, nunca inserido.
            fb = company_start.get(cid)
            if fb is not None:
                m["valid_from"] = fb
                m["listing_start"] = m["listing_start"] or fb
                m["quality_flag"] = "estimated"
                m["quality_reason"] = _add_reason(
                    m.get("quality_reason"),
                    "valid_from ausente na FCA; fallback = company_lifecycle.valid_from",
                )
                fallback_start += 1
            else:
                not_eligible_data += 1
                record_finding(
                    run_id,
                    PIPELINE,
                    "not_eligible_data",
                    "WARNING",
                    f"company_id={cid} classe={m['share_class']} sem data efetiva "
                    "e sem fallback -- NOT_ELIGIBLE_DATA",
                    entity_type="instrument_lifecycle",
                    entity_id=str(cid),
                )
                continue
        if m["listing_start"] is None:
            m["listing_start"] = m["valid_from"]

        # Handoff §5.2: instrumento que sumiu do FCA sem Data_Fim_Negociacao ->
        # listing_end derivado da ultima referencia observada.
        if m["valid_to"] is None and m["source_reference_year"] < latest_year:
            end = date(m["source_reference_year"], 12, 31)
            m["valid_to"] = end
            m["listing_end"] = m["listing_end"] or end
            m["trading_status"] = "delisted"
            m["quality_flag"] = "estimated"
            m["quality_reason"] = _add_reason(
                m.get("quality_reason"),
                f"ausente do FCA apos {m['source_reference_year']}; listing_end derivado",
            )
            derived_ends += 1

        # Teto por cancelamento da companhia (Handoff §5.2).
        cancel = company_cancel.get(cid)
        if m["valid_to"] is None and cancel is not None:
            m["valid_to"] = cancel
            m["listing_end"] = m["listing_end"] or cancel
            m["trading_status"] = "delisted"
            m["quality_flag"] = "estimated"
            m["quality_reason"] = _add_reason(
                m.get("quality_reason"), "listing_end = data de cancelamento da companhia"
            )

        # Handoff §5.4: divergencia cadastro x FCA -> registra, mantem a data do instrumento.
        if (
            m["valid_to"] is not None
            and cancel is not None
            and abs((m["valid_to"] - cancel).days) > _DIVERGENCE_DAYS
        ):
            divergences += 1
            record_finding(
                run_id,
                PIPELINE,
                "source_disagreement",
                "WARNING",
                f"company_id={cid} fim FCA {m['valid_to']} vs cancelamento CVM {cancel}",
                entity_type="instrument_lifecycle",
                entity_id=str(cid),
            )

        # Um `valid_to`/`listing_end` DERIVADO pode cair antes do inicio quando a
        # companhia foi cancelada logo apos abrir o registro do instrumento, ou
        # quando o fallback de inicio veio depois do fim. Colapsa para um
        # intervalo degenerado (fim = inicio) e marca -- nunca grava um intervalo
        # invertido nem descarta em silencio.
        if m["valid_to"] is not None and m["valid_to"] < m["valid_from"]:
            m["valid_to"] = m["valid_from"]
            m["quality_flag"] = "inconsistent"
            m["quality_reason"] = _add_reason(
                m.get("quality_reason"), "valid_to derivado < valid_from; colapsado"
            )
        if (
            m["listing_end"] is not None
            and m["listing_start"] is not None
            and m["listing_end"] < m["listing_start"]
        ):
            m["listing_end"] = m["listing_start"]
            m["quality_flag"] = "inconsistent"
            m["quality_reason"] = _add_reason(
                m.get("quality_reason"), "listing_end derivado < listing_start; colapsado"
            )

        # `missing_input` so faz sentido quando a data efetiva ficou de fato
        # ausente. Se ela foi resolvida (algum ano da FCA trouxe, ou fallback),
        # o pior flag correto e `estimated`, nao `missing_input`.
        if m["valid_from"] is not None and m["quality_flag"] == "missing_input":
            m["quality_flag"] = "estimated"

        rows.append(m)

    execute("delete from public.instrument_lifecycle where source = 'cvm_fca'")
    inserted = insert_many("instrument_lifecycle", rows) if rows else 0

    return {
        "years": years_done,
        "candidates": len(candidates),
        "merged_intervals": len(merged),
        "lifecycle_rows": inserted,
        "not_eligible_data": not_eligible_data,
        "fallback_valid_from": fallback_start,
        "derived_listing_end": derived_ends,
        "source_disagreements": divergences,
        "with_ticker": sum(1 for r in rows if r["ticker"]),
        "without_ticker": sum(1 for r in rows if not r["ticker"]),
    }


def _read_fca_year(year: int, raw_dir: Any, run_id: int) -> list[dict[str, Any]]:
    download = cvm_fca.download_year(year, raw_dir)
    index_member = cvm_fca.index_member(year)
    vm_member = cvm_fca.valor_mobiliario_member(year)
    with zipfile.ZipFile(download.local_path) as zf:
        members = set(zf.namelist())
        for needed in (index_member, vm_member):
            if needed not in members:
                raise FileNotFoundError(f"{needed} ausente em {download.local_path.name}")

        _, _, idx_cols = sniff_zip_member(zf, index_member)
        validate_columns(set(idx_cols), cvm_fca.REQUIRED_COLUMNS_FCA_INDEX, context=index_member)
        recv_by_key: dict[tuple[str, str, str], str] = {}
        for r in iter_csv_rows(zf, index_member, full_scan_encoding=True):
            key = (
                (r.get("CNPJ_CIA") or "").strip(),
                (r.get("DT_REFER") or "").strip(),
                (r.get("VERSAO") or "").strip(),
            )
            recv_by_key[key] = (r.get("DT_RECEB") or "").strip()

        _, _, vm_cols = sniff_zip_member(zf, vm_member)
        validate_columns(
            set(vm_cols), cvm_fca.REQUIRED_COLUMNS_VALOR_MOBILIARIO, context=vm_member
        )
        observed_at = datetime(year, 12, 31, 23, 59, 59, tzinfo=UTC)
        out: list[dict[str, Any]] = []
        for vm in iter_csv_rows(zf, vm_member, full_scan_encoding=True):
            key = (
                (vm.get("CNPJ_Companhia") or "").strip(),
                (vm.get("Data_Referencia") or "").strip(),
                (vm.get("Versao") or "").strip(),
            )
            recv = parse_date(recv_by_key.get(key))
            avail = (
                datetime(recv.year, recv.month, recv.day, 23, 59, 59, tzinfo=UTC)
                if recv is not None
                else None
            )
            cand = build_instrument_candidate(
                vm,
                reference_year=year,
                source_available_from=avail,
                source_observed_at=observed_at,
                run_id=run_id,
            )
            for w in cand.warnings:
                record_finding(
                    run_id, PIPELINE, "fca_row", "WARNING", w, entity_type="fca", entity_id=str(year)
                )
            if cand.row is not None:
                out.append(cand.row)
    return out


# ---------------------------------------------------------------------------
# Seed manual (VALE5 e outras classes historicas que a FCA nao lista)
# ---------------------------------------------------------------------------

_SEED_INSTRUMENTS: list[dict[str, Any]] = [
    {
        "cnpj": "33.592.510/0001-54",
        "ticker": "VALE5",
        "share_class": "PNA",
        "listing_start": date(2000, 1, 1),
        "valid_from": date(2000, 1, 1),
        "valid_to": date(2017, 12, 22),
        "listing_end": date(2017, 12, 22),
        "reason": "conversao/unificacao de classes da Vale em 2017-12-22 "
        "(PN 2.108.579.618 acoes -> 12; ver share_count_history)",
    }
]


def seed_manual_instruments() -> dict[str, Any]:
    run_id = start_run(PIPELINE, provider="seed", params={"stage": "seed_manual"})
    try:
        cnpj_to_id = {
            r["cnpj"]: r["company_id"]
            for r in fetch_all("select company_id, cnpj from public.companies")
        }
        instr_by_ticker = {
            r["ticker"]: r for r in fetch_all("select instrument_id, ticker, active from public.instruments")
        }
        lifecycle_rows: list[dict[str, Any]] = []
        alias_rows: list[dict[str, Any]] = []
        new_instruments = 0

        for spec in _SEED_INSTRUMENTS:
            cid = cnpj_to_id.get(spec["cnpj"])
            if cid is None:
                continue
            existing = instr_by_ticker.get(spec["ticker"])
            if existing is None:
                created = upsert_many(
                    "instruments",
                    [
                        {
                            "ticker": spec["ticker"],
                            "exchange": "B3",
                            "company_name": "VALE S.A.",
                            "cnpj": spec["cnpj"],
                            "share_class": spec["share_class"],
                            "asset_type": "stock",
                            "active": False,
                            "company_id": cid,
                            "valid_from": spec["valid_from"],
                            "valid_to": spec["valid_to"],
                            "notes": "classe historica -- sem preco ingerido; universo estrutural",
                        }
                    ],
                    conflict_columns=["ticker", "exchange"],
                    update_columns=[],
                )
                new_instruments += created["inserted"]
                instr_by_ticker = {
                    r["ticker"]: r
                    for r in fetch_all("select instrument_id, ticker, active from public.instruments")
                }
                existing = instr_by_ticker.get(spec["ticker"])

            iid = existing["instrument_id"] if existing else None
            lifecycle_rows.append(
                {
                    "company_id": cid,
                    "instrument_id": iid,
                    "valid_from": spec["valid_from"],
                    "valid_to": spec["valid_to"],
                    "listing_start": spec["listing_start"],
                    "listing_end": spec["listing_end"],
                    "ticker": spec["ticker"],
                    "share_class": spec["share_class"],
                    "market": "bolsa",
                    "listing_venue": "B3",
                    "segment": None,
                    "trading_status": "delisted",
                    "source": "seed_manual",
                    "source_reference_year": spec["valid_to"].year,
                    "source_available_from": None,
                    "source_observed_at": datetime.now(UTC),
                    "run_id": run_id,
                    "quality_flag": "estimated",
                    "quality_reason": spec["reason"],
                }
            )
            if iid is not None:
                alias_rows.append(
                    {
                        "instrument_id": iid,
                        "ticker": spec["ticker"],
                        "valid_from": spec["valid_from"],
                        "valid_to": spec["valid_to"],
                        "source": "seed_manual",
                        "confidence": 0.9,
                    }
                )

        execute("delete from public.instrument_lifecycle where source = 'seed_manual'")
        inserted = insert_many("instrument_lifecycle", lifecycle_rows) if lifecycle_rows else 0
        aliases = (
            upsert_many(
                "ticker_aliases",
                alias_rows,
                conflict_columns=["instrument_id", "ticker", "valid_from"],
                update_columns=["valid_to", "source", "confidence"],
            )["total"]
            if alias_rows
            else 0
        )
        finish_run(run_id, status="success", records_inserted=inserted)
        return {
            "status": "success",
            "new_instruments": new_instruments,
            "lifecycle_rows": inserted,
            "aliases": aliases,
        }
    except Exception as exc:
        finish_run(run_id, status="failed", error_message=str(exc))
        raise


# ---------------------------------------------------------------------------


def sync_all(*, from_year: int | None = None, to_year: int | None = None) -> dict[str, Any]:
    """M1 completo: company lifecycle -> instrument lifecycle -> seed."""
    company = sync_company_lifecycle()
    instrument = sync_instrument_lifecycle(from_year=from_year, to_year=to_year)
    seed = seed_manual_instruments()
    return {"company": company, "instrument": instrument, "seed": seed}


def _count_by(rows: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        out[r[key]] = out.get(r[key], 0) + 1
    return out


def _add_reason(existing: str | None, extra: str) -> str:
    return f"{existing}; {extra}" if existing else extra


__all__ = [
    "seed_manual_instruments",
    "sync_all",
    "sync_company_lifecycle",
    "sync_instrument_lifecycle",
]
