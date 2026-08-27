"""Testes offline de ``pipelines/fundamentals_ingest``."""

from __future__ import annotations

from stock_research.pipelines import fundamentals_ingest


def test_target_instruments_filters_active_and_keys_by_cnpj(monkeypatch):
    """So instrumento ativo com CNPJ entra, indexado por CNPJ.

    A partir da Fase 2 uma companhia pode ter mais de um instrumento com o
    mesmo CNPJ (PETR3 + PETR4). As classes secundarias entram inativas; a
    ingestao de fundamentos precisa continuar de instrumento unico por CNPJ,
    senao os fatos iriam para um ``instrument_id`` que ``get_fundamentals_as_of``
    nao consulta (fase2_plan.md 13.4).
    """
    captured: dict[str, str] = {}

    def fake_fetch_all(query: str, *args, **kwargs):
        captured["query"] = query
        return [
            {
                "instrument_id": 1,
                "ticker": "PETR4",
                "cnpj": "33.000.167/0001-01",
                "cvm_code": "9512",
            },
        ]

    monkeypatch.setattr(fundamentals_ingest, "fetch_all", fake_fetch_all)

    targets = fundamentals_ingest.target_instruments()

    assert "active = true" in captured["query"]
    assert "cnpj is not null" in captured["query"]
    assert set(targets) == {"33.000.167/0001-01"}
    assert targets["33.000.167/0001-01"]["ticker"] == "PETR4"
