"""Nenhum segredo pode chegar a um handler de log.

O arquivo de log e commitavel por acidente e fica em disco para sempre. Se um
token vazar para la, ele vazou. Estes testes existem para essa regra nao
depender de disciplina humana.

Os valores abaixo sao FICTICIOS, com o mesmo formato dos reais. Fixture de teste
nunca carrega credencial de verdade -- ela iria para o git junto com o codigo.
"""

from __future__ import annotations

import logging

import pytest

from stock_research.logging import _RedactSecrets

FAKE_SECRET_KEY = "sb_secret_AAAAAAAAAAAAAAAAAAAAAA_BBBBBBBBBB"
FAKE_PUBLISHABLE_KEY = "sb_publishable_CCCCCCCCCCCCCCCCCCCCCC_DDDDDDDDDD"
FAKE_JWT = (
    "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"
    ".eyJpc3MiOiJleGFtcGxlIiwicm9sZSI6InNlcnZpY2Vfcm9sZSJ9"
    ".EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE"
)
FAKE_JWT_SIGNATURE = "EEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEEE"


@pytest.fixture
def redact():
    filtro = _RedactSecrets()

    def _apply(message: str, *args: object) -> str:
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname=__file__,
            lineno=1,
            msg=message,
            args=args,
            exc_info=None,
        )
        filtro.filter(record)
        return record.getMessage()

    return _apply


class TestRedacao:
    def test_secret_key_do_supabase(self, redact):
        out = redact(f"usando {FAKE_SECRET_KEY} no header")
        assert "AAAAAAAAAAAAAAAAAAAAAA" not in out
        assert "sb_secret_***" in out

    def test_publishable_key_do_supabase(self, redact):
        out = redact(f"key={FAKE_PUBLISHABLE_KEY}")
        assert "CCCCCCCCCCCCCCCCCCCCCC" not in out

    def test_assinatura_do_jwt(self, redact):
        # Header e payload sao base64 publico; a assinatura e o que autentica.
        out = redact(f"Authorization: Bearer {FAKE_JWT}")
        assert FAKE_JWT_SIGNATURE not in out

    def test_senha_dentro_da_connection_string(self, redact):
        out = redact("conectando em postgresql://postgres.abc:MinhaSenha123@host:5432/postgres")
        assert "MinhaSenha123" not in out
        # O resto da URL sobrevive: o log precisa continuar diagnostico.
        assert "postgresql://postgres.abc:***@host:5432/postgres" in out

    def test_par_chave_valor_generico(self, redact):
        assert "abc123xyz" not in redact("brapi_token=abc123xyz")
        assert "hunter2" not in redact('{"password": "hunter2"}')

    def test_texto_sem_segredo_passa_intacto(self, redact):
        message = "prices PETR4 4120 linhas entre 2010-01-04 e 2026-08-07"
        assert redact(message) == message

    def test_segredo_interpolado_por_args_tambem_e_mascarado(self, redact):
        # logger.info("token=%s", token) -- o caminho facil de esquecer.
        out = redact("token=%s", FAKE_SECRET_KEY)
        assert "AAAAAAAAAAAAAAAAAAAAAA" not in out
