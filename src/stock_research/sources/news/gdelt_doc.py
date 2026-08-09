"""Adapter do GDELT DOC 2.0 API (fase1.md 24-25).

Validado contra a API real (agosto/2026) antes de escrever este modulo:

* endpoint: ``https://api.gdeltproject.org/api/v2/doc/doc``;
* ``format=json`` devolve ``{"articles": [...]}`` com campos
  ``url, url_mobile, title, seendate, socialimage, domain, language,
  sourcecountry`` -- SEM ``tone`` (isso vem de outro modo/produto do GDELT,
  fora do escopo do MVP; fase1.md 28 permite campo ausente ficar ausente);
* ``seendate`` usa formato compacto proprio (``20260808T141500Z``), nao
  ISO 8601 padrao;
* rate limit real confirmado por HTTP 429 com corpo explicito: "please limit
  requests to one every 5 seconds". ``config/settings.yaml`` reflete isso
  (``providers.gdelt.requests_per_second``); o retry aqui faz backoff
  adicional especificamente em 429, com jitter, para nao martelar o limite.

Esta e a DOC API (busca textual, artigo a artigo) -- fase1.md 25 exige
separar isso do GKG/BigQuery, que fica fora do MVP e nao pode virar
dependencia obrigatoria.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from stock_research.config import data_dir, load_settings, project_root
from stock_research.logging import get_logger
from stock_research.sources.news.base import RawNewsResponse
from stock_research.utils.ratelimit import throttle

logger = get_logger(__name__)

PROVIDER = "gdelt"
ENDPOINT = "https://api.gdeltproject.org/api/v2/doc/doc"
# Confirmado contra a API real: 250 e o teto por chamada do modo ArtList.
MAX_RECORDS_PER_CALL = 250
_GDELT_DATETIME_FORMAT = "%Y%m%d%H%M%S"


class GdeltRateLimitError(RuntimeError):
    """HTTP 429 -- corpo real observado: "please limit requests to one every
    5 seconds". Distinto de outros erros HTTP para permitir retry mais
    paciente sem mascarar falhas genuinas (fase1.md 67)."""


class GdeltTimeoutError(RuntimeError):
    """Timeout de rede -- distinto de rate limit e de erro HTTP para o
    checkpoint de backfill poder registrar a causa real (fase1.1 12)."""


class GdeltHttpError(RuntimeError):
    """Erro HTTP != 429 (4xx/5xx). Nao adianta o mesmo backoff paciente do
    rate limit -- provavelmente e um erro real de request (fase1.1 12)."""


class GdeltParseError(RuntimeError):
    """Resposta 200 mas corpo nao e o JSON esperado (fase1.1 12)."""


class GdeltUnsupportedDateRangeError(RuntimeError):
    """A API recusou a janela por estar fora da cobertura historica dela --
    HTTP 200 com corpo texto puro ``"Invalid query start date."`` (observado
    na Fase 1.1 tentando 2015: nao e rate limit, e a fonte dizendo que aquela
    data nao existe pra ela). Diferente das outras falhas, retry nunca resolve
    isso -- a janela precisa ser marcada como definitivamente sem cobertura,
    nao reagendada (fase1.1 22: "nao fingir possuir 2015 se so ha dado depois")."""


def _format_gdelt_datetime(value: datetime) -> str:
    return value.strftime(_GDELT_DATETIME_FORMAT)


@retry(
    retry=retry_if_exception_type((httpx.TransportError, GdeltRateLimitError)),
    stop=stop_after_attempt(5),
    # Backoff generoso: o 429 real observado nao respeitou nem 45s de espera
    # entre chamadas isoladas -- provavelmente limite por IP compartilhado
    # entre varios usuarios da mesma rede/sandbox, nao so "5s por chamada".
    wait=wait_exponential_jitter(initial=8, max=120),
    reraise=True,
)
def _get(params: dict[str, Any], *, requests_per_second: float, timeout: float) -> httpx.Response:
    throttle(PROVIDER, requests_per_second)
    logger.info("gdelt: query=%r params=%s", params.get("query"), {k: v for k, v in params.items() if k != "query"})
    with httpx.Client(timeout=timeout) as client:
        response = client.get(ENDPOINT, params=params)
    if response.status_code == 429:
        raise GdeltRateLimitError(response.text[:300])
    response.raise_for_status()
    return response


def fetch_articles(
    query: str,
    *,
    start: datetime | None = None,
    end: datetime | None = None,
    max_records: int = MAX_RECORDS_PER_CALL,
    sort: str = "DateDesc",
) -> RawNewsResponse:
    """Uma chamada a DOC API. Sem paginacao real (o GDELT nao expoe cursor
    estavel no modo ArtList) -- backfill historico usa janelas de data
    estreitas em ``pipelines/news.py`` para nao depender de paginacao
    inexistente."""
    settings = load_settings()
    provider_cfg = settings["providers"]["gdelt"]
    params: dict[str, Any] = {
        "query": query,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": min(max_records, MAX_RECORDS_PER_CALL),
        "sort": sort,
    }
    if start is not None:
        params["startdatetime"] = _format_gdelt_datetime(start)
    if end is not None:
        params["enddatetime"] = _format_gdelt_datetime(end)

    try:
        response = _get(
            params,
            requests_per_second=float(provider_cfg["requests_per_second"]),
            timeout=float(provider_cfg["timeout_seconds"]),
        )
    except GdeltRateLimitError:
        raise
    except httpx.TimeoutException as exc:
        raise GdeltTimeoutError(str(exc)) from exc
    except httpx.HTTPStatusError as exc:
        raise GdeltHttpError(str(exc)) from exc
    except httpx.TransportError as exc:
        # Esgotou os retries de erro de rede sem ser timeout puro (DNS, conexao
        # recusada, etc.) -- mais proximo de "erro de rede" que de "timeout".
        raise GdeltHttpError(str(exc)) from exc

    if "invalid query start date" in response.text.strip().lower():
        raise GdeltUnsupportedDateRangeError(response.text.strip()[:200])

    try:
        payload = _parse_response_json(response.text)
    except RuntimeError as exc:
        raise GdeltParseError(str(exc)) from exc
    articles = payload.get("articles") or []

    raw_path = _save_raw(query, params, response.text)
    return RawNewsResponse(
        provider=PROVIDER,
        query=query,
        articles=articles,
        raw_path=raw_path,
        fetched_at=datetime.now(UTC),
        request_url=str(response.request.url) if response.request else ENDPOINT,
    )


def _parse_response_json(text: str) -> dict[str, Any]:
    """O GDELT as vezes devolve corpo vazio ou HTML de erro com status 200
    (observado esporadicamente). Nunca deixar json.JSONDecodeError propagar
    como se fosse um bug do parser -- e uma resposta invalida da fonte."""
    if not text.strip():
        return {"articles": []}
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"gdelt: resposta nao-JSON (primeiros 200 chars): {text[:200]!r}") from exc
    return parsed if isinstance(parsed, dict) else {"articles": []}


def _save_raw(query: str, params: dict[str, Any], response_text: str) -> Path:
    """Preserva request + resposta antes de qualquer transformacao (fase1.md 27)."""
    digest = hashlib.sha256((query + json.dumps(params, sort_keys=True)).encode("utf-8")).hexdigest()[:16]
    today = date.today().isoformat()
    raw_dir = data_dir() / "raw" / "news" / "gdelt" / today
    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"{digest}.json"

    envelope = {
        "provider": PROVIDER,
        "endpoint": ENDPOINT,
        "params": params,
        "fetched_at": datetime.now(UTC).isoformat(),
        "response": json.loads(response_text) if response_text.strip() else {"articles": []},
    }
    path.write_text(json.dumps(envelope, ensure_ascii=False, indent=2), encoding="utf-8")
    return path.relative_to(project_root())
