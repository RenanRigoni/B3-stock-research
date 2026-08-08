"""Carga do universo de instrumentos a partir de ``config/companies.yaml``.

Idempotente: rodar de novo atualiza o cadastro em vez de duplicar. Benchmarks
(``^BVSP``) entram como instrumentos comuns marcados com ``is_benchmark``, para
que um unico pipeline de precos sirva acao e indice.
"""

from __future__ import annotations

from typing import Any

from stock_research.config import load_companies
from stock_research.db import fetch_all, finish_run, start_run, upsert_many
from stock_research.logging import get_logger

logger = get_logger(__name__)

PIPELINE = "universe"


def _instrument_row(entry: dict[str, Any], *, is_benchmark: bool) -> dict[str, Any]:
    flags = entry.get("flags") or {}
    return {
        "ticker": entry["ticker"].upper(),
        "yahoo_symbol": entry.get("yahoo_symbol"),
        "company_name": entry["company_name"],
        "legal_name": entry.get("legal_name"),
        "share_class": entry.get("share_class"),
        "sector": entry.get("sector"),
        "asset_type": entry.get("asset_type", "index" if is_benchmark else "stock"),
        "exchange": entry.get("exchange", "B3"),
        "currency": entry.get("currency", "BRL"),
        "is_benchmark": is_benchmark,
        "financial_company": bool(flags.get("financial_company", False)),
        "utility": bool(flags.get("utility", False)),
        "commodity_exposed": bool(flags.get("commodity_exposed", False)),
        "holding": bool(flags.get("holding", False)),
        "notes": entry.get("notes"),
    }


def _alias_rows(entry: dict[str, Any], instrument_id: int) -> list[dict[str, Any]]:
    """Aliases de busca de noticias.

    O proprio nome e o ticker entram como alias forte automaticamente; o YAML
    so precisa listar o que foge disso.
    """
    aliases = entry.get("aliases") or {}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    # "Vale" e o proprio company_name da VALE3, mas o YAML classifica esse
    # termo isolado como fraco de proposito (casa com "vale a pena",
    # "vale-refeicao", nomes de cidade). Sem essa checagem, a promocao
    # automatica de company_name/legal_name para "forte" ignoraria a
    # classificacao explicita e reintroduziria o ruido que ela existe para
    # evitar -- o YAML e a fonte da verdade, nao a heuristica de default.
    weak_names = {str(a).strip().lower() for a in (aliases.get("weak") or [])}

    def add(alias: str, kind: str, strong: bool) -> None:
        key = alias.strip()
        if not key or key.lower() in seen:
            return
        seen.add(key.lower())
        rows.append(
            {
                "instrument_id": instrument_id,
                "alias": key,
                "alias_kind": kind,
                "is_strong": strong and key.lower() not in weak_names,
            }
        )

    add(entry["company_name"], "name", True)
    add(entry["ticker"].upper(), "ticker", True)
    if entry.get("legal_name"):
        add(entry["legal_name"], "legal_name", True)
    for alias in aliases.get("strong") or []:
        add(alias, "name", True)
    for alias in aliases.get("weak") or []:
        add(alias, "name", False)

    return rows


def load_universe_from_config() -> dict[str, int]:
    """Le ``companies.yaml`` e sincroniza ``instruments`` + ``company_aliases``."""
    config = load_companies()
    entries: list[tuple[dict[str, Any], bool]] = [
        *((e, True) for e in config.get("benchmarks") or []),
        *((e, False) for e in config.get("companies") or []),
    ]
    if not entries:
        raise ValueError("config/companies.yaml nao lista nenhum instrumento")

    run_id = start_run(PIPELINE, provider="config", params={"entries": len(entries)})
    try:
        instrument_rows = [_instrument_row(e, is_benchmark=b) for e, b in entries]
        stats = upsert_many(
            "instruments",
            instrument_rows,
            conflict_columns=["ticker", "exchange"],
            # active/valid_from ficam de fora: sao estado operacional, nao
            # devem ser resetados por uma recarga de config.
            update_columns=[
                "yahoo_symbol",
                "company_name",
                "legal_name",
                "share_class",
                "sector",
                "asset_type",
                "currency",
                "is_benchmark",
                "financial_company",
                "utility",
                "commodity_exposed",
                "holding",
                "notes",
            ],
        )

        id_by_ticker = {
            row["ticker"]: row["instrument_id"]
            for row in fetch_all("select instrument_id, ticker from public.instruments")
        }

        alias_rows: list[dict[str, Any]] = []
        for entry, _ in entries:
            instrument_id = id_by_ticker[entry["ticker"].upper()]
            alias_rows.extend(_alias_rows(entry, instrument_id))

        alias_stats = upsert_many(
            "company_aliases",
            alias_rows,
            conflict_columns=["instrument_id", "alias"],
            update_columns=["alias_kind", "is_strong"],
        )

        # O ticker atual tambem e um alias historico valido.
        upsert_many(
            "ticker_aliases",
            [
                {
                    "instrument_id": id_by_ticker[e["ticker"].upper()],
                    "ticker": e["ticker"].upper(),
                    "valid_from": None,
                    "source": "config",
                    "confidence": 1.0,
                }
                for e, _ in entries
            ],
            conflict_columns=["instrument_id", "ticker", "valid_from"],
            update_columns=[],  # ja existe: nao mexer no historico
        )

        finish_run(
            run_id,
            status="success",
            records_raw=len(entries),
            records_inserted=stats["inserted"] + alias_stats["inserted"],
            records_updated=stats["updated"] + alias_stats["updated"],
        )
        return {"instruments": stats["total"], "aliases": alias_stats["total"]}

    except Exception as exc:
        finish_run(run_id, status="failed", error_message=str(exc))
        raise
