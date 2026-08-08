"""Conexao e primitivas de escrita idempotente sobre o Supabase Postgres.

Todo pipeline segue o mesmo contrato:

    run_id = start_run("prices", provider="yfinance", ticker="PETR4")
    ...                                   # baixa, salva raw, normaliza
    upsert_many(...)                      # idempotente por chave natural
    finish_run(run_id, status="success", ...)

Reexecutar um pipeline nunca duplica linha: toda escrita passa por
``INSERT ... ON CONFLICT (chave natural) DO UPDATE``.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from stock_research.config import code_version, require_database_url
from stock_research.logging import get_logger

logger = get_logger(__name__)

# Lotes grandes demais estouram o limite de parametros do protocolo; 500 linhas
# e um meio-termo seguro para tabelas largas como financial_statement_facts.
DEFAULT_BATCH_SIZE = 500


@contextmanager
def connection(autocommit: bool = False) -> Iterator[psycopg.Connection]:
    """Conexao com o Postgres. Fecha sempre, faz rollback em erro."""
    conn = psycopg.connect(require_database_url(), autocommit=autocommit, row_factory=dict_row)
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def cursor(autocommit: bool = False) -> Iterator[psycopg.Cursor]:
    with connection(autocommit=autocommit) as conn, conn.cursor() as cur:
        yield cur


def fetch_all(query: str, params: Sequence[Any] | None = None) -> list[dict[str, Any]]:
    with cursor() as cur:
        cur.execute(query, params)
        return list(cur.fetchall())


def fetch_one(query: str, params: Sequence[Any] | None = None) -> dict[str, Any] | None:
    with cursor() as cur:
        cur.execute(query, params)
        return cur.fetchone()


def healthcheck() -> dict[str, Any]:
    """Confirma conexao e retorna versao do servidor e contagem de tabelas."""
    with cursor() as cur:
        cur.execute("select version() as version, current_database() as database")
        info = cur.fetchone() or {}
        cur.execute(
            "select count(*) as tables from information_schema.tables "
            "where table_schema = 'public' and table_type = 'BASE TABLE'"
        )
        tables = cur.fetchone() or {}
    return {**info, **tables}


# ---------------------------------------------------------------------------
# Escrita idempotente
# ---------------------------------------------------------------------------


def _adapt(value: Any) -> Any:
    """Converte tipos Python que o psycopg nao mapeia sozinho."""
    if isinstance(value, dict | list) and not isinstance(value, str):
        # Listas de escalares viram array Postgres; dicts viram jsonb.
        if isinstance(value, list):
            return value
        return json.dumps(value, ensure_ascii=False, default=str)
    return value


def upsert_many(
    table: str,
    rows: Sequence[dict[str, Any]],
    conflict_columns: Sequence[str],
    update_columns: Sequence[str] | None = None,
    *,
    conn: psycopg.Connection | None = None,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> dict[str, int]:
    """``INSERT ... ON CONFLICT DO UPDATE`` em lotes.

    Args:
        table: nome da tabela em ``public``.
        rows: dicionarios homogeneos (mesmas chaves).
        conflict_columns: colunas da chave natural (precisam ter UNIQUE/PK).
        update_columns: colunas a sobrescrever no conflito. ``None`` = todas as
            colunas que nao fazem parte da chave. Lista vazia = ``DO NOTHING``.
        conn: conexao existente, para participar de uma transacao maior.

    Returns:
        ``{"inserted": n, "updated": n, "total": n}``.
    """
    if not rows:
        return {"inserted": 0, "updated": 0, "total": 0}

    columns = list(rows[0].keys())
    missing = [c for c in conflict_columns if c not in columns]
    if missing:
        raise ValueError(f"conflict_columns ausentes nas linhas: {missing}")

    if update_columns is None:
        update_columns = [c for c in columns if c not in conflict_columns]

    ident = sql.Identifier
    col_sql = sql.SQL(", ").join(ident(c) for c in columns)
    conflict_sql = sql.SQL(", ").join(ident(c) for c in conflict_columns)

    if update_columns:
        assignments = sql.SQL(", ").join(
            sql.SQL("{} = excluded.{}").format(ident(c), ident(c)) for c in update_columns
        )
        conflict_action = sql.SQL("do update set {}").format(assignments)
    else:
        conflict_action = sql.SQL("do nothing")

    # xmax = 0 identifica linha recem-inserida; != 0 significa que houve update.
    statement = sql.SQL(
        "insert into public.{table} ({cols}) values ({placeholders}) "
        "on conflict ({conflict}) {action} "
        "returning (xmax = 0) as was_inserted"
    ).format(
        table=ident(table),
        cols=col_sql,
        placeholders=sql.SQL(", ").join(sql.Placeholder() * len(columns)),
        conflict=conflict_sql,
        action=conflict_action,
    )

    inserted = updated = 0

    def _run(active: psycopg.Connection) -> None:
        nonlocal inserted, updated
        with active.cursor() as cur:
            for start in range(0, len(rows), batch_size):
                chunk = rows[start : start + batch_size]
                for row in chunk:
                    cur.execute(statement, [_adapt(row.get(c)) for c in columns])
                    result = cur.fetchone()
                    if result is None:
                        continue  # DO NOTHING que nao afetou linha
                    if result["was_inserted"]:
                        inserted += 1
                    else:
                        updated += 1

    if conn is not None:
        _run(conn)
    else:
        with connection() as owned:
            _run(owned)

    total = inserted + updated
    logger.debug("upsert %s: %d inseridos, %d atualizados", table, inserted, updated)
    return {"inserted": inserted, "updated": updated, "total": total}


def insert_many(table: str, rows: Sequence[dict[str, Any]], *, batch_size: int = DEFAULT_BATCH_SIZE) -> int:
    """``INSERT`` simples, sem ``ON CONFLICT``.

    Para tabelas sem chave natural (ex.: ``data_changes``), onde a idempotencia
    vem da logica de quem chama (so inserimos quando ha diferenca real) e nao
    de uma constraint UNIQUE.
    """
    if not rows:
        return 0

    columns = list(rows[0].keys())
    ident = sql.Identifier
    statement = sql.SQL("insert into public.{table} ({cols}) values ({placeholders})").format(
        table=ident(table),
        cols=sql.SQL(", ").join(ident(c) for c in columns),
        placeholders=sql.SQL(", ").join(sql.Placeholder() * len(columns)),
    )

    with connection() as conn, conn.cursor() as cur:
        for start in range(0, len(rows), batch_size):
            chunk = rows[start : start + batch_size]
            cur.executemany(statement, [[_adapt(row.get(c)) for c in columns] for row in chunk])

    logger.debug("insert %s: %d linhas", table, len(rows))
    return len(rows)


def insert_returning(table: str, row: dict[str, Any]) -> dict[str, Any]:
    """``INSERT`` de uma linha devolvendo o registro criado (para pegar o id
    gerado por ``identity``). Espelha ``rest.insert_returning`` -- mesma
    assinatura nos dois backends."""
    columns = list(row.keys())
    ident = sql.Identifier
    statement = sql.SQL(
        "insert into public.{table} ({cols}) values ({placeholders}) returning *"
    ).format(
        table=ident(table),
        cols=sql.SQL(", ").join(ident(c) for c in columns),
        placeholders=sql.SQL(", ").join(sql.Placeholder() * len(columns)),
    )
    with cursor(autocommit=True) as cur:
        cur.execute(statement, [_adapt(row.get(c)) for c in columns])
        created = cur.fetchone()
    assert created is not None
    return created


# ---------------------------------------------------------------------------
# Linhagem (fase1.md 64)
# ---------------------------------------------------------------------------


def start_run(
    pipeline: str,
    *,
    provider: str | None = None,
    ticker: str | None = None,
    params: dict[str, Any] | None = None,
    config_hash: str | None = None,
) -> int:
    """Abre um registro em ``ingestion_runs`` e devolve o ``run_id``."""
    with cursor(autocommit=True) as cur:
        cur.execute(
            "insert into public.ingestion_runs "
            "(pipeline, provider, ticker, params, config_hash, code_version) "
            "values (%s, %s, %s, %s, %s, %s) returning run_id",
            (
                pipeline,
                provider,
                ticker,
                json.dumps(params or {}, ensure_ascii=False, default=str),
                config_hash,
                code_version(),
            ),
        )
        row = cur.fetchone()
    assert row is not None
    run_id = int(row["run_id"])
    logger.info("run %d iniciado: pipeline=%s provider=%s ticker=%s", run_id, pipeline, provider, ticker)
    return run_id


def finish_run(
    run_id: int,
    *,
    status: str = "success",
    records_raw: int = 0,
    records_inserted: int = 0,
    records_updated: int = 0,
    records_rejected: int = 0,
    error_message: str | None = None,
) -> None:
    """Fecha o registro do run. Chamar sempre, inclusive em caminho de erro."""
    with cursor(autocommit=True) as cur:
        cur.execute(
            "update public.ingestion_runs set finished_at = now(), status = %s, "
            "records_raw = %s, records_inserted = %s, records_updated = %s, "
            "records_rejected = %s, error_message = %s where run_id = %s",
            (
                status,
                records_raw,
                records_inserted,
                records_updated,
                records_rejected,
                error_message,
                run_id,
            ),
        )
    logger.info(
        "run %d finalizado: status=%s +%d ~%d !%d",
        run_id,
        status,
        records_inserted,
        records_updated,
        records_rejected,
    )


def record_finding(
    run_id: int | None,
    pipeline: str,
    check_name: str,
    severity: str,
    message: str,
    *,
    entity_type: str | None = None,
    entity_id: str | None = None,
    trade_date: Any = None,
    details: dict[str, Any] | None = None,
) -> None:
    """Grava uma anomalia em ``quality_findings``. Nada e descartado em silencio."""
    if severity not in {"INFO", "WARNING", "ERROR"}:
        raise ValueError(f"severity invalida: {severity}")
    with cursor(autocommit=True) as cur:
        cur.execute(
            "insert into public.quality_findings "
            "(run_id, pipeline, check_name, severity, entity_type, entity_id, "
            " trade_date, message, details) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                run_id,
                pipeline,
                check_name,
                severity,
                entity_type,
                entity_id,
                trade_date,
                message,
                json.dumps(details or {}, ensure_ascii=False, default=str),
            ),
        )
