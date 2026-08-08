"""Classificacao heuristica de noticias (fase1.md 33-35, 37).

Default do MVP: heuristica + regras a partir de ``config/news_taxonomy.yaml``.
Nenhuma chave paga e obrigatoria -- a interface deixa espaco para um provider
LLM/modelo local depois, mas o pipeline nunca depende disso (fase1.md 35).

Tres decisoes que o codigo NAO mistura, porque sao coisas diferentes
(fase1.md 33):

    category      -- sobre o que a noticia fala (taxonomia versionada)
    sentiment     -- tom linguistico do titulo
    impact_score  -- NAO calculado aqui

``impact_score`` fica deliberadamente ``None``. "Petrobras anuncia
investimento de R$ 100 bilhoes" e linguisticamente positivo e
economicamente ambiguo; inferir impacto a partir do titulo seria inventar
(fase1.md 33 usa exatamente esse exemplo). Impacto so sera estimavel na
Fase 2+, cruzando com fundamentos e reacao de preco.

Todo resultado carrega ``analysis_method``/``analysis_version`` e uma
``explanation`` estruturada: nada de caixa preta (fase1.md 119) -- da pra
perguntar "por que essa categoria?" e receber os termos que casaram.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from re import Pattern
from re import compile as re_compile
from typing import Any

ANALYSIS_METHOD = "heuristic"


@dataclass(frozen=True)
class Classification:
    category: str | None
    subcategory: str | None
    scope: str  # company | sector | macro | mixed | unknown
    sentiment: str  # positive | neutral | negative | mixed | unknown
    sentiment_score: float | None
    is_company_specific: bool
    is_macro: bool
    is_sector: bool
    is_rumor: bool
    is_official_source: bool
    analysis_version: str
    explanation: dict[str, Any] = field(default_factory=dict)


def _normalize(text: str) -> str:
    ascii_form = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return " ".join(ascii_form.lower().split())


def _phrase_pattern(term: str) -> Pattern[str]:
    escaped = re_compile(r"[\\^$.|?*+()\[\]{}]").sub(lambda m: "\\" + m.group(0), _normalize(term))
    return re_compile(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])")


def _count_hits(haystack: str, terms: list[str]) -> list[str]:
    return [t for t in terms if _phrase_pattern(t).search(haystack)]


def classify(
    title: str | None,
    taxonomy: dict[str, Any],
    *,
    domain: str | None = None,
) -> Classification:
    """Classifica pelo TITULO (unico texto garantido -- ``article_text`` e
    opcional, fase1.md 32). ``taxonomy`` e o dict de ``news_taxonomy.yaml``.
    """
    version = taxonomy.get("version", "news_taxonomy_v1")
    if not title or not title.strip():
        return Classification(
            category=None, subcategory=None, scope="unknown", sentiment="unknown",
            sentiment_score=None, is_company_specific=False, is_macro=False, is_sector=False,
            is_rumor=False, is_official_source=False, analysis_version=version,
            explanation={"reason": "titulo ausente ou vazio"},
        )

    text = _normalize(title)
    categories = taxonomy.get("categories") or {}

    category, scope, matched_terms, runner_ups = _pick_category(text, categories)
    sentiment, sentiment_score, sentiment_hits = _score_sentiment(text, taxonomy)

    rumor_hits = _count_hits(text, taxonomy.get("rumor_markers") or [])
    official_hits = _official_source_hits(text, domain, taxonomy)

    return Classification(
        category=category,
        subcategory=None,  # heuristica nao distingue subcategoria; fica pra revisao/LLM
        scope=scope,
        sentiment=sentiment,
        sentiment_score=sentiment_score,
        is_company_specific=scope == "company",
        is_macro=scope == "macro",
        is_sector=scope == "sector",
        is_rumor=bool(rumor_hits),
        is_official_source=bool(official_hits),
        analysis_version=version,
        explanation={
            "matched_terms": matched_terms,
            "runner_up_categories": runner_ups,
            "sentiment_terms": sentiment_hits,
            "rumor_markers": rumor_hits,
            "official_source_markers": official_hits,
        },
    )


def _pick_category(
    text: str, categories: dict[str, Any]
) -> tuple[str | None, str, list[str], list[dict[str, Any]]]:
    """Categoria com mais termos casados vence. Empate desempata pelo termo
    mais longo casado (mais especifico = mais informativo que varios termos
    genericos). Sem nenhum termo casado -> ``None``, nunca "other" por
    default: "nao classificado" e informacao diferente de "outros"."""
    scored: list[tuple[str, str, list[str]]] = []
    for name, cfg in categories.items():
        keywords = cfg.get("keywords") or []
        if not keywords:
            continue
        hits = _count_hits(text, keywords)
        if hits:
            scored.append((name, cfg.get("scope", "mixed"), hits))

    if not scored:
        return None, "unknown", [], []

    scored.sort(key=lambda s: (len(s[2]), max(len(h) for h in s[2])), reverse=True)
    winner_name, winner_scope, winner_hits = scored[0]
    runner_ups = [
        {"category": name, "matched_terms": hits} for name, _scope, hits in scored[1:4]
    ]
    return winner_name, winner_scope, winner_hits, runner_ups


def _score_sentiment(text: str, taxonomy: dict[str, Any]) -> tuple[str, float | None, dict[str, list[str]]]:
    """Sentimento LINGUISTICO do titulo, nao impacto economico (fase1.md 33).

    Score em [-1, 1] pela proporcao liquida de termos positivos/negativos.
    Titulo com termos dos dois lados vira ``mixed`` em vez de somar e fingir
    um veredito -- "lucro sobe, mas divida cresce" nao e nem positivo nem
    negativo, e ambos.
    """
    lexicon = taxonomy.get("sentiment_lexicon") or {}
    positive_hits = _count_hits(text, lexicon.get("positive") or [])
    negative_hits = _count_hits(text, lexicon.get("negative") or [])
    hits = {"positive": positive_hits, "negative": negative_hits}

    if not positive_hits and not negative_hits:
        return "neutral", 0.0, hits
    if positive_hits and negative_hits:
        total = len(positive_hits) + len(negative_hits)
        return "mixed", round((len(positive_hits) - len(negative_hits)) / total, 3), hits
    if positive_hits:
        return "positive", round(min(1.0, 0.5 + 0.25 * len(positive_hits)), 3), hits
    return "negative", round(max(-1.0, -0.5 - 0.25 * len(negative_hits)), 3), hits


def _official_source_hits(text: str, domain: str | None, taxonomy: dict[str, Any]) -> list[str]:
    markers = taxonomy.get("official_source_markers") or []
    hits = _count_hits(text, [m for m in markers if not m.endswith(".br") and "." not in m])
    if domain:
        normalized_domain = _normalize(domain)
        hits += [m for m in markers if "." in m and _normalize(m) in normalized_domain]
    return hits


def to_analysis_row(
    classification: Classification,
    *,
    article_id: int,
    instrument_id: int | None,
    relevance_score: float | None,
    novelty_score: float | None,
) -> dict[str, Any]:
    """Monta a linha de ``news_analysis``. ``impact_score`` fica ``None`` de
    proposito -- ver docstring do modulo."""
    return {
        "article_id": article_id,
        "instrument_id": instrument_id,
        "category": classification.category,
        "subcategory": classification.subcategory,
        "sentiment": classification.sentiment,
        "sentiment_score": classification.sentiment_score,
        "relevance_score": relevance_score,
        "novelty_score": novelty_score,
        "impact_score": None,
        "is_company_specific": classification.is_company_specific,
        "is_macro": classification.is_macro,
        "is_sector": classification.is_sector,
        "is_rumor": classification.is_rumor,
        "is_official_source": classification.is_official_source,
        "analysis_method": ANALYSIS_METHOD,
        "analysis_model": None,  # heuristica nao tem modelo; LLM/local preencheria aqui
        "analysis_version": classification.analysis_version,
        "explanation": classification.explanation,
    }
