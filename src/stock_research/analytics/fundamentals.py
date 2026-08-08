"""Consulta point-in-time de fundamentos (fase1.md 47, 62-63).

Esta e a regra mais importante do projeto inteiro: uma demonstracao
referente a 31/12/2023 nao estava disponivel ao mercado em 31/12/2023.
``get_fundamentals_as_of`` filtra SEMPRE por ``available_from``, nunca por
``reference_date`` -- usar ``reference_date <= data`` sozinho ainda vaza
informacao do futuro (o documento pode ter sido publicado meses depois do
fim do periodo a que se refere).

A selecao em si (``select_point_in_time``) e uma funcao PURA, deliberadamente
separada do acesso a banco: e o que ``tests/unit/test_lookahead.py`` exercita
diretamente, sem precisar de mock de SQL -- o contrato "nenhuma linha com
available_from > as_of" fica testavel de forma direta e legivel, e o teste
falha de verdade se a logica regredir (fase1.md 63).
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from stock_research.db import fetch_all
from stock_research.transforms.fundamentals_facts import end_of_day_brt


def select_point_in_time(facts: list[dict[str, Any]], as_of_boundary: datetime) -> list[dict[str, Any]]:
    """Funcao pura: de todos os fatos de um instrumento, devolve so a versao
    mais recente de cada ``(statement_type, reference_date, account_code,
    is_consolidated)`` cujo ``available_from`` e <= ``as_of_boundary``.

    Reapresentacoes (fase1.md 48): se a versao mais nova de uma conta so
    ficou disponivel depois de ``as_of_boundary``, ela e ignorada e a versao
    anterior (que realmente circulava naquele momento) e devolvida em seu
    lugar -- nunca a mais recente do banco.
    """
    eligible = [f for f in facts if f.get("available_from") is not None and f["available_from"] <= as_of_boundary]

    latest: dict[tuple[Any, ...], dict[str, Any]] = {}
    for f in eligible:
        key = (f["statement_type"], f["reference_date"], f["account_code"], f["is_consolidated"])
        current_best = latest.get(key)
        if current_best is None:
            latest[key] = f
            continue
        # Empate em available_from: fact_id maior e o que foi gravado por
        # ultimo (mesmo desempate usado na consulta SQL equivalente).
        if (f["available_from"], f.get("fact_id", 0)) > (current_best["available_from"], current_best.get("fact_id", 0)):
            latest[key] = f

    return list(latest.values())


def get_fundamentals_as_of(
    instrument_id: int, as_of: date, *, consolidated: bool = True
) -> list[dict[str, Any]]:
    """Fatos contabeis conhecidos pelo mercado ate o fim de ``as_of``
    (fase1.md 47, 62). Nunca filtra por ``reference_date``."""
    boundary = end_of_day_brt(as_of)
    facts = fetch_all(
        """
        select fact_id, document_id, instrument_id, cvm_code, document_type, statement_type,
               reference_date, period_start, period_end, filing_received_at, available_from,
               version, account_code, account_description, value, currency, scale,
               fiscal_year_order, is_consolidated
        from public.financial_statement_facts
        where instrument_id = %s
          and is_consolidated = %s
          and available_from is not null
        """,
        [instrument_id, consolidated],
    )
    return select_point_in_time(facts, boundary)
