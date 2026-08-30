"""Transformacoes puras: linha do cadastro CVM -> linha de ``company_lifecycle``
(+ linha de upsert de ``companies``). Fase 3 M1, Handoff v2 §4.1.

Nenhuma funcao aqui faz I/O. A logica delicada -- reconstruir o intervalo
EFETIVO (`valid_from` = DT_REG, `valid_to` = DT_CANCEL) e normalizar
`MOTIVO_CANCEL` / `SIT` / `SIT_EMISSOR` -- fica testavel sem rede/banco.

REGRA BITEMPORAL (Handoff §1-§3): `valid_from`/`valid_to` sao TEMPO EFETIVO e
sao o unico gate de elegibilidade. `source_observed_at` (TEMPO DE TRANSACAO) e a
data do snapshot do cadastro e NUNCA filtra elegibilidade. O cadastro CVM nao
informa data de disponibilizacao por linha, entao `source_available_from` fica
NULL (e valido: nao e usado como gate).

V1: **uma linha por companhia** (o cadastro e um snapshot -- so conhecemos os
pontos DT_REG e DT_CANCEL, nao o historico de transicoes). Empresa ativa =
`valid_to = NULL`. Cancelada = `valid_to = DT_CANCEL` + `event_type =
'cancellation'` + `reason`/`reason_category` de `MOTIVO_CANCEL`.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from stock_research.transforms.fundamentals_facts import parse_date

SOURCE = "cvm_cad"


def _norm(text: str | None) -> str:
    """Sem acento, caixa alta, espacos colapsados -- para casar rotulos livres."""
    if not text:
        return ""
    ascii_only = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_only.upper().split())


def registration_status_from_sit(sit: str | None) -> str:
    n = _norm(sit)
    if n == "ATIVO":
        return "registered"
    if n.startswith("SUSPENSO"):
        return "suspended"
    if n == "CANCELADA":
        return "canceled"
    # Qualquer outro estado do cadastro (raro) -> tratado como cancelado para
    # nao inflar o universo indevidamente; o rotulo bruto fica em quality_reason.
    return "canceled"


def issuer_status_from_sit_emissor(sit_emissor: str | None) -> str | None:
    n = _norm(sit_emissor)
    if not n:
        return None
    table = {
        "EM FUNCIONAMENTO NORMAL": "operational",
        "FASE PRE OPERACIONAL": "pre_operational",
        "EM RECUPERACAO JUDICIAL OU EQUIVALENTE": "judicial_recovery",
        "EM LIQUIDACAO": "liquidation",
        "EM LIQUIDACAO EXTRAJUDICIAL": "liquidation",
        "LIQUIDACAO EXTRAJUDICIAL": "liquidation",
        "PARALISADA": "paused",
        "FALIDA": "bankrupt",
    }
    if n in table:
        return table[n]
    # Slug conservador do rotulo bruto -- nunca perde a informacao.
    return n.lower().replace(" ", "_")


# ``MOTIVO_CANCEL`` e texto livre da CVM. Ordem importa: incorporacao antes de
# "instrucao" (varias incorporacoes citam a instrucao CVM no texto).
def reason_category(motivo_cancel: str | None) -> str | None:
    n = _norm(motivo_cancel)
    if not n:
        return None
    if "INCORPORA" in n:
        return "incorporation"
    if "FALENCIA" in n or "LIQUIDAC" in n or "LIQUIDACAO" in n:
        return "bankruptcy_liquidation"
    if "VOLUNTAR" in n or "361" in n or "OPA" in n or "FECHAMENTO DE CAPITAL" in n:
        return "voluntary_delisting"
    if "INSTR" in n or "NORMAS" in n or "OFICIO" in n or "CVM" in n:
        return "regulatory"
    return "other"


@dataclass(frozen=True)
class CompanyLifecycleBuild:
    company_upsert: dict[str, Any] | None
    lifecycle_row: dict[str, Any] | None
    warnings: tuple[str, ...] = ()


def build_company_lifecycle(
    cad_row: dict[str, Any],
    *,
    source_observed_at: datetime,
    run_id: int | None,
) -> CompanyLifecycleBuild:
    """Uma linha de ``cad_cia_aberta.csv`` -> (upsert de ``companies``, linha de
    ``company_lifecycle``). Falha graciosamente por linha (``warnings``).
    """
    cnpj = (cad_row.get("CNPJ_CIA") or "").strip()
    legal_name = (cad_row.get("DENOM_SOCIAL") or "").strip()
    if not cnpj or not legal_name:
        return CompanyLifecycleBuild(None, None, ("linha sem CNPJ_CIA ou DENOM_SOCIAL",))

    cvm_code = (cad_row.get("CD_CVM") or "").strip() or None
    sector = (cad_row.get("SETOR_ATIV") or "").strip() or None

    dt_reg = parse_date(cad_row.get("DT_REG"))
    dt_const = parse_date(cad_row.get("DT_CONST"))
    dt_cancel = parse_date(cad_row.get("DT_CANCEL"))

    warnings: list[str] = []
    quality_flag = "ok"
    quality_bits: list[str] = []

    valid_from = dt_reg
    if valid_from is None:
        valid_from = dt_const
        if valid_from is not None:
            quality_flag = "estimated"
            quality_bits.append("valid_from de DT_CONST (DT_REG ausente)")
    if valid_from is None:
        return CompanyLifecycleBuild(
            None, None, (f"{cnpj}: sem DT_REG nem DT_CONST -- nao entra no universo",)
        )

    reg_status = registration_status_from_sit(cad_row.get("SIT"))
    issuer_status = issuer_status_from_sit_emissor(cad_row.get("SIT_EMISSOR"))

    is_cancellation = reg_status == "canceled"
    if is_cancellation and dt_cancel is None:
        # Cancelada sem data -> nao da para saber quando saiu do universo.
        # Conservador: mantem valid_to NULL (some so na camada investivel) e
        # marca incomplete -- nunca inventa a data.
        quality_flag = "incomplete"
        quality_bits.append("SIT=CANCELADA sem DT_CANCEL -- valid_to fica NULL")

    valid_to = dt_cancel if is_cancellation else None
    if valid_to is not None and valid_to < valid_from:
        warnings.append(f"{cnpj}: DT_CANCEL {valid_to} < DT_REG {valid_from}")
        quality_flag = "inconsistent"
        quality_bits.append("DT_CANCEL anterior a DT_REG")
        valid_to = valid_from

    event_type = "cancellation" if is_cancellation else "registration"
    event_date = dt_cancel if is_cancellation else valid_from
    motivo = (cad_row.get("MOTIVO_CANCEL") or "").strip() or None

    company_upsert = {
        "cnpj": cnpj,
        "cvm_code": cvm_code,
        "legal_name": legal_name,
        "sector": sector,
    }
    lifecycle_row = {
        "cnpj": cnpj,  # o pipeline troca por company_id apos o upsert
        "valid_from": valid_from,
        "valid_to": valid_to,
        "event_date": event_date,
        "cvm_registration_date": dt_reg,
        "cvm_cancel_date": dt_cancel,
        "registration_status": reg_status,
        "issuer_status": issuer_status,
        "event_type": event_type,
        "reason": motivo if is_cancellation else None,
        "reason_category": reason_category(motivo) if is_cancellation else None,
        "successor_company_id": None,
        "predecessor_company_id": None,
        "source": SOURCE,
        "source_document_ref": None,
        "source_available_from": None,  # cadastro nao informa; NAO e gate
        "source_observed_at": source_observed_at,
        "run_id": run_id,
        "quality_flag": quality_flag,
        "quality_reason": "; ".join(quality_bits) or None,
    }
    return CompanyLifecycleBuild(company_upsert, lifecycle_row, tuple(warnings))


def eligibility_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Chave natural sem ``company_id`` (so o pipeline conhece)."""
    return (row["event_type"], row["valid_from"], row["source"])


def company_eligible_at(row: dict[str, Any], as_of: date) -> bool:
    """Predicado do Handoff §6 -- TEMPO EFETIVO apenas.

    NENHUMA referencia a source_available_from / source_observed_at /
    ingested_at.
    """
    vf = row.get("valid_from")
    if vf is None or vf > as_of:
        return False
    vt = row.get("valid_to")
    return vt is None or vt >= as_of
