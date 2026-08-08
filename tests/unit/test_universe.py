"""Testes de ``pipelines/universe.py`` -- carga do universo a partir do YAML.

Cobre especificamente a regressao encontrada ao validar a busca de noticias
(M6): ``_alias_rows`` promovia ``company_name``/``legal_name`` para alias
"forte" incondicionalmente, mesmo quando o mesmo termo ja estava listado como
fraco em ``aliases.weak`` -- caso real de VALE3, onde ``company_name: Vale``
colide com o alias fraco "Vale" (ambiguo: "vale a pena", "vale-refeicao").
"""

from __future__ import annotations

from stock_research.pipelines.universe import _alias_rows


def _rows_by_alias(rows: list[dict]) -> dict[str, bool]:
    return {r["alias"]: r["is_strong"] for r in rows}


class TestAliasRows:
    def test_company_name_forte_por_padrao(self):
        entry = {"ticker": "PETR4", "company_name": "Petrobras", "aliases": {}}

        rows = _alias_rows(entry, instrument_id=1)

        assert _rows_by_alias(rows)["Petrobras"] is True

    def test_alias_explicito_como_fraco_permanece_fraco(self):
        entry = {
            "ticker": "PETR4",
            "company_name": "Petrobras",
            "aliases": {"weak": ["Petrobas"]},
        }

        rows = _alias_rows(entry, instrument_id=1)

        assert _rows_by_alias(rows)["Petrobas"] is False

    def test_company_name_que_coincide_com_alias_fraco_fica_fraco(self):
        # Caso real: VALE3 tem company_name="Vale" E aliases.weak=["Vale"].
        # A classificacao explicita do YAML precisa vencer a promocao
        # automatica -- "Vale" isolado gera ruido de busca (fase1.md 26).
        entry = {
            "ticker": "VALE3",
            "company_name": "Vale",
            "aliases": {
                "strong": ["Vale S.A.", "VALE3"],
                "weak": ["Vale"],
            },
        }

        rows = _alias_rows(entry, instrument_id=1)
        by_alias = _rows_by_alias(rows)

        assert by_alias["Vale"] is False
        assert by_alias["Vale S.A."] is True
        assert by_alias["VALE3"] is True

    def test_legal_name_que_coincide_com_alias_fraco_tambem_fica_fraco(self):
        entry = {
            "ticker": "XXXX3",
            "company_name": "Empresa Exemplo",
            "legal_name": "Exemplo",
            "aliases": {"weak": ["Exemplo"]},
        }

        rows = _alias_rows(entry, instrument_id=1)

        assert _rows_by_alias(rows)["Exemplo"] is False

    def test_sem_duplicata_entre_ticker_e_alias_forte_repetido(self):
        entry = {
            "ticker": "PETR4",
            "company_name": "Petrobras",
            "aliases": {"strong": ["PETR4"]},  # repete o ticker
        }

        rows = _alias_rows(entry, instrument_id=1)

        assert len([r for r in rows if r["alias"].upper() == "PETR4"]) == 1

    def test_comparacao_de_fraco_ignora_caixa(self):
        entry = {
            "ticker": "PETR4",
            "company_name": "PETROBRAS",
            "aliases": {"weak": ["petrobras"]},
        }

        rows = _alias_rows(entry, instrument_id=1)

        assert _rows_by_alias(rows)["PETROBRAS"] is False
