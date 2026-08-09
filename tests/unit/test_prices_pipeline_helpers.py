"""Helpers puros do pipeline de precos e da validacao cruzada (fase1.md 19, 21, 75)."""

from __future__ import annotations

from stock_research.pipelines.prices import _differs
from stock_research.pipelines.validation import _count_by_status, _status


class TestDiffers:
    def test_mesmo_valor_nao_difere(self):
        assert _differs(38.5, 38.5) is False

    def test_diferenca_minuscula_por_arredondamento_nao_conta(self):
        assert _differs(38.5, 38.5000001) is False

    def test_correcao_real_do_provedor_e_detectada(self):
        # Yahoo corrigindo uma serie ajustada apos split retroativo (fase1.md 19, 99).
        assert _differs(26.5, 13.25) is True

    def test_none_para_valor_conta_como_diferenca(self):
        assert _differs(None, 38.5) is True

    def test_none_para_none_nao_conta_como_diferenca(self):
        assert _differs(None, None) is False

    def test_ruido_de_recomputacao_do_adj_close_nao_conta_como_correcao(self):
        # Caso real observado na Fase 1.1 (PETR4, 2010-01-04): valor gravado
        # arredondado em numeric(20,6) vs valor recem-recalculado pelo Yahoo
        # encadeando fatores de proventos sobre toda a serie -- ~0.0015% de
        # diferenca, sem nenhuma correcao real ter ocorrido.
        assert _differs(7.864796, 7.864911424013455, relative=True) is False

    def test_correcao_real_do_adj_close_ainda_e_detectada_com_relative(self):
        assert _differs(26.5, 13.25, relative=True) is True


class TestStatusDeValidacao:
    def test_dentro_do_warning_e_ok(self):
        assert _status(0.001, warning_pct=0.005, error_pct=0.02) == "ok"

    def test_acima_do_warning_e_warning(self):
        assert _status(0.01, warning_pct=0.005, error_pct=0.02) == "warning"

    def test_acima_do_error_e_error(self):
        assert _status(0.05, warning_pct=0.005, error_pct=0.02) == "error"

    def test_sem_diferenca_calculavel_e_missing(self):
        assert _status(None, warning_pct=0.005, error_pct=0.02) == "missing"

    def test_conta_por_status(self):
        rows = [{"status": "ok"}, {"status": "ok"}, {"status": "warning"}]
        assert _count_by_status(rows) == {"ok": 2, "warning": 1}
