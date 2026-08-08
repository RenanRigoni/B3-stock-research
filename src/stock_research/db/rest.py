"""Backend alternativo: fala com o Postgres via PostgREST usando a service key.

Existe porque as API keys do Supabase nao servem como senha do Postgres. Sem
``DATABASE_URL``, este backend permite que os pipelines funcionem mesmo assim.

Trade-offs conscientes:
  * mais lento que psycopg em cargas grandes (JSON sobre HTTP, sem COPY);
  * SQL arbitrario passa pela RPC ``public.exec_sql``, restrita a service_role.

Assim que ``DATABASE_URL`` for preenchida, ``stock_research.db`` passa a usar
psycopg sozinho -- nenhum chamador precisa mudar.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime
from typing import Any

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from stock_research.config import MissingConfigError, load_secrets
from stock_research.logging import get_logger

logger = get_logger(__name__)

# PostgREST rejeita payloads muito grandes; 1000 linhas por requisicao e seguro.
REST_BATCH_SIZE = 1000
TIMEOUT = httpx.Timeout(120.0, connect=15.0)


class RestError(RuntimeError):
    """Falha vinda do PostgREST, com o corpo da resposta preservado."""


def _base_url() -> str:
    secrets = load_secrets()
    if not secrets.supabase_url:
        raise MissingConfigError("SUPABASE_URL nao configurada")
    return secrets.supabase_url.rstrip("/")


def _service_key() -> str:
    secrets = load_secrets()
    key = secrets.supabase_secret_key or secrets.supabase_service_role_key
    if not key:
        raise MissingConfigError(
            "Nenhuma chave de servico configurada "
            "(SUPABASE_SECRET_KEY ou SUPABASE_SERVICE_ROLE_KEY)"
        )
    return key


def _headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    key = _service_key()
    headers = {
        "apikey": key,
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if extra:
        headers.update(extra)
    return headers


def is_available() -> bool:
    secrets = load_secrets()
    return bool(secrets.supabase_url) and bool(
        secrets.supabase_secret_key or secrets.supabase_service_role_key
    )


@retry(
    retry=retry_if_exception_type((httpx.TransportError, httpx.HTTPStatusError)),
    stop=stop_after_attempt(4),
    wait=wait_exponential_jitter(initial=1, max=20),
    reraise=True,
)
def _request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    url = f"{_base_url()}{path}"
    with httpx.Client(timeout=TIMEOUT) as client:
        response = client.request(method, url, **kwargs)
    # 4xx que nao seja 429 e erro do chamador: nao adianta repetir.
    if response.status_code >= 500 or response.status_code == 429:
        response.raise_for_status()
    if response.status_code >= 400:
        raise RestError(f"{response.status_code} em {path}: {response.text[:500]}")
    return response


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}")


def _coerce_temporal(value: Any) -> Any:
    """PostgREST devolve ``date``/``timestamptz`` como string JSON; o backend
    ``psycopg`` devolve objetos ``date``/``datetime`` nativos via OID do driver.
    Sem essa conversao, o mesmo codigo se comporta diferente dependendo do
    backend ativo -- exatamente o que a fachada em ``db/__init__.py`` promete
    nunca acontecer. Comparacoes point-in-time (``available_from <= boundary``)
    dependem disso: comparar string com ``datetime`` levanta ``TypeError``.
    """
    if not isinstance(value, str):
        return value
    if _TIMESTAMP_RE.match(value):
        try:
            return datetime.fromisoformat(value.replace(" ", "T", 1).replace("Z", "+00:00"))
        except ValueError:
            return value
    if _DATE_RE.match(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return value
    return value


def _coerce_row(row: dict[str, Any]) -> dict[str, Any]:
    return {k: _coerce_temporal(v) for k, v in row.items()}


def fetch_all(query: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    """Executa um SELECT via RPC ``exec_sql``.

    ``params`` usa placeholders ``%s`` no estilo psycopg e sao interpolados aqui
    com quoting adequado, ja que a RPC recebe a query pronta.
    """
    sql_text = _inline_params(query, params) if params else query
    response = _request(
        "POST",
        "/rest/v1/rpc/exec_sql",
        headers=_headers(),
        content=json.dumps({"query": sql_text}),
    )
    payload = response.json()
    if not isinstance(payload, list):
        return []
    return [_coerce_row(row) if isinstance(row, dict) else row for row in payload]


def fetch_one(query: str, params: list[Any] | None = None) -> dict[str, Any] | None:
    rows = fetch_all(query, params)
    return rows[0] if rows else None


def execute(statement: str, params: list[Any] | None = None) -> None:
    """Executa um comando sem retorno via RPC ``exec_ddl``."""
    sql_text = _inline_params(statement, params) if params else statement
    _request(
        "POST",
        "/rest/v1/rpc/exec_ddl",
        headers=_headers(),
        content=json.dumps({"query": sql_text}),
    )


def _quote(value: Any) -> str:
    """Literal SQL seguro. A RPC recebe texto, entao o quoting acontece aqui."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int | float):
        return repr(value)
    if isinstance(value, dict | list):
        escaped = json.dumps(value, ensure_ascii=False, default=str).replace("'", "''")
        return f"'{escaped}'::jsonb"
    text = str(value).replace("'", "''")
    return f"'{text}'"


def _inline_params(query: str, params: list[Any]) -> str:
    """Substitui cada ``%s`` pelo literal correspondente, na ordem."""
    parts = query.split("%s")
    if len(parts) - 1 != len(params):
        raise ValueError(
            f"numero de placeholders ({len(parts) - 1}) difere do de parametros ({len(params)})"
        )
    out = parts[0]
    for value, tail in zip(params, parts[1:], strict=True):
        out += _quote(value) + tail
    return out


# ---------------------------------------------------------------------------
# Escrita idempotente
# ---------------------------------------------------------------------------


def _dedupe_by_conflict_key(rows: list[dict[str, Any]], conflict_columns: list[str]) -> list[dict[str, Any]]:
    """Mantem so a ultima ocorrencia de cada valor de ``conflict_columns``,
    preservando a ordem original das chaves restantes. "Ultima ganha" espelha
    o efeito de rodar upserts sequenciais linha a linha (o que o backend
    psycopg faz de verdade)."""
    last_by_key: dict[tuple[Any, ...], dict[str, Any]] = {}
    for row in rows:
        key = tuple(row.get(c) for c in conflict_columns)
        last_by_key[key] = row
    return list(last_by_key.values())


def upsert_many(
    table: str,
    rows: list[dict[str, Any]],
    conflict_columns: list[str],
    update_columns: list[str] | None = None,
    *,
    batch_size: int = REST_BATCH_SIZE,
) -> dict[str, int]:
    """Upsert via ``Prefer: resolution=merge-duplicates``.

    O PostgREST nao informa quantas linhas foram inseridas versus atualizadas, e
    tambem nao permite escolher quais colunas sobrescrever. Quando
    ``update_columns`` e uma lista vazia, usamos ``ignore-duplicates`` para
    preservar o registro existente.

    Deduplica por ``conflict_columns`` antes de enviar (mantendo a ULTIMA
    ocorrencia de cada chave). Postgres processa um ``INSERT ... VALUES (...),
    (...)`` como uma unica operacao: se duas linhas da MESMA chamada tiverem a
    mesma chave de conflito, ele rejeita com "ON CONFLICT DO UPDATE command
    cannot affect row a second time" -- confirmado batendo nisso duas vezes
    (financial_statement_facts via DMPL, depois news_articles quando o GDELT
    devolveu o mesmo artigo mais de uma vez na mesma janela). O backend
    ``psycopg`` (connection.py) nao sofre disso porque executa linha a linha;
    dedup aqui faz os dois backends se comportarem igual, em vez de cada
    chamador ter que se lembrar de deduplicar por conta propria.
    """
    if not rows:
        return {"inserted": 0, "updated": 0, "total": 0}

    rows = _dedupe_by_conflict_key(rows, conflict_columns)
    resolution = "ignore-duplicates" if update_columns == [] else "merge-duplicates"
    on_conflict = ",".join(conflict_columns)
    total = 0

    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        response = _request(
            "POST",
            f"/rest/v1/{table}?on_conflict={on_conflict}",
            headers=_headers(
                {"Prefer": f"resolution={resolution},return=representation,count=exact"}
            ),
            content=json.dumps(chunk, ensure_ascii=False, default=str),
        )
        returned = response.json()
        total += len(returned) if isinstance(returned, list) else len(chunk)

    logger.debug("upsert REST %s: %d linhas", table, total)
    # Sem distincao inserted/updated neste backend; total e o numero confiavel.
    return {"inserted": total, "updated": 0, "total": total}


def insert_many(table: str, rows: list[dict[str, Any]], *, batch_size: int = REST_BATCH_SIZE) -> int:
    """``INSERT`` simples via POST, sem ``Prefer`` de resolucao (sem ON CONFLICT).

    Espelha ``connection.insert_many``: usar so em tabelas sem chave natural.
    """
    if not rows:
        return 0

    total = 0
    for start in range(0, len(rows), batch_size):
        chunk = rows[start : start + batch_size]
        _request(
            "POST",
            f"/rest/v1/{table}",
            headers=_headers({"Prefer": "return=minimal"}),
            content=json.dumps(chunk, ensure_ascii=False, default=str),
        )
        total += len(chunk)
    logger.debug("insert REST %s: %d linhas", table, total)
    return total


def insert_returning(table: str, row: dict[str, Any]) -> dict[str, Any]:
    """INSERT de uma linha devolvendo o registro criado (para pegar o id)."""
    response = _request(
        "POST",
        f"/rest/v1/{table}",
        headers=_headers({"Prefer": "return=representation"}),
        content=json.dumps([row], ensure_ascii=False, default=str),
    )
    payload = response.json()
    if not isinstance(payload, list) or not payload:
        raise RestError(f"insert em {table} nao retornou a linha criada")
    return payload[0]


def update_where(table: str, filters: dict[str, Any], values: dict[str, Any]) -> None:
    """UPDATE com filtros de igualdade."""
    query = "&".join(f"{col}=eq.{val}" for col, val in filters.items())
    _request(
        "PATCH",
        f"/rest/v1/{table}?{query}",
        headers=_headers({"Prefer": "return=minimal"}),
        content=json.dumps(values, ensure_ascii=False, default=str),
    )


def healthcheck() -> dict[str, Any]:
    rows = fetch_all(
        "select version() as version, current_database() as database, "
        "(select count(*) from information_schema.tables "
        " where table_schema = 'public' and table_type = 'BASE TABLE') as tables"
    )
    if not rows:
        raise RestError("healthcheck nao retornou dados")
    return rows[0]
