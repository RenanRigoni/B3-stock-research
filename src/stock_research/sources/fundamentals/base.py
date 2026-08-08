"""Interface comum aos artefatos brutos da CVM (fase1.md 42-45).

Cadastro, DFP e ITR sao todos "baixar um arquivo, preservar em disco, so
depois processar". Esta interface isola o HTTP puro do parsing, para o
parsing ser testavel com fixtures sem rede (fase1.md 45: baixar um ZIP real
antes de escrever qualquer parser definitivo).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


@dataclass(frozen=True)
class RawDownload:
    """Um arquivo bruto ja gravado em disco, com checksum.

    ``already_existed`` distingue um download novo de um cache local hit --
    quem chama decide se isso importa (ex.: nao reprocessar raw_files igual).
    """

    url: str
    local_path: Path
    sha256: str
    bytes: int
    downloaded_at: datetime
    already_existed: bool = False


class CvmSchemaError(RuntimeError):
    """O layout de um arquivo da CVM nao bate com o schema minimo esperado.

    Levantado em vez de tentar adivinhar -- fase1.md 45: "nunca produzir
    dados errados silenciosamente". O arquivo bruto ja esta em disco antes
    desta excecao ser possivel, entao nada se perde.
    """
