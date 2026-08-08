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

**Rate limit real confirmado** (agosto/2026, HTTP 429 com corpo explícito): *"please limit
requests to one every 5 seconds"*. Em teste contra a API real a partir do ambiente de
desenvolvimento, o limite se mostrou **mais rígido que isso na prática** — chamadas isoladas
com 20s e 45s de intervalo ainda retornaram 429, sugerindo limite por IP compartilhado (rede/
sandbox com outros usuários) em vez de "5s por chamada isolada". `config/settings.yaml` usa
`requests_per_second: 0.18` (~1 a cada 5,5s) e o adapter (`sources/news/gdelt_doc.py`) faz
backoff exponencial adicional em 429 (8s a 120s, com jitter) — mas rodando de uma rede
compartilhada, esperar mais que isso pode ser necessário. Validar de novo a partir de uma
conexão pessoal antes de assumir que o throttle configurado é suficiente.

**Formato confirmado contra a API real** (modo `ArtList`, `format=json`):
`{"articles": [{url, url_mobile, title, seendate, socialimage, domain, language,
sourcecountry}]}`. Note a ausência de `tone` — esse campo pertence a outro
modo/produto do GDELT, fora do escopo do MVP (fica `NULL` em `news_articles.tone`,
nunca inventado). `seendate` usa formato compacto próprio (`"20260808T141500Z"`),
não ISO 8601 padrão.

**Validado contra tráfego real** (query com aliases de PETR4, janela de 3 dias, 131
artigos): a query **funciona** — 45/131 títulos mencionavam "Petrobras"/"Petróleo"
explicitamente. Os outros 86 muito provavelmente casaram no **corpo do texto**: a DOC
API busca full-text (título + conteúdo), não só título — GDELT nunca documentou isso
explicitamente, mas o padrão observado (títulos sobre "Dólar e Bolsa", "Cade", etc.
aparecendo numa busca por Petrobras) é consistente com isso, já que essas matérias de
mercado plausivelmente citam a Petrobras no corpo sem o nome aparecer no título. Por
isso o score de relevância (`transforms/news_relevance.py`) usa **título** como sinal
forte e deliberadamente pontua baixo o que só bateu por full-text — sem extrair o texto
completo (fase1.md 32, opcional), não há como confirmar a menção além do título.

**Limitações:** cobertura incompleta e desigual ao longo do tempo; timestamps imprecisos
(`seendate` é quando o crawler viu a página, não necessariamente a publicação);
ausência de resultado ≠ ausência de notícia; relevância de artigos sem o termo no título
fica deliberadamente baixa até haver extração de texto completo ou revisão humana.

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
