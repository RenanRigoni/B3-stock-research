# Fontes de dados

## yfinance — preços (fonte primária)

- https://ranaroussi.github.io/yfinance/
- https://github.com/ranaroussi/yfinance

Biblioteca **open source, não afiliada ao Yahoo**, que consome dados do Yahoo Finance. A
documentação orienta uso para pesquisa/educação e lembra que os dados do Yahoo se destinam
a uso pessoal — compatível com o escopo deste projeto.

**Símbolos B3:** sufixo `.SA` (`PETR4` → `PETR4.SA`). Benchmark: `^BVSP`.

**Parâmetros fixados** (`config/settings.yaml`):

| Parâmetro | Valor | Por quê |
|---|---|---|
| `auto_adjust` | `false` | OHLC bruto e ajustado precisam conviver separados |
| `actions` | `true` | Captura dividendos e splits |
| `repair` | `true` | Corrige falhas conhecidas, mas registra em `is_repaired` |
| `keepna` | `true` | NaN é sinal de auditoria; normalizar só na camada curada |
| `interval` | `1d` | — |

`end` é **exclusivo** na API do yfinance. Isso é verificado por teste de integração, não
assumido.

Versão testada: **1.5.2** (agosto/2026). Fixada no lock — a API muda entre versões menores.

**Limitações:** não é feed oficial da B3; séries podem ser corrigidas retroativamente;
dividendo e JCP nem sempre vêm diferenciados. Ver [limitations.md](limitations.md).

---

## CVM Dados Abertos — fundamentos (fonte oficial)

- https://dados.cvm.gov.br/
- DFP (anual): https://dados.cvm.gov.br/dataset/cia_aberta-doc-dfp
- ITR (trimestral): https://dados.cvm.gov.br/dataset/cia_aberta-doc-itr
- Cadastro: https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/

Fonte **oficial e gratuita**. Arquivos CSV compactados em ZIP, separados por ano.

O schema é validado contra o arquivo real antes do parse definitivo — encoding, separador e
colunas não são assumidos. Quando o formato muda, o pipeline **falha de forma explícita** e
preserva o bruto, em vez de produzir dado errado em silêncio.

**Mapeamento CVM → ticker** é o problema difícil: resolvido por CNPJ e código CVM (fortes),
com razão social apenas como último recurso e revisão humana. Ver
[config/company_mapping.yaml](../config/company_mapping.yaml).

**Limitações:** reapresentações; data de recebimento nem sempre disponível; ITR cumulativo
em algumas demonstrações.

---

## GDELT DOC 2.0 API — notícias

- Endpoint: `https://api.gdeltproject.org/api/v2/doc/doc`
- https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
- https://blog.gdeltproject.org/gdelt-2-0-our-global-world-in-realtime/

Gratuita, sem token. Acessada por adapter dedicado — chamadas HTTP ao GDELT não se espalham
pelo código.

A DOC API e o GKG/BigQuery são **produtos distintos**. O MVP usa apenas a DOC API. A
interface prevê uma fonte histórica adicional (GKG, público desde 2015), mas o sistema
**não pode depender de BigQuery para funcionar**.

Rate limit conservador: 0.5 req/s.

**Limitações:** cobertura incompleta e desigual ao longo do tempo; timestamps imprecisos;
ausência de resultado ≠ ausência de notícia.

---

## brapi — validação cruzada (opcional)

- https://brapi.dev/docs
- https://brapi.dev/faq/quais-as-limitacoes

Plano gratuito verificado em **agosto/2026**: R$ 0, até 15.000 requisições/mês, 1 ação por
requisição, histórico de **até 3 meses**, cotações com defasagem informada pelo provedor.

Por isso a brapi **não serve como fonte primária de backfill histórico**. É usada apenas
para validar preços recentes e conferir divergências.

Sem `BRAPI_TOKEN`, a validação cruzada é pulada com aviso — o pipeline principal nunca
quebra por causa dela. Divergências **não** são corrigidas automaticamente: viram registro
em `price_validations` para decisão humana.

---

## Licenças e termos

Nenhuma base comercial é copiada. Cada fonte é usada dentro dos seus termos, para pesquisa
pessoal. O bruto preservado em `data/raw/` fica **local e fora do git** — não é
redistribuído.
