"""Score de relevancia de uma noticia para uma empresa (fase1.md 36).

Funcao pura: recebe o artigo e os aliases da empresa, devolve score + termos
que casaram. Nao decide categoria (isso e Milestone 8) nem le banco -- so as
features que fase1.md 36 lista e que independem de classificacao:

    * nome/ticker da empresa no titulo (alias forte no titulo == sinal forte);
    * presenca de alias fraco (conta, mas menos);
    * quantidade de aliases distintos que casaram;
    * presenca no dominio (raro, mas ocorre em portais de RI da propria empresa).

Threshold de classificacao (``config/settings.yaml news.relevance``):
    >= 0.80 -> alta
    0.50-0.79 -> media
    < 0.50 -> baixa

Nunca excluir a linha por relevancia baixa na camada raw (fase1.md 36) --
so marca ``review_status``, quem decide o que fazer com isso e o pipeline
de eventos (Milestone 9) ou revisao humana.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from re import Pattern
from re import compile as re_compile


@dataclass(frozen=True)
class AliasMatch:
    alias: str
    is_strong: bool
    in_title: bool


@dataclass(frozen=True)
class RelevanceResult:
    score: float
    matched_terms: list[str]
    is_primary_company: bool

    @property
    def band(self) -> str:
        if self.score >= 0.80:
            return "high"
        if self.score >= 0.50:
            return "medium"
        return "low"


def _normalize(text: str) -> str:
    ascii_form = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return ascii_form.lower()


def _word_boundary_pattern(term: str) -> Pattern[str]:
    escaped = re_compile(r"[\\^$.|?*+()\[\]{}]").sub(lambda m: "\\" + m.group(0), term)
    return re_compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])")


def score_relevance(
    title: str | None,
    aliases: list[tuple[str, bool]],  # (alias, is_strong)
    *,
    domain: str | None = None,
) -> RelevanceResult:
    """``aliases``: lista de ``(alias, is_strong)`` da empresa candidata.

    Casamento por palavra inteira (nao substring) -- "Vale" nao pode casar
    dentro de "inviavel" ou "avaliação". Alias multi-palavra ("Petroleo
    Brasileiro") casa se a frase inteira aparecer, tambem por borda de
    palavra em cada extremo.
    """
    if not title or not aliases:
        return RelevanceResult(score=0.0, matched_terms=[], is_primary_company=False)

    normalized_title = _normalize(title)
    normalized_domain = _normalize(domain) if domain else ""

    matches: list[AliasMatch] = []
    for alias, is_strong in aliases:
        normalized_alias = _normalize(alias)
        if not normalized_alias:
            continue
        pattern = _word_boundary_pattern(normalized_alias)
        if pattern.search(normalized_title):
            matches.append(AliasMatch(alias=alias, is_strong=is_strong, in_title=True))

    if not matches:
        # Alias nao apareceu no titulo -- o artigo pode ainda ser relevante
        # (aliases so batem no corpo do texto), mas sem titulo casando o sinal
        # e fraco. Usar domain como ultimo recurso: portal de RI da propria
        # empresa costuma ter o nome dela no dominio.
        strong_names = [a for a, strong in aliases if strong]
        domain_hit = any(_normalize(a).replace(" ", "") in normalized_domain.replace(".", "") for a in strong_names)
        if domain_hit:
            return RelevanceResult(score=0.35, matched_terms=[], is_primary_company=False)
        return RelevanceResult(score=0.0, matched_terms=[], is_primary_company=False)

    strong_hits = [m for m in matches if m.is_strong]
    weak_hits = [m for m in matches if not m.is_strong]

    # Base: um alias forte no titulo ja e sinal solido o bastante pra faixa
    # "alta" (>= 0.80) sozinho -- fase1.md 36 trata "nome da empresa no
    # titulo" como a feature mais forte da lista, nao como evidencia parcial
    # que precisa de reforco de um segundo sinal pra ser confiavel.
    score = 0.0
    if strong_hits:
        score = 0.85
        # Mais de um alias forte distinto (ex.: nome E ticker) reforca.
        score += min(0.15, 0.05 * (len(strong_hits) - 1))
    elif weak_hits:
        # Alias fraco sozinho no titulo: sinal presente, mas ambiguo por
        # definicao (fase1.md 26) -- nunca alcanca a faixa "alta" sozinho.
        score = 0.45 + min(0.10, 0.05 * (len(weak_hits) - 1))

    score = min(1.0, score)
    matched_terms = [m.alias for m in matches]
    is_primary = bool(strong_hits)

    return RelevanceResult(score=round(score, 3), matched_terms=matched_terms, is_primary_company=is_primary)
