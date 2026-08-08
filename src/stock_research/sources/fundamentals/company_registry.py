"""Cadastro de companhias abertas da CVM e resolucao ticker -> CNPJ/codigo CVM
(fase1.md 52).

Ordem de forca do identificador, sempre a mais forte disponivel:

1. CNPJ            -- se ja estiver salvo em ``company_mapping.yaml``, mesmo
                       nao confirmado, e usado para reancorar no cadastro.
2. codigo CVM       -- idem, quando nao ha CNPJ salvo.
3. razao social     -- ultimo recurso, so quando nenhum identificador forte
                       existe ainda (ex.: primeiro preenchimento do arquivo).

``resolve_mapping`` NUNCA sobrescreve uma entrada com ``confirmed: true`` --
isso e conferencia humana e so muda por edicao manual do YAML.

A CVM nao publica ISIN no cadastro de companhias abertas (``CAD/DADOS``); o
campo fica ``null`` em vez de inventado (fase1.md 123).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from rapidfuzz import fuzz, process

from stock_research.config import CONFIG_DIR, load_settings
from stock_research.logging import get_logger
from stock_research.sources.fundamentals.base import RawDownload
from stock_research.sources.fundamentals.cvm_common import (
    REQUIRED_COLUMNS_REGISTRY,
    download_raw,
    iter_csv_rows_from_path,
    sniff_text_file,
    validate_columns,
)

logger = get_logger(__name__)

REGISTRY_FILENAME = "cad_cia_aberta.csv"
# Abaixo disso, a correspondencia por razao social e descartada como ruido em
# vez de virar palpite (fase1.md 123: dado ausente > dado incorreto).
NAME_MATCH_THRESHOLD = 90.0
MAPPING_FILE = CONFIG_DIR / "company_mapping.yaml"


def registry_url() -> str:
    base = load_settings()["cvm"]["base_url"]
    return f"{base}/CAD/DADOS/{REGISTRY_FILENAME}"


def download_registry(dest_dir: Path) -> RawDownload:
    """Baixa o cadastro (CSV puro, sem ZIP -- confirmado contra a fonte real)."""
    rps = float(load_settings()["providers"]["cvm"].get("requests_per_second", 1.0))
    dest_path = dest_dir / REGISTRY_FILENAME
    return download_raw(registry_url(), dest_path, requests_per_second=rps)


def parse_registry(path: Path) -> list[dict[str, Any]]:
    """Le o cadastro e valida o schema minimo antes de devolver as linhas."""
    _, _, columns = sniff_text_file(path)
    validate_columns(set(columns), REQUIRED_COLUMNS_REGISTRY, context=str(path))
    return list(iter_csv_rows_from_path(path))


# ---------------------------------------------------------------------------
# Normalizacao e matching (funcoes puras, testaveis sem rede)
# ---------------------------------------------------------------------------


def normalize_legal_name(name: str) -> str:
    """Remove acentos, pontuacao e caixa para comparar razoes sociais que a
    CVM e o ``companies.yaml`` grafam de formas ligeiramente diferentes
    (ex.: "PETRÓLEO BRASILEIRO S.A. - PETROBRAS" x "PETROLEO BRASILEIRO S.A.
    PETROBRAS").
    """
    ascii_only = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    cleaned = re.sub(r"[^A-Za-z0-9\s]", " ", ascii_only)
    return re.sub(r"\s+", " ", cleaned).strip().upper()


def _by_cnpj(rows: list[dict[str, Any]], cnpj: str) -> dict[str, Any] | None:
    return next((r for r in rows if r.get("CNPJ_CIA") == cnpj), None)


def _by_cvm_code(rows: list[dict[str, Any]], cvm_code: str) -> dict[str, Any] | None:
    target = cvm_code.lstrip("0") or "0"
    return next((r for r in rows if (r.get("CD_CVM") or "").lstrip("0") == target), None)


def _best_name_match(legal_name: str, rows: list[dict[str, Any]]) -> tuple[dict[str, Any], float] | None:
    """Prefere emissores ATIVO; so cai para todo o cadastro se nenhum ativo
    bater bem -- companhia cancelada com nome parecido gera falso positivo
    caro (CNPJ errado gravado como se fosse confiavel)."""
    target = normalize_legal_name(legal_name)
    for candidates in (
        [r for r in rows if r.get("SIT", "").upper() == "ATIVO"],
        rows,
    ):
        choices = {i: normalize_legal_name(r["DENOM_SOCIAL"]) for i, r in enumerate(candidates)}
        if not choices:
            continue
        match = process.extractOne(target, choices, scorer=fuzz.token_sort_ratio)
        if match is not None:
            _, score, idx = match
            if score >= NAME_MATCH_THRESHOLD:
                return candidates[idx], score
    return None


# ---------------------------------------------------------------------------
# Resolucao
# ---------------------------------------------------------------------------


def resolve_mapping(
    companies: list[dict[str, Any]],
    current_mapping: dict[str, Any],
    registry_rows: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    """Devolve o novo bloco ``mappings`` de ``company_mapping.yaml`` + notas
    legiveis do que foi feito com cada ticker (para o relatorio do comando).
    """
    new_mapping: dict[str, Any] = {}
    notes: list[str] = []
    now = datetime.now(UTC).isoformat()

    for entry in companies:
        ticker = entry["ticker"].upper()
        existing = (current_mapping or {}).get(ticker) or {}

        if existing.get("confirmed"):
            new_mapping[ticker] = existing
            notes.append(f"{ticker}: confirmado manualmente, mantido sem alteracao")
            continue

        match, resolved_by, score = _resolve_one(entry, existing, registry_rows)
        if match is None:
            new_mapping[ticker] = {
                "cnpj": existing.get("cnpj"),
                "cvm_code": existing.get("cvm_code"),
                "legal_name": existing.get("legal_name"),
                "isin": existing.get("isin"),
                "resolved_by": None,
                "confirmed": False,
                "confirmed_at": None,
            }
            notes.append(f"{ticker}: nao resolvido automaticamente -- exige entrada manual")
            continue

        new_mapping[ticker] = {
            "cnpj": match["CNPJ_CIA"],
            "cvm_code": match["CD_CVM"],
            "legal_name": match["DENOM_SOCIAL"],
            "isin": existing.get("isin"),  # CVM nao publica ISIN no cadastro (fase1.md 123).
            "resolved_by": resolved_by,
            "confirmed": False,
            "confirmed_at": None,
            "_resolved_at": now,
        }
        detail = f" (score={score:.0f})" if score is not None else ""
        notes.append(f"{ticker}: resolvido por {resolved_by}{detail} -> CNPJ {match['CNPJ_CIA']}")

    return new_mapping, notes


def _resolve_one(
    entry: dict[str, Any], existing: dict[str, Any], registry_rows: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, str | None, float | None]:
    """Tenta o identificador mais forte disponivel, na ordem do fase1.md 52."""
    if existing.get("cnpj"):
        match = _by_cnpj(registry_rows, existing["cnpj"])
        if match:
            return match, "cnpj", None
    if existing.get("cvm_code"):
        match = _by_cvm_code(registry_rows, str(existing["cvm_code"]))
        if match:
            return match, "cvm_code", None

    legal_name = entry.get("legal_name") or entry.get("company_name")
    if not legal_name:
        return None, None, None
    best = _best_name_match(legal_name, registry_rows)
    if best is None:
        return None, None, None
    match, score = best
    return match, "legal_name", score


# ---------------------------------------------------------------------------
# Persistencia -- preserva os comentarios de topo do YAML versionado
# ---------------------------------------------------------------------------


def _split_header(text: str) -> tuple[str, str]:
    """Separa o bloco de comentarios (documentacao da politica de resolucao)
    do bloco de dados YAML (``version:`` em diante). Regravar so o segundo
    evita que ``yaml.safe_dump`` apague a documentacao do arquivo.
    """
    marker = "version:"
    idx = text.find(marker)
    if idx == -1:
        return text, ""
    return text[:idx], text[idx:]


def update_company_mapping_file(new_mapping: dict[str, Any]) -> Path:
    current_text = MAPPING_FILE.read_text(encoding="utf-8") if MAPPING_FILE.exists() else ""
    header, _ = _split_header(current_text)
    if not header:
        header = (
            "# Mapeamento ticker -> identificadores oficiais da CVM (fase1.md 52).\n"
            "# Preenchido por `stock-research sync-cvm --registry`. Conferencia humana\n"
            "# obrigatoria antes de confirmed: true.\n\n"
        )

    # Remove a chave interna de rastreamento (_resolved_at) antes de gravar --
    # ela existe so para o log do comando, nao faz parte do contrato do arquivo.
    clean_mapping = {
        ticker: {k: v for k, v in fields.items() if not k.startswith("_")}
        for ticker, fields in new_mapping.items()
    }
    data = {"version": "company_mapping_v1", "mappings": clean_mapping}
    body = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False)

    MAPPING_FILE.write_text(header + body, encoding="utf-8")
    logger.info("company_mapping.yaml atualizado: %d ticker(s)", len(clean_mapping))
    return MAPPING_FILE
