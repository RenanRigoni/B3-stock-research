"""Testes de ``sources/news/query_builder.py`` (fase1.md 26). Funcao pura,
sem rede."""

from __future__ import annotations

import pytest

from stock_research.sources.news.query_builder import build_company_query


class TestBuildCompanyQuery:
    def test_um_alias_nao_usa_parenteses_de_grupo(self):
        query = build_company_query(["Petrobras"], sourcelang=None)

        assert query == '"Petrobras"'

    def test_varios_aliases_viram_grupo_or(self):
        query = build_company_query(["Petrobras", "Petroleo Brasileiro"], sourcelang=None)

        assert query == '("Petrobras" OR "Petroleo Brasileiro")'

    def test_sourcelang_e_anexado_por_padrao(self):
        query = build_company_query(["Petrobras"])

        assert query == '"Petrobras" sourcelang:portuguese'

    def test_sourcelang_none_omite_o_filtro(self):
        query = build_company_query(["Petrobras"], sourcelang=None)

        assert "sourcelang" not in query

    def test_aliases_duplicados_colapsam(self):
        query = build_company_query(["Petrobras", "petrobras", "PETROBRAS"], sourcelang=None)

        assert query == '"Petrobras"'

    def test_aspas_internas_sao_escapadas(self):
        query = build_company_query(['Empresa "Apelido" S.A.'], sourcelang=None)

        assert '"' not in query.strip('"').replace("'Apelido'", "")
        assert "'Apelido'" in query

    def test_limita_ao_maximo_de_aliases(self):
        aliases = [f"Alias{i}" for i in range(10)]

        query = build_company_query(aliases, sourcelang=None)

        assert query.count(" OR ") == 4  # 5 aliases = 4 conectores OR

    def test_lista_vazia_levanta_erro(self):
        with pytest.raises(ValueError):
            build_company_query([], sourcelang=None)

    def test_so_strings_vazias_levanta_erro(self):
        with pytest.raises(ValueError):
            build_company_query(["", "   "], sourcelang=None)

    def test_preserva_ordem_de_prioridade(self):
        # A ordem de entrada reflete a prioridade que o chamador escolheu
        # (fase1.md 26: nome > ticker > razao social costuma ser a ordem
        # mais informativa); a funcao nao deve embaralhar.
        query = build_company_query(["Petrobras", "PETR4"], sourcelang=None)

        assert query.index('"Petrobras"') < query.index('"PETR4"')
