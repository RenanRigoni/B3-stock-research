"""Transformacoes puras: linhas da FRE -> linhas de ``share_count_history``.

Nenhuma funcao aqui faz I/O. Point-in-time (``available_from`` a partir de
``DT_RECEB``) e a escolha da linha de capital correta sao a logica delicada, e
ficam testaveis sem rede/banco -- mesmo espirito de
``transforms/fundamentals_facts.py``.

Regras validadas contra FRE 2024 e 2013 reais (fase2_plan.md 13.1, achado
2026-08-27 no §24):

* ``shares_issued`` vem da linha ``Tipo_Capital == 'Capital Integralizado'`` do
  arquivo ``capital_social`` -- nao ``Capital Emitido``/``Subscrito``/
  ``Autorizado``. Quando ha mais de uma linha ``Integralizado`` na mesma versao
  (aprovacoes societarias distintas -- visto em 2013), escolhe a de
  ``Data_Autorizacao_Aprovacao`` mais recente e marca ``inconsistent`` se as
  quantidades divergirem entre elas.
* ``free_float_shares`` vem de ``Quantidade_Acoes_*_Circulacao`` do arquivo
  ``distribuicao_capital`` -- e FREE FLOAT (exclui o bloco de controle), NAO
  "emitidas menos tesouraria". Nunca usado como denominador de market cap.
* ``treasury_shares``/``shares_outstanding`` ficam ``None``: a FRE nao traz
  tesouraria de forma consistente entre anos (2013 tem arquivo dedicado, 2024
  nao). Inventar ``issued - free_float`` daria um numero errado (subtrairia o
  controlador). ``quality_flag='missing_input'`` nesses campos.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from stock_research.transforms.fundamentals_facts import (
    availability_from_metadata,
    parse_date,
    parse_decimal,
)

TIPO_CAPITAL_INTEGRALIZADO = "Capital Integralizado"
# Ordem de preferencia para shares_issued. `Capital Integralizado` (capital
# efetivamente pago) e o correto; quando ausente -- visto na FRE recente da Vale
# (2023-2024) --, cai para Subscrito e depois Emitido, que sao iguais ao
# Integralizado numa empresa com capital totalmente pago (confirmado em PETR4
# 2024: os tres tipos com quantidades identicas). O fallback marca `estimated`.
_CAPITAL_PRIORITY = ("Capital Integralizado", "Capital Subscrito", "Capital Emitido")
SOURCE = "cvm_fre"
CALCULATION_VERSION = "share_count_v1"

_CLASS_COLUMNS = {
    "ON": ("Quantidade_Acoes_Ordinarias", "Quantidade_Acoes_Ordinarias_Circulacao"),
    "PN": ("Quantidade_Acoes_Preferenciais", "Quantidade_Acoes_Preferenciais_Circulacao"),
    "TOTAL": ("Quantidade_Total_Acoes", "Quantidade_Total_Acoes_Circulacao"),
}


@dataclass(frozen=True)
class IntegralizadoSelection:
    row: dict[str, Any] | None
    quality_flag: str
    quality_reason: str | None


def _quantities(r: dict[str, Any]) -> tuple[Decimal | None, Decimal | None, Decimal | None]:
    return (
        parse_decimal(r.get("Quantidade_Acoes_Ordinarias")),
        parse_decimal(r.get("Quantidade_Acoes_Preferenciais")),
        parse_decimal(r.get("Quantidade_Total_Acoes")),
    )


def select_capital_integralizado(rows: list[dict[str, Any]]) -> IntegralizadoSelection:
    """Escolhe a linha de capital paga de um grupo ``(cnpj, data_ref, versao)``
    do arquivo ``capital_social`` -- ``Capital Integralizado``, ou o fallback de
    ``_CAPITAL_PRIORITY`` quando ele nao existe.

    ``rows`` ja deve estar filtrado para um unico ``(cnpj, data_ref, versao)``.
    """
    for tier, tipo in enumerate(_CAPITAL_PRIORITY):
        matches = [r for r in rows if (r.get("Tipo_Capital") or "").strip() == tipo]
        if not matches:
            continue

        chosen = max(
            matches, key=lambda r: parse_date(r.get("Data_Autorizacao_Aprovacao")) or date.min
        )
        divergent = len({_quantities(r) for r in matches}) > 1
        if divergent:
            return IntegralizadoSelection(
                chosen,
                "inconsistent",
                f"{len(matches)} linhas '{tipo}' com quantidades divergentes na mesma versao; "
                "escolhida a de aprovacao mais recente",
            )
        if tier == 0:
            return IntegralizadoSelection(chosen, "ok", None)
        return IntegralizadoSelection(
            chosen, "estimated", f"sem 'Capital Integralizado'; usada linha '{tipo}'"
        )

    return IntegralizadoSelection(
        None, "missing_input", "sem linha de capital (Integralizado/Subscrito/Emitido)"
    )


@dataclass
class ShareCountBuildResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_share_count_rows(
    *,
    reference_date_raw: str | None,
    version_raw: str | None,
    metadata_row: dict[str, Any] | None,
    capital_rows: list[dict[str, Any]],
    distribuicao_row: dict[str, Any] | None,
    source_file: str,
    run_id: int | None,
) -> ShareCountBuildResult:
    """Um ``(cnpj, data_ref, versao)`` da FRE -> ate 3 linhas de
    ``share_count_history`` (ON, PN, TOTAL).

    ``reference_date_raw``/``version_raw`` vem do proprio arquivo
    ``capital_social`` (``Data_Referencia``/``Versao``). ``metadata_row`` e a
    linha do arquivo indice desse ``(cnpj, data_ref, versao)`` -- traz
    ``DT_RECEB`` para ``available_from`` -- ou ``None`` quando a versao do
    ``capital_social`` nao tem documento correspondente no indice.
    ``capital_rows`` sao as linhas de ``capital_social`` do grupo;
    ``distribuicao_row`` (opcional) e a unica linha de ``distribuicao_capital``.
    """
    result = ShareCountBuildResult()

    reference_date = parse_date(reference_date_raw)
    version = (version_raw or "").strip()
    if reference_date is None or not version:
        result.warnings.append(
            f"capital_social sem Data_Referencia/Versao utilizavel: "
            f"{reference_date_raw!r}/{version_raw!r}"
        )
        return result

    filing_received_at, available_from = availability_from_metadata(metadata_row)
    if metadata_row is None:
        result.warnings.append(
            f"versao {version} de {reference_date} sem documento no indice FRE -- "
            "available_from fica nulo (fora de consulta point-in-time)"
        )
    selection = select_capital_integralizado(capital_rows)
    if selection.quality_flag != "ok":
        result.warnings.append(selection.quality_reason or "capital_social inconsistente")

    for share_class, (issued_col, ff_col) in _CLASS_COLUMNS.items():
        shares_issued = (
            parse_decimal(selection.row.get(issued_col)) if selection.row is not None else None
        )
        free_float = (
            parse_decimal(distribuicao_row.get(ff_col)) if distribuicao_row is not None else None
        )

        flag, reason = _class_quality(selection, shares_issued)
        result.rows.append(
            {
                "share_class": share_class,
                "reference_date": reference_date,
                "version": version,
                "filing_received_at": filing_received_at,
                "available_from": available_from,
                "shares_issued": shares_issued,
                "free_float_shares": free_float,
                "treasury_shares": None,
                "shares_outstanding": None,
                "source": SOURCE,
                "calculation_version": CALCULATION_VERSION,
                "quality_flag": flag,
                "quality_reason": reason,
                "run_id": run_id,
            }
        )
    return result


def _class_quality(
    selection: IntegralizadoSelection, shares_issued: Decimal | None
) -> tuple[str, str | None]:
    if shares_issued is None:
        return "missing_input", selection.quality_reason or "shares_issued ausente"
    # Propaga o flag da selecao de capital (ok / estimated / inconsistent).
    return selection.quality_flag, selection.quality_reason


def dedupe_key(row: dict[str, Any]) -> tuple[Any, ...]:
    """Chave natural (sem ``company_id``, que so o pipeline conhece)."""
    return (row["share_class"], row["reference_date"], row["version"])


def latest_available(rows: list[dict[str, Any]], *, as_of: datetime) -> dict[str, Any] | None:
    """Ultima versao com ``available_from <= as_of`` (point-in-time).

    Helper de leitura -- usado pelo calculo de market cap (§4), nao pela
    ingestao. Mantido aqui junto da regra de negocio da tabela.
    """
    eligible = [
        r for r in rows if r.get("available_from") is not None and r["available_from"] <= as_of
    ]
    if not eligible:
        return None
    return max(eligible, key=lambda r: (r["available_from"], str(r["version"])))
