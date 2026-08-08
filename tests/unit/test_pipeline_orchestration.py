"""Testes de ``pipelines/pipeline.py`` -- deteccao de sucesso/falha por
etapa (fase1.md 112). A maioria dos pipelines internos captura a propria
excecao e devolve um dict em vez de deixar a excecao subir; ``_step``
precisa inspecionar esse dict, nao so um ``except``."""

from __future__ import annotations

from stock_research.pipelines.pipeline import _result_indicates_success, _step


class TestResultIndicatesSuccess:
    def test_dict_com_status_failed_e_falha(self):
        assert _result_indicates_success({"status": "failed", "error": "x"}) is False

    def test_dict_com_status_success_e_sucesso(self):
        assert _result_indicates_success({"status": "success"}) is True

    def test_dict_com_lista_failed_nao_vazia_e_falha(self):
        assert _result_indicates_success({"results": {}, "failed": ["PETR4"]}) is False

    def test_dict_com_lista_failed_vazia_e_sucesso(self):
        assert _result_indicates_success({"results": {}, "failed": []}) is True

    def test_dict_sem_status_nem_failed_e_sucesso(self):
        assert _result_indicates_success({"registry_rows": 2677, "resolved": 3}) is True

    def test_valor_nao_dict_e_sucesso(self):
        # audit()/report() devolvem Path, nao dict.
        from pathlib import Path

        assert _result_indicates_success(Path("data/exports/x.md")) is True

    def test_none_e_sucesso(self):
        assert _result_indicates_success(None) is True


class TestStep:
    def test_funcao_que_retorna_normalmente_e_ok(self):
        result = _step("teste", lambda: {"status": "success"})

        assert result["ok"] is True

    def test_funcao_que_retorna_status_failed_nao_e_ok(self):
        result = _step("teste", lambda: {"status": "failed", "error": "deu ruim"})

        assert result["ok"] is False

    def test_funcao_que_levanta_excecao_nao_e_ok(self):
        def _raise() -> None:
            raise ValueError("instrumento nao cadastrado")

        result = _step("teste", _raise)

        assert result["ok"] is False
        assert "instrumento nao cadastrado" in result["error"]
