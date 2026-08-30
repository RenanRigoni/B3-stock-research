"""Transformacoes puras: linha da FCA ``valor_mobiliario`` -> candidato a linha
de ``instrument_lifecycle``. Fase 3 M1, Handoff v2 §4.2 / §8.5.

Nenhuma funcao aqui faz I/O.

Cada linha da FCA e de um ANO de referencia. O pipeline (``historical_universe``)
funde os candidatos de anos consecutivos da mesma ``(companhia, classe, ticker)``
num intervalo unico -- ver ``merge_instrument_intervals``.

REGRA BITEMPORAL: ``valid_from``/``valid_to``/``listing_start``/``listing_end``
sao TEMPO EFETIVO (unico gate). ``source_reference_year``/``source_available_from``
/``source_observed_at`` sao TEMPO DE TRANSACAO -- nunca gate.

Limitacao real (docs/historical_universe.md §3.3): ``Codigo_Negociacao`` vem
vazio 2010-2017. Nesse caso ``ticker=None`` e ``quality_flag='incomplete'``;
``share_class`` continua sendo o discriminador.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from datetime import date
from typing import Any

from stock_research.transforms.fundamentals_facts import parse_date

SOURCE = "cvm_fca"


def _norm(text: str | None) -> str:
    if not text:
        return ""
    ascii_only = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_only.upper().split())


_MARKET_MAP = {
    "BOLSA": "bolsa",
    "BALCAO ORGANIZADO": "balcao_organizado",
    "BALCAO NAO ORGANIZADO": "balcao_nao_organizado",
    "BALCAO NAO-ORGANIZADO": "balcao_nao_organizado",
}


def normalize_market(mercado: str | None) -> str | None:
    return _MARKET_MAP.get(_norm(mercado))


def classify_security(valor_mobiliario: str | None, classe_pref: str | None) -> str | None:
    """``Valor_Mobiliario`` (+ classe da PN) -> ``share_class``, ou ``None`` se
    nao for acao/unit/DR (debenture, nota comercial, bonus... ficam de fora do
    universo de acoes).
    """
    vm = _norm(valor_mobiliario)
    if not vm:
        return None
    if "ORDINARIA" in vm:
        return "ON"
    if "UNIT" in vm:
        return "UNT"
    if "BDR" in vm or "DEPOSITO DE ACOES" in vm or "DEPOSITARY" in vm:
        return "DR"
    if "PREFERENCIA" in vm:
        c = _norm(classe_pref)
        if c in {"A", "CLASSE A"}:
            return "PNA"
        if c in {"B", "CLASSE B"}:
            return "PNB"
        if c in {"C", "CLASSE C"}:
            return "PNC"
        if c in {"D", "CLASSE D"}:
            return "PND"
        return "PN"
    return None  # debenture, nota comercial/promissoria, bonus de subscricao, ...


def normalize_venue(sigla: str | None) -> str | None:
    n = _norm(sigla)
    if not n:
        return None
    if "B3" in n or "BOVESPA" in n or "BM F" in n or "BMF" in n:
        return "B3"
    if "CETIP" in n:
        return "CETIP"
    return n[:32]


@dataclass(frozen=True)
class InstrumentLifecycleCandidate:
    row: dict[str, Any] | None
    warnings: tuple[str, ...] = ()


def build_instrument_candidate(
    vm_row: dict[str, Any],
    *,
    reference_year: int,
    source_available_from: Any,
    source_observed_at: Any,
    run_id: int | None,
) -> InstrumentLifecycleCandidate:
    """Uma linha da FCA ``valor_mobiliario`` -> candidato (ainda por ano). O
    ``company_id``/``instrument_id`` sao resolvidos pelo pipeline.
    """
    cnpj = (vm_row.get("CNPJ_Companhia") or "").strip()
    if not cnpj:
        return InstrumentLifecycleCandidate(None, ("linha FCA sem CNPJ_Companhia",))

    share_class = classify_security(
        vm_row.get("Valor_Mobiliario"),
        vm_row.get("Sigla_Classe_Acao_Preferencial") or vm_row.get("Classe_Acao_Preferencial"),
    )
    if share_class is None:
        return InstrumentLifecycleCandidate(None)  # nao e acao -- ignora em silencio, de proposito

    ticker = (vm_row.get("Codigo_Negociacao") or "").strip() or None
    trading_start = parse_date(vm_row.get("Data_Inicio_Negociacao"))
    trading_end = parse_date(vm_row.get("Data_Fim_Negociacao"))
    listing_start = parse_date(vm_row.get("Data_Inicio_Listagem"))
    listing_end = parse_date(vm_row.get("Data_Fim_Listagem"))

    valid_from = trading_start or listing_start
    quality_bits: list[str] = []
    quality_flag = "ok"
    if valid_from is None:
        # Sem data efetiva utilizavel -- o pipeline resolve por fallback
        # (company.valid_from) ou marca NOT_ELIGIBLE_DATA. Nunca cai no filtro
        # em silencio (Handoff §5.1).
        quality_flag = "missing_input"
        quality_bits.append("sem Data_Inicio_Negociacao nem Data_Inicio_Listagem")
    if ticker is None:
        quality_flag = "incomplete" if quality_flag == "ok" else quality_flag
        quality_bits.append("FCA nao informa Codigo_Negociacao antes de 2018")

    trading_status = "delisted" if trading_end is not None else "trading"

    row = {
        "cnpj": cnpj,
        "valid_from": valid_from,
        "valid_to": trading_end,
        "listing_start": listing_start,
        "listing_end": listing_end,
        "ticker": ticker,
        "share_class": share_class,
        "isin": None,
        "market": normalize_market(vm_row.get("Mercado")),
        "listing_venue": normalize_venue(vm_row.get("Sigla_Entidade_Administradora")),
        "segment": (vm_row.get("Segmento") or "").strip() or None,
        "trading_status": trading_status,
        "source": SOURCE,
        "source_reference_year": reference_year,
        "source_available_from": source_available_from,
        "source_observed_at": source_observed_at,
        "run_id": run_id,
        "quality_flag": quality_flag,
        "quality_reason": "; ".join(quality_bits) or None,
    }
    return InstrumentLifecycleCandidate(row)


def _identity(row: dict[str, Any]) -> tuple[Any, ...]:
    return (row["cnpj"], row["share_class"], row["ticker"])


def merge_instrument_intervals(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Funde candidatos anuais da mesma ``(cnpj, classe, ticker)`` num intervalo.

    - ``valid_from`` / ``listing_start`` = o menor observado.
    - ``valid_to`` / ``listing_end`` = o menor **nao nulo** observado (a primeira
      FCA que reportou o fim). Se nenhuma FCA reportou fim -> NULL (o pipeline
      deriva depois, Handoff §5.2).
    - ``source_reference_year`` = o maior (FCA mais recente = conhecimento mais
      completo); ``source_available_from`` / ``source_observed_at`` idem.
    - ``quality_flag`` = o pior; ``ticker`` NULL num ano e preenchido noutro
      colapsam em identidades diferentes de proposito (o pipeline liga os dois
      pela classe se precisar).
    """
    by_id: dict[tuple[Any, ...], dict[str, Any]] = {}
    for cand in candidates:
        key = _identity(cand)
        cur = by_id.get(key)
        if cur is None:
            by_id[key] = dict(cand)
            continue
        cur["valid_from"] = _min_date(cur["valid_from"], cand["valid_from"])
        cur["listing_start"] = _min_date(cur["listing_start"], cand["listing_start"])
        cur["valid_to"] = _min_date(cur["valid_to"], cand["valid_to"])
        cur["listing_end"] = _min_date(cur["listing_end"], cand["listing_end"])
        if cand["source_reference_year"] >= cur["source_reference_year"]:
            cur["source_reference_year"] = cand["source_reference_year"]
            cur["source_available_from"] = cand["source_available_from"]
            cur["source_observed_at"] = cand["source_observed_at"]
            cur["market"] = cand["market"] or cur["market"]
            cur["segment"] = cand["segment"] or cur["segment"]
            cur["listing_venue"] = cand["listing_venue"] or cur["listing_venue"]
        cur["trading_status"] = "delisted" if cur["valid_to"] is not None else "trading"
        cur["quality_flag"] = _worst_flag(cur["quality_flag"], cand["quality_flag"])
        reasons = {r for r in (cur.get("quality_reason"), cand.get("quality_reason")) if r}
        cur["quality_reason"] = "; ".join(sorted(reasons)) or None
    return list(by_id.values())


def _min_date(a: date | None, b: date | None) -> date | None:
    if a is None:
        return b
    if b is None:
        return a
    return min(a, b)


_FLAG_RANK = {"ok": 0, "estimated": 1, "incomplete": 2, "inconsistent": 3, "missing_input": 4}


def _worst_flag(a: str, b: str) -> str:
    return a if _FLAG_RANK.get(a, 0) >= _FLAG_RANK.get(b, 0) else b


def instrument_eligible_at(row: dict[str, Any], as_of: date) -> bool:
    """Predicado do Handoff §6 -- TEMPO EFETIVO apenas.

    NENHUMA referencia a source_*. NULL em ``valid_from`` / ``listing_start``
    **nao** cai no filtro implicitamente: retorna ``False`` aqui, e o chamador
    (pipeline / relatorio) e responsavel por contabilizar como
    ``NOT_ELIGIBLE_DATA`` -- nunca drop silencioso (Handoff §5.1, spec §94).
    """
    vf = row.get("valid_from")
    if vf is None or vf > as_of:
        return False
    vt = row.get("valid_to")
    if vt is not None and vt < as_of:
        return False
    ls = row.get("listing_start")
    if ls is None or ls > as_of:
        return False
    le = row.get("listing_end")
    return le is None or le >= as_of
