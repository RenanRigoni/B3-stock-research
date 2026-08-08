"""Testes da camada de configuracao.

Rodam offline. Validam que os YAMLs versionados sao coerentes com o que o
codigo espera -- um typo em `settings.yaml` quebra o pipeline inteiro em
silencio se ninguem checar.
"""

from __future__ import annotations

import pytest

from stock_research.config import (
    load_companies,
    load_company_mapping,
    load_settings,
    load_taxonomy,
    project_root,
)


def test_project_root_contem_pyproject():
    assert (project_root() / "pyproject.toml").exists()


class TestSettings:
    def test_timezone_do_projeto_e_sao_paulo(self):
        assert load_settings()["project"]["timezone"] == "America/Sao_Paulo"

    def test_auto_adjust_desligado(self):
        # OHLC bruto e ajustado precisam conviver separados (fase1.md 12).
        # Se isso virar True, adj_close some dentro do close sem rastro.
        assert load_settings()["prices"]["auto_adjust"] is False

    def test_falha_em_lookahead_esta_ligada(self):
        assert load_settings()["quality"]["fail_on_lookahead"] is True

    def test_horizontes_do_event_study_sao_positivos_e_ordenados(self):
        horizons = load_settings()["event_study"]["horizons"]
        assert horizons == sorted(horizons)
        assert all(h > 0 for h in horizons)

    def test_horizontes_pre_evento_sao_negativos(self):
        # Janela anterior e essencial: a noticia pode so confirmar o que o
        # mercado ja precificava (fase1.md 59).
        pre = load_settings()["event_study"]["pre_event_horizons"]
        assert pre, "janela pre-evento nao pode estar vazia"
        assert all(h < 0 for h in pre)

    def test_janela_de_estimacao_termina_antes_do_evento(self):
        cfg = load_settings()["event_study"]
        assert cfg["estimation_start"] < cfg["estimation_end"] < 0

    def test_thresholds_de_validacao_sao_crescentes(self):
        cfg = load_settings()["price_validation"]
        assert 0 < cfg["warning_pct"] < cfg["error_pct"]


class TestCompanies:
    def test_universo_tem_benchmark(self):
        benchmarks = load_companies()["benchmarks"]
        assert any(b["yahoo_symbol"] == "^BVSP" for b in benchmarks)

    def test_tickers_sao_unicos(self):
        cfg = load_companies()
        tickers = [c["ticker"] for c in cfg["companies"]] + [
            b["ticker"] for b in cfg["benchmarks"]
        ]
        assert len(tickers) == len(set(tickers))

    def test_sufixo_sa_nos_simbolos_yahoo(self):
        for company in load_companies()["companies"]:
            assert company["yahoo_symbol"].endswith(".SA"), company["ticker"]

    def test_banco_marcado_como_instituicao_financeira(self):
        # ITUB4 e o caso de teste do bloqueio de metricas inadequadas
        # (net_debt/EBITDA nao se aplica a banco -- fase1.md 49/50).
        itub = next(c for c in load_companies()["companies"] if c["ticker"] == "ITUB4")
        assert itub["flags"]["financial_company"] is True

    @pytest.mark.parametrize("ticker", ["PETR4", "VALE3", "ITUB4"])
    def test_cada_empresa_tem_ao_menos_um_alias_forte(self, ticker):
        company = next(c for c in load_companies()["companies"] if c["ticker"] == ticker)
        assert company["aliases"]["strong"]

    def test_vale_isolado_e_alias_fraco(self):
        # "Vale" casa com "vale a pena", "vale-refeicao", nomes de cidade.
        # Tratar como forte encheria a base de falso positivo.
        vale = next(c for c in load_companies()["companies"] if c["ticker"] == "VALE3")
        assert "Vale" in vale["aliases"]["weak"]
        assert "Vale" not in vale["aliases"]["strong"]


class TestTaxonomy:
    def test_categorias_obrigatorias_existem(self):
        categories = load_taxonomy()["categories"]
        for required in ("earnings", "dividend", "management_change", "rumor", "other"):
            assert required in categories

    def test_todo_escopo_e_valido(self):
        validos = {"company", "sector", "macro", "mixed"}
        for name, cfg in load_taxonomy()["categories"].items():
            assert cfg["scope"] in validos, f"{name} tem scope invalido: {cfg['scope']}"

    def test_taxonomia_e_versionada(self):
        # Classificacoes de versoes diferentes nunca se misturam (fase1.md 87).
        assert load_taxonomy()["version"].startswith("news_taxonomy_")


class TestCompanyMapping:
    def test_nenhuma_entrada_se_autoconfirma(self):
        # `sync-cvm --registry` pode preencher cnpj/cvm_code a partir do
        # cadastro oficial da CVM (dado real, nao inventado) -- mas
        # `confirmed` so vira true por edicao manual do YAML, nunca pelo
        # pipeline (fase1.md 52 e 123: conferencia humana e obrigatoria antes
        # de tratar um identificador oficial como definitivo).
        for ticker, mapping in load_company_mapping()["mappings"].items():
            assert mapping["confirmed"] is False, f"{ticker} marcado como confirmado sem revisao humana"
            if mapping["cnpj"] is not None:
                assert mapping["resolved_by"] is not None, f"{ticker} tem CNPJ sem indicar como foi resolvido"
