"""Carregamento de configuracao.

Duas fontes, com papeis distintos:

* ``config/*.yaml`` -- parametros versionados (janelas, thresholds, provedores).
  Vao para o git; mudanca deles muda o resultado e precisa aparecer no diff.
* ``.env`` -- segredos e caminhos da maquina. Nunca vao para o git.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def project_root() -> Path:
    """Raiz do repositorio (dois niveis acima de ``src/stock_research``)."""
    return Path(__file__).resolve().parents[2]


CONFIG_DIR = project_root() / "config"


class Secrets(BaseSettings):
    """Valores do ``.env``. Nenhum deles pode ser logado."""

    model_config = SettingsConfigDict(
        env_file=project_root() / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    supabase_project_ref: str = ""
    supabase_url: str = ""
    supabase_publishable_key: str = ""
    supabase_secret_key: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""

    # Conexao Postgres direta usada pelos pipelines.
    database_url: str = ""

    # Opcional: sem ele a validacao cruzada de precos e pulada, nao quebra.
    brapi_token: str = ""

    data_dir: Path = Field(default=Path("./data"))
    log_level: str = "INFO"

    @property
    def has_database(self) -> bool:
        return bool(self.database_url.strip())

    @property
    def has_brapi(self) -> bool:
        return bool(self.brapi_token.strip())


class MissingConfigError(RuntimeError):
    """Configuracao obrigatoria ausente. Falha explicita, nunca silenciosa."""


def _read_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        raise MissingConfigError(f"Arquivo de configuracao ausente: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise MissingConfigError(f"{path} nao contem um mapeamento YAML valido")
    return data


@lru_cache(maxsize=1)
def load_secrets() -> Secrets:
    load_dotenv(project_root() / ".env", override=False)
    return Secrets()


@lru_cache(maxsize=1)
def load_settings() -> dict[str, Any]:
    """``config/settings.yaml`` -- parametros do pipeline."""
    return _read_yaml("settings.yaml")


@lru_cache(maxsize=1)
def load_companies() -> dict[str, Any]:
    """``config/companies.yaml`` -- universo de instrumentos."""
    return _read_yaml("companies.yaml")


@lru_cache(maxsize=1)
def load_taxonomy() -> dict[str, Any]:
    """``config/news_taxonomy.yaml`` -- categorias de evento e lexicos."""
    return _read_yaml("news_taxonomy.yaml")


@lru_cache(maxsize=1)
def load_company_mapping() -> dict[str, Any]:
    """``config/company_mapping.yaml`` -- ticker -> CNPJ/codigo CVM."""
    return _read_yaml("company_mapping.yaml")


def require_database_url() -> str:
    """URL do Postgres ou erro com instrucao de como obte-la."""
    secrets = load_secrets()
    if not secrets.has_database:
        raise MissingConfigError(
            "DATABASE_URL nao configurada.\n"
            "Pegue em: Supabase Dashboard > Project Settings > Database > "
            "Connection string > URI (use o Session pooler, porta 5432).\n"
            "As API keys do Supabase NAO servem como senha do Postgres."
        )
    return secrets.database_url


def data_dir() -> Path:
    """Diretorio de dados, resolvido a partir da raiz do projeto."""
    raw = load_secrets().data_dir
    path = raw if raw.is_absolute() else project_root() / raw
    return path


def ensure_data_dirs() -> list[Path]:
    """Cria a arvore de dados local. Idempotente."""
    base = data_dir()
    subdirs = [
        base / "raw" / "prices",
        base / "raw" / "news",
        base / "raw" / "cvm",
        base / "raw" / "reference",
        base / "staging",
        base / "curated",
        base / "exports",
    ]
    for path in subdirs:
        path.mkdir(parents=True, exist_ok=True)
    return subdirs


def code_version() -> str:
    """Identificador da versao do codigo, gravado em ``ingestion_runs``."""
    from stock_research import __version__

    sha = os.environ.get("GIT_SHA", "")
    return f"{__version__}+{sha[:8]}" if sha else __version__


def load_universe_config() -> dict[str, Any]:
    """``config/backtest_universe_v1.yaml`` -- criterios do universo investivel."""
    return _read_yaml("backtest_universe_v1.yaml")


@lru_cache(maxsize=1)
def load_price_continuity_exceptions() -> dict[str, Any]:
    """``config/price_continuity_exceptions.yaml`` -- excecao explicita a regra
    canonica de ``price_valid_from`` (Fase 3 M2.1). Cada entrada precisa de
    prova independente e justificativa escrita; a lista nao cresce sem
    autorizacao (ha teste de guarda)."""
    return _read_yaml("price_continuity_exceptions.yaml")


def thresholds_from_config(cfg: dict[str, Any] | None = None) -> Any:
    """Limiares de investibilidade a partir da config versionada.

    ``None`` num campo = limiar **ainda nao aprovado**: o gate correspondente
    NAO e aplicado, a distribuicao e medida e reportada. Nunca inventar numero
    aqui (`fase3.md` §15, Opus regra 7).
    """
    from stock_research.analytics.universe import InvestabilityThresholds

    cfg = cfg if cfg is not None else load_universe_config()
    liq = cfg.get("liquidity") or {}
    dm = cfg.get("data_minimums") or {}
    return InvestabilityThresholds(
        min_avg_financial_volume_60=liq.get("min_avg_financial_volume_60"),
        min_median_financial_volume_60=liq.get("min_median_financial_volume_60"),
        min_trading_days_60=liq.get("min_trading_days_60"),
        min_price_history_days=dm.get("min_price_history_days"),
        require_fundamentals=dm.get("require_fundamentals"),
    )
