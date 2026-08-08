"""Normalizacao pura de noticias: artigo bruto do GDELT -> linha de
``news_articles`` (fase1.md 28-30).

Nenhuma funcao aqui faz I/O -- mesma disciplina de ``transforms/fundamentals_facts.py``.
Duas camadas de deduplicacao moram aqui (a 3a, por similaridade de titulo
entre artigos, precisa de contexto de banco e fica em ``pipelines/news.py``):

    Camada 1 -- URL:      ``canonicalize_url``
    Camada 2 -- titulo:   ``normalize_title`` + hash

fase1.md 39 (``time_precision``): o GDELT so devolve ``seendate`` -- o
instante em que o CRAWLER viu a pagina, nao necessariamente a hora real de
publicacao. Tratado como ``hour`` (temos hora, mas nao e garantidamente a de
publicacao), nunca ``exact``. Isso alimenta a politica conservadora de
``effective_trade_date`` no clustering de eventos (Milestone 9).
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# Parametros de tracking que nao mudam o conteudo da pagina -- removidos na
# canonicalizacao para URLs equivalentes colapsarem na mesma chave.
_TRACKING_PARAM_PREFIXES = ("utm_", "fbclid", "gclid", "igshid", "mc_cid", "mc_eid")

_WHITESPACE_RE = re.compile(r"\s+")
_PUNCTUATION_RE = re.compile(r"[^\w\s]", re.UNICODE)
_GDELT_SEENDATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z$")


def canonicalize_url(raw_url: str) -> str:
    """Camada 1 de dedup (fase1.md 30): unifica http/https, remove ``www.``,
    remove parametros de tracking, remove fragmento, remove barra final.
    """
    parsed = urlparse(raw_url.strip())
    scheme = "https"  # http/https sao o mesmo recurso pra fins de dedup
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]

    kept_params = [
        (k, v) for k, v in parse_qsl(parsed.query, keep_blank_values=True)
        if not any(k.lower().startswith(p) for p in _TRACKING_PARAM_PREFIXES)
    ]
    query = urlencode(sorted(kept_params))

    path = parsed.path.rstrip("/") or "/"

    return urlunparse((scheme, netloc, path, "", query, ""))


def url_hash(canonical_url: str) -> str:
    return hashlib.sha256(canonical_url.encode("utf-8")).hexdigest()


def normalize_title(raw_title: str) -> str:
    """Camada 2 de dedup (fase1.md 30): minusculas, sem acento, sem
    pontuacao, espacos colapsados. Preserva palavras (nao remove stopwords) --
    isso e forma canonica para hash exato, similaridade fica em
    ``pipelines/news.py`` via RapidFuzz sobre este mesmo texto normalizado.
    """
    ascii_form = unicodedata.normalize("NFKD", raw_title).encode("ascii", "ignore").decode("ascii")
    no_punct = _PUNCTUATION_RE.sub(" ", ascii_form.lower())
    return _WHITESPACE_RE.sub(" ", no_punct).strip()


def title_hash(normalized_title: str) -> str | None:
    if not normalized_title:
        return None
    return hashlib.sha256(normalized_title.encode("utf-8")).hexdigest()


def parse_gdelt_seendate(raw: str | None) -> datetime | None:
    """``"20260808T141500Z"`` -> ``datetime`` UTC. Formato compacto proprio do
    GDELT, nao ISO 8601 -- confirmado contra a API real, nao documentacao
    generica que poderia estar desatualizada (fase1.md 45's principio
    aplicado aqui a uma fonte diferente da CVM)."""
    if not raw:
        return None
    match = _GDELT_SEENDATE_RE.match(raw.strip())
    if not match:
        return None
    year, month, day, hour, minute, second = (int(g) for g in match.groups())
    try:
        return datetime(year, month, day, hour, minute, second, tzinfo=UTC)
    except ValueError:
        return None


def build_article_row(
    raw_article: dict[str, Any],
    *,
    query_used: str,
    raw_file: str,
    run_id: int | None,
) -> dict[str, Any] | None:
    """Um artigo do GDELT (``articles[i]`` do JSON) -> linha de ``news_articles``.

    Devolve ``None`` (nunca levanta) quando o artigo nao tem URL -- sem URL
    nao ha como canonicalizar nem deduplicar, o artigo e inutilizavel
    (fase1.md 104: erro numa linha nao aborta o resto).
    """
    url = (raw_article.get("url") or "").strip()
    if not url:
        return None

    canonical = canonicalize_url(url)
    title = (raw_article.get("title") or "").strip() or None
    normalized_title = normalize_title(title) if title else ""
    seen_at = parse_gdelt_seendate(raw_article.get("seendate"))

    language = (raw_article.get("language") or "").strip() or None
    domain = (raw_article.get("domain") or "").strip().lower() or None

    return {
        "provider": "gdelt",
        "provider_id": None,  # GDELT DOC API (ArtList) nao expoe id estavel de artigo
        "url": url,
        "canonical_url": canonical,
        "url_hash": url_hash(canonical),
        "domain": domain,
        "source_name": domain,
        "title": title,
        "title_normalized": normalized_title or None,
        "title_hash": title_hash(normalized_title),
        "language": language,
        "country": (raw_article.get("sourcecountry") or "").strip() or None,
        "source_country": (raw_article.get("sourcecountry") or "").strip() or None,
        # seendate e quando o CRAWLER viu a pagina, nao necessariamente a
        # publicacao real -- por isso "hour" (temos hora) e nao "exact"
        # (fase1.md 39: nunca fingir precisao que a fonte nao garante).
        "published_at_utc": seen_at,
        "published_at_local": None,  # requer conversao de fuso por fonte, fora do MVP
        "source_timezone": None,
        "time_precision": "hour" if seen_at is not None else "unknown",
        "seen_at": seen_at,
        "tone": None,  # ArtList nao devolve tone (outro modo do GDELT, fora do MVP)
        "image_url": (raw_article.get("socialimage") or "").strip() or None,
        "query_used": query_used,
        "raw_file": raw_file,
        "run_id": run_id,
    }
