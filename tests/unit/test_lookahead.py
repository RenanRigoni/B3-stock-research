"""Suite anti-look-ahead (fase1.md 63) -- a regra mais importante do projeto.

``select_point_in_time`` (analytics/fundamentals.py) e a unica porta de
entrada para fundamentos em qualquer analise historica. Se ela vazar um fato
que so ficou disponivel depois de ``as_of``, toda conclusao downstream (event
study, score, backtest futuro) fica contaminada por informacao do futuro sem
ninguem perceber.

Contrato testado aqui, ao pe da letra do que fase1.md 63 pede:

    Para um evento em 2023-05-01, nenhum fato usado pode ter
    available_from > 2023-05-01. O teste tem que FALHAR se essa
    invariante for violada -- nao e documentacao, e enforcement.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from stock_research.analytics.fundamentals import select_point_in_time

BRT = timezone(timedelta(hours=-3))


def _fact(
    *,
    fact_id: int,
    account_code: str = "3.01",
    reference_date: date = date(2023, 12, 31),
    available_from: datetime | None,
    statement_type: str = "DRE",
    is_consolidated: bool = True,
    value: str = "100",
) -> dict:
    return {
        "fact_id": fact_id,
        "statement_type": statement_type,
        "reference_date": reference_date,
        "account_code": account_code,
        "is_consolidated": is_consolidated,
        "available_from": available_from,
        "value": value,
    }


class TestNuncaVazaFatoFuturo:
    """O nome do contrato, testado diretamente: nenhum fato retornado pode
    ter available_from posterior ao boundary consultado."""

    def test_fato_disponivel_depois_do_boundary_e_excluido(self):
        boundary = datetime(2023, 5, 1, 23, 59, 59, tzinfo=BRT)
        fact = _fact(fact_id=1, available_from=datetime(2023, 5, 2, 0, 0, 0, tzinfo=BRT))

        result = select_point_in_time([fact], boundary)

        assert result == []

    def test_fato_disponivel_antes_do_boundary_e_incluido(self):
        boundary = datetime(2023, 5, 1, 23, 59, 59, tzinfo=BRT)
        fact = _fact(fact_id=1, available_from=datetime(2023, 4, 1, 0, 0, 0, tzinfo=BRT))

        result = select_point_in_time([fact], boundary)

        assert result == [fact]

    def test_fato_disponivel_exatamente_no_boundary_e_incluido(self):
        # Fronteira inclusiva: "disponivel ATE o fim do dia" inclui o fim do dia.
        boundary = datetime(2023, 5, 1, 23, 59, 59, tzinfo=BRT)
        fact = _fact(fact_id=1, available_from=boundary)

        result = select_point_in_time([fact], boundary)

        assert result == [fact]

    def test_fato_sem_available_from_nunca_e_retornado(self):
        # Ausencia de data de disponibilidade NAO significa "sempre disponivel".
        # Documento so entra em consulta point-in-time apos alguem resolver a
        # data manualmente (fase1.md 47) -- nunca por omissao otimista.
        boundary = datetime(2099, 1, 1, tzinfo=BRT)
        fact = _fact(fact_id=1, available_from=None)

        result = select_point_in_time([fact], boundary)

        assert result == []

    def test_propriedade_nenhum_resultado_viola_o_boundary(self):
        """Versao "property-based" manual do contrato: para qualquer boundary,
        varrendo uma mistura de fatos passados/futuros/sem-data, nada retornado
        pode ter available_from > boundary. Se essa asserção falhar algum dia,
        a regra mais importante do projeto quebrou."""
        boundary = datetime(2023, 5, 1, 23, 59, 59, tzinfo=BRT)
        facts = [
            _fact(fact_id=i, account_code=f"3.{i:02d}", available_from=available_from)
            for i, available_from in enumerate(
                [
                    datetime(2020, 1, 1, tzinfo=BRT),
                    datetime(2023, 4, 30, 23, 59, 59, tzinfo=BRT),
                    datetime(2023, 5, 1, 23, 59, 59, tzinfo=BRT),  # limite exato
                    datetime(2023, 5, 2, 0, 0, 0, tzinfo=BRT),  # 1s depois: fora
                    datetime(2024, 1, 1, tzinfo=BRT),
                    None,
                ]
            )
        ]

        result = select_point_in_time(facts, boundary)

        assert result, "o teste precisa exercitar ao menos um fato elegivel"
        for fact in result:
            assert fact["available_from"] is not None
            assert fact["available_from"] <= boundary


class TestReapresentacao:
    """fase1.md 48: reapresentacao nao pode reescrever o passado. Se a versao
    nova de uma conta so ficou publica depois do boundary, a consulta
    point-in-time devolve a versao antiga (a que circulava na epoca), nunca a
    mais recente do banco."""

    def test_versao_reapresentada_apos_boundary_fica_invisivel(self):
        boundary = datetime(2023, 6, 1, tzinfo=BRT)
        original = _fact(
            fact_id=1, available_from=datetime(2023, 3, 1, tzinfo=BRT), value="100"
        )
        reapresentado = _fact(
            fact_id=2, available_from=datetime(2023, 8, 1, tzinfo=BRT), value="150"
        )

        result = select_point_in_time([original, reapresentado], boundary)

        assert len(result) == 1
        assert result[0]["fact_id"] == 1
        assert result[0]["value"] == "100"

    def test_apos_a_reapresentacao_ficar_publica_a_versao_nova_e_usada(self):
        boundary = datetime(2023, 9, 1, tzinfo=BRT)
        original = _fact(fact_id=1, available_from=datetime(2023, 3, 1, tzinfo=BRT), value="100")
        reapresentado = _fact(fact_id=2, available_from=datetime(2023, 8, 1, tzinfo=BRT), value="150")

        result = select_point_in_time([original, reapresentado], boundary)

        assert len(result) == 1
        assert result[0]["fact_id"] == 2
        assert result[0]["value"] == "150"

    def test_desempate_por_fact_id_quando_available_from_empata(self):
        # Duas linhas com o mesmo available_from (raro, mas possivel se dois
        # documentos forem recebidos no mesmo dia): a gravada por ultimo
        # (fact_id maior) vence -- mesmo criterio usado na consulta SQL
        # equivalente em get_fundamentals_as_of.
        boundary = datetime(2023, 6, 1, tzinfo=BRT)
        same_day = datetime(2023, 3, 1, tzinfo=BRT)
        older = _fact(fact_id=5, available_from=same_day, value="100")
        newer = _fact(fact_id=9, available_from=same_day, value="200")

        result = select_point_in_time([older, newer], boundary)

        assert len(result) == 1
        assert result[0]["fact_id"] == 9
        assert result[0]["value"] == "200"


class TestGranularidadeDaChave:
    """A dedup point-in-time e por (statement_type, reference_date,
    account_code, is_consolidated) -- contas diferentes, referencias
    diferentes, ou consolidado x individual NAO devem se sobrepor."""

    def test_contas_diferentes_nao_se_sobrepoem(self):
        boundary = datetime(2023, 6, 1, tzinfo=BRT)
        receita = _fact(fact_id=1, account_code="3.01", available_from=datetime(2023, 1, 1, tzinfo=BRT))
        custo = _fact(fact_id=2, account_code="3.02", available_from=datetime(2023, 1, 1, tzinfo=BRT))

        result = select_point_in_time([receita, custo], boundary)

        assert {f["fact_id"] for f in result} == {1, 2}

    def test_consolidado_e_individual_nao_se_sobrepoem(self):
        boundary = datetime(2023, 6, 1, tzinfo=BRT)
        con = _fact(fact_id=1, is_consolidated=True, available_from=datetime(2023, 1, 1, tzinfo=BRT))
        ind = _fact(fact_id=2, is_consolidated=False, available_from=datetime(2023, 1, 1, tzinfo=BRT))

        result = select_point_in_time([con, ind], boundary)

        assert {f["fact_id"] for f in result} == {1, 2}

    def test_referencias_diferentes_nao_se_sobrepoem(self):
        boundary = datetime(2024, 6, 1, tzinfo=BRT)
        ano_2022 = _fact(fact_id=1, reference_date=date(2022, 12, 31), available_from=datetime(2023, 3, 1, tzinfo=BRT))
        ano_2023 = _fact(fact_id=2, reference_date=date(2023, 12, 31), available_from=datetime(2024, 3, 1, tzinfo=BRT))

        result = select_point_in_time([ano_2022, ano_2023], boundary)

        assert {f["fact_id"] for f in result} == {1, 2}


class TestCenarioDoEventStudy:
    """Reproduz literalmente o exemplo de fase1.md 63: evento em 2023-05-01,
    nenhum fato elegivel pode ter available_from posterior a essa data."""

    def test_evento_2023_05_01_nao_ve_balanco_de_2023_publicado_em_agosto(self):
        event_date_boundary = datetime(2023, 5, 1, 23, 59, 59, tzinfo=BRT)

        balanco_2022_publicado_marco_2023 = _fact(
            fact_id=1, reference_date=date(2022, 12, 31), available_from=datetime(2023, 3, 20, tzinfo=BRT)
        )
        # Balanco de 2023 so seria publicado em 2024 -- nao pode aparecer de jeito nenhum.
        balanco_2023_publicado_marco_2024 = _fact(
            fact_id=2, reference_date=date(2023, 12, 31), available_from=datetime(2024, 3, 15, tzinfo=BRT)
        )

        result = select_point_in_time(
            [balanco_2022_publicado_marco_2023, balanco_2023_publicado_marco_2024],
            event_date_boundary,
        )

        assert [f["fact_id"] for f in result] == [1]
        for fact in result:
            assert fact["available_from"] <= event_date_boundary, (
                f"VAZAMENTO DE FUTURO: fact_id={fact['fact_id']} tinha "
                f"available_from={fact['available_from']} > boundary do evento"
            )
