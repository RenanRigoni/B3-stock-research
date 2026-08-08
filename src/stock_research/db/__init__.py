"""Acesso ao Supabase Postgres.

Dois backends, mesma interface. A escolha e automatica:

* ``psycopg`` -- usado quando ``DATABASE_URL`` esta configurada. Rapido, com
  transacoes reais e distincao entre linha inserida e atualizada. Preferido.
* ``rest``    -- PostgREST com a service key. Usado quando so temos as API keys
  do Supabase (a senha do Postgres nao esta disponivel). Mais lento e sem
  transacao multi-tabela, mas desbloqueia todos os pipelines.

Chamadores usam apenas as funcoes deste modulo e nao precisam saber qual esta
ativo. Rode ``stock-research doctor`` para ver o backend em uso.
"""

from __future__ import annotations

import json
from typing import Any

from stock_research.config import MissingConfigError, code_version, load_secrets
from stock_research.logging import get_logger

logger = get_logger(__name__)

PSYCOPG = "psycopg"
REST = "rest"


def backend_name() -> str:
    """Qual backend esta ativo, sem abrir conexao."""
    if load_secrets().has_database:
        return PSYCOPG
    from stock_research.db import rest

    if rest.is_available():
        return REST
    raise MissingConfigError(
        "Nenhum backend de banco disponivel.\n"
        "Configure DATABASE_URL (preferido) ou SUPABASE_URL + SUPABASE_SECRET_KEY.\n"
        "Rode `stock-research doctor` para o diagnostico."
    )


def _is_pg() -> bool:
    return backend_name() == PSYCOPG


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def fetch_all(query: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    if _is_pg():
        from stock_research.db import connection as pg

        return pg.fetch_all(query, params)
    from stock_research.db import rest

    return rest.fetch_all(query, params)


def fetch_one(query: str, params: list[Any] | None = None) -> dict[str, Any] | None:
    rows = fetch_all(query, params)
    return rows[0] if rows else None


def execute(statement: str, params: list[Any] | None = None) -> None:
    """Comando sem retorno."""
    if _is_pg():
        from stock_research.db.connection import cursor

        with cursor(autocommit=True) as cur:
            cur.execute(statement, params)
        return
    from stock_research.db import rest

    rest.execute(statement, params)


def healthcheck() -> dict[str, Any]:
    if _is_pg():
        from stock_research.db import connection as pg

        info = pg.healthcheck()
    else:
        from stock_research.db import rest

        info = rest.healthcheck()
    return {**info, "backend": backend_name()}


# ---------------------------------------------------------------------------
# Escrita idempotente
# ---------------------------------------------------------------------------


def upsert_many(
    table: str,
    rows: list[dict[str, Any]],
    conflict_columns: list[str],
    update_columns: list[str] | None = None,
    **kwargs: Any,
) -> dict[str, int]:
    """``INSERT ... ON CONFLICT DO UPDATE``. Reexecutar nunca duplica."""
    if not rows:
        return {"inserted": 0, "updated": 0, "total": 0}

    if _is_pg():
        from stock_research.db import connection as pg

        return pg.upsert_many(table, rows, conflict_columns, update_columns, **kwargs)

    from stock_research.db import rest

    kwargs.pop("conn", None)  # sem transacao compartilhada no backend REST
    return rest.upsert_many(table, rows, conflict_columns, update_columns, **kwargs)


def insert_many(table: str, rows: list[dict[str, Any]]) -> int:
    """``INSERT`` em lote sem ``ON CONFLICT``.

    Usar so quando a tabela nao tem chave natural (ex.: ``data_changes``) e a
    idempotencia vem da logica de quem chama, nao de uma constraint UNIQUE.
    """
    if not rows:
        return 0

    if _is_pg():
        from stock_research.db import connection as pg

        return pg.insert_many(table, rows)

    from stock_research.db import rest

    return rest.insert_many(table, rows)


def insert_returning(table: str, row: dict[str, Any]) -> dict[str, Any]:
    """``INSERT`` de uma linha devolvendo o registro criado (para pegar um id
    gerado por ``identity``, ex. ``cluster_id``). Mesma assinatura nos dois backends."""
    if _is_pg():
        from stock_research.db import connection as pg

        return pg.insert_returning(table, row)

    from stock_research.db import rest

    return rest.insert_returning(table, row)


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
    if _is_pg():
        from stock_research.db import connection as pg

        return pg.start_run(
            pipeline, provider=provider, ticker=ticker, params=params, config_hash=config_hash
        )

    from stock_research.db import rest

    created = rest.insert_returning(
        "ingestion_runs",
        {
            "pipeline": pipeline,
            "provider": provider,
            "ticker": ticker,
            "params": params or {},
            "config_hash": config_hash,
            "code_version": code_version(),
        },
    )
    run_id = int(created["run_id"])
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
    """Fecha o registro do run. Chamar sempre, inclusive no caminho de erro."""
    if _is_pg():
        from stock_research.db import connection as pg

        pg.finish_run(
            run_id,
            status=status,
            records_raw=records_raw,
            records_inserted=records_inserted,
            records_updated=records_updated,
            records_rejected=records_rejected,
            error_message=error_message,
        )
        return

    from stock_research.db import rest

    rest.execute(
        "update public.ingestion_runs set finished_at = now(), status = %s, "
        "records_raw = %s, records_inserted = %s, records_updated = %s, "
        "records_rejected = %s, error_message = %s where run_id = %s",
        [
            status,
            records_raw,
            records_inserted,
            records_updated,
            records_rejected,
            error_message,
            run_id,
        ],
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
    """Grava uma anomalia em ``quality_findings``. Nada some em silencio."""
    if severity not in {"INFO", "WARNING", "ERROR"}:
        raise ValueError(f"severity invalida: {severity}")

    if _is_pg():
        from stock_research.db import connection as pg

        pg.record_finding(
            run_id,
            pipeline,
            check_name,
            severity,
            message,
            entity_type=entity_type,
            entity_id=entity_id,
            trade_date=trade_date,
            details=details,
        )
        return

    from stock_research.db import rest

    rest.insert_returning(
        "quality_findings",
        {
            "run_id": run_id,
            "pipeline": pipeline,
            "check_name": check_name,
            "severity": severity,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "trade_date": str(trade_date) if trade_date is not None else None,
            "message": message,
            "details": details or {},
        },
    )


def connection(**kwargs: Any) -> Any:
    """Conexao psycopg crua. So existe no backend ``psycopg``."""
    if not _is_pg():
        raise MissingConfigError(
            "Transacao explicita exige DATABASE_URL (backend psycopg). "
            "O backend REST nao suporta transacao multi-tabela."
        )
    from stock_research.db.connection import connection as pg_connection

    return pg_connection(**kwargs)


__all__ = [
    "PSYCOPG",
    "REST",
    "backend_name",
    "connection",
    "execute",
    "fetch_all",
    "fetch_one",
    "finish_run",
    "healthcheck",
    "insert_many",
    "insert_returning",
    "record_finding",
    "start_run",
    "upsert_many",
]

# Reexportado para pipelines que serializam params antes de gravar.
json_dumps = json.dumps
