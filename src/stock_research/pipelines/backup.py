"""``stock-research backup`` (fase1.md 100).

So copia o que ja esta em disco -- ``data/raw/`` e ``config/`` -- pra
``backups/YYYYMMDD_HHMM/``. O banco em si (Supabase) tem backup proprio do
provedor; esta rotina cobre o que so existe localmente e some se a maquina
falhar.
"""

from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from stock_research.config import data_dir, project_root
from stock_research.logging import get_logger

logger = get_logger(__name__)


def run_backup() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = project_root() / "backups" / stamp
    dest.mkdir(parents=True, exist_ok=True)

    raw_dir = data_dir() / "raw"
    if raw_dir.exists():
        shutil.copytree(raw_dir, dest / "raw", dirs_exist_ok=True)
        logger.info("backup: data/raw copiado para %s", dest / "raw")

    config_dir = project_root() / "config"
    if config_dir.exists():
        shutil.copytree(config_dir, dest / "config", dirs_exist_ok=True)
        logger.info("backup: config copiado para %s", dest / "config")

    return dest
