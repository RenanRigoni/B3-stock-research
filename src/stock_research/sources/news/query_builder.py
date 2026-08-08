"""Monta a query de busca do GDELT a partir dos aliases de uma empresa
(fase1.md 26).

Funcao pura, sem I/O -- testavel sem rede. So usa aliases ``is_strong=true``:
um alias fraco ("Vale" isolado, "Itau" isolado) gera ruido se usado na busca
sozinho (fase1.md 26: "buscar ticker isolado pode gerar ruido"). A query
efetivamente usada em cada chamada e salva junto do resultado bruto
(``news_articles.query_used``), para poder reconstituir depois por que um
artigo apareceu.

Sintaxe de query do GDELT DOC 2.0 API (fase1.md 861-882): termos entre aspas
casam frase exata; ``OR`` dentro de parenteses agrupa alternativas; espaco
entre grupos e AND implicito; ``sourcelang:`` filtra idioma da fonte.
"""

from __future__ import annotations

from collections.abc import Iterable

# GDELT nao documenta um limite formal de tamanho de query, mas queries muito
# longas (muitos termos OR) degradam relevancia e aumentam risco de erro 400.
# Cinco aliases fortes cobrem nome, ticker e razao social sem exagerar.
MAX_ALIASES_PER_QUERY = 5


def build_company_query(aliases: list[str], *, sourcelang: str | None = "portuguese") -> str:
    """``aliases`` deve conter so termos fortes (``company_aliases.is_strong``).

    Exemplo: ``build_company_query(["Petrobras", "Petroleo Brasileiro", "PETR4"])``
    -> ``'("Petrobras" OR "Petroleo Brasileiro" OR "PETR4") sourcelang:portuguese'``
    """
    unique = _dedupe_preserving_order(a.strip() for a in aliases if a and a.strip())
    if not unique:
        raise ValueError("build_company_query exige ao menos um alias forte nao vazio")

    selected = unique[:MAX_ALIASES_PER_QUERY]
    quoted = [f'"{_escape_quotes(a)}"' for a in selected]
    group = quoted[0] if len(quoted) == 1 else f"({' OR '.join(quoted)})"

    return f"{group} sourcelang:{sourcelang}" if sourcelang else group


def _escape_quotes(text: str) -> str:
    return text.replace('"', "'")


def _dedupe_preserving_order(items: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out
