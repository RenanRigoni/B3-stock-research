"""Logging estruturado: console legivel (rich) + arquivo rotativo.

Regra dura: nenhum token, chave ou senha entra em log. ``_SECRET_PATTERNS``
mascara valores que se parecem com credencial antes de qualquer emissao.
"""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from stock_research.config import load_secrets, project_root

# (padrao, substituicao). Cada entrada preserva o contexto ao redor do segredo,
# para o log continuar diagnostico -- so o valor sensivel vira "***".
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    # Chaves do Supabase: sb_secret_..., sb_publishable_...
    (re.compile(r"(sb_(?:secret|publishable)_)[A-Za-z0-9_\-]+"), r"\1***"),
    # JWT: header e payload sao publicos; a assinatura e o que autentica.
    (re.compile(r"(eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,})\.[A-Za-z0-9_\-]+"), r"\1.***"),
    # Senha dentro da connection string do Postgres.
    (re.compile(r"(postgres(?:ql)?://[^:/\s]+:)[^@\s]+(@)", re.IGNORECASE), r"\1***\2"),
    # Par chave/valor generico: token=..., "password": "...".
    (
        re.compile(r"((?:token|key|password|secret)[\"']?\s*[:=]\s*[\"']?)[^\s\"',]+", re.IGNORECASE),
        r"\1***",
    ),
]

console = Console(stderr=True)


class _RedactSecrets(logging.Filter):
    """Mascara credenciais na mensagem antes de ela chegar a qualquer handler."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = message
        for pattern, replacement in _SECRET_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def setup_logging(level: str | None = None, log_file: Path | None = None) -> None:
    """Configura o root logger. Chamar uma vez, no inicio do processo."""
    resolved_level = (level or load_secrets().log_level or "INFO").upper()

    root = logging.getLogger()
    root.setLevel(resolved_level)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    redactor = _RedactSecrets()

    console_handler = RichHandler(
        console=console,
        rich_tracebacks=True,
        show_path=False,
        omit_repeated_times=False,
    )
    console_handler.setFormatter(logging.Formatter("%(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    console_handler.addFilter(redactor)
    root.addHandler(console_handler)

    target = log_file or project_root() / "logs" / "stock_research.log"
    target.parent.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        target, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"
        )
    )
    file_handler.addFilter(redactor)
    root.addHandler(file_handler)

    # yfinance e httpx sao verbosos demais em DEBUG.
    logging.getLogger("httpx").setLevel("WARNING")
    logging.getLogger("urllib3").setLevel("WARNING")
    logging.getLogger("yfinance").setLevel("WARNING")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
