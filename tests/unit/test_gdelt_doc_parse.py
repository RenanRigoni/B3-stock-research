"""Regressao para o parse defensivo da resposta do GDELT DOC API.

Fase 1.1: janelas de 2017/2019/2020 ficaram permanentemente presas em
``parse_error`` (retry nunca resolve, o conteudo historico e sempre o mesmo)
porque o GDELT devolve titulos raspados da web com caracteres de controle
brutos nao escapados dentro de valores de string, quebrando ``json.loads``
mesmo com o resto do payload bem-formado.
"""

from stock_research.sources.news.gdelt_doc import _parse_response_json


def test_parse_normal_aceita_json_valido():
    payload = _parse_response_json('{"articles": [{"url": "https://x.test", "title": "Petrobras"}]}')
    assert payload["articles"][0]["title"] == "Petrobras"


def test_parse_repara_caractere_de_controle_bruto_dentro_de_string():
    # \x0b (vertical tab) bruto dentro do valor de "title" -- reproduz o
    # padrao real observado (titulo raspado da web sem escapar o byte).
    raw = '{"articles": [{"url": "https://x.test", "title": "Kay Cuts\x0b: JBS"}]}'
    payload = _parse_response_json(raw)
    assert payload["articles"][0]["title"] == "Kay Cuts: JBS"


def test_parse_repara_escape_invalido_dentro_de_string():
    # "\-" nao e um escape JSON valido -- reproduz payload real de
    # 2020-12-20/26 (PETR4, ingles): titulo "high \- quality subsalt oil find".
    raw = '{"articles": [{"title": "Brazil Petrobras makes high \\- quality find"}]}'
    payload = _parse_response_json(raw)
    assert "quality" in payload["articles"][0]["title"]


def test_parse_json_genuinamente_invalido_ainda_levanta_erro():
    import pytest

    with pytest.raises(RuntimeError):
        _parse_response_json("<html>not json at all</html>")


def test_parse_corpo_vazio_retorna_lista_vazia():
    assert _parse_response_json("   ") == {"articles": []}
