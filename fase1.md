# PROMPT MESTRE PARA CODEX — FASE 1
## Sistema pessoal de análise histórica de ações da B3
### Escopo: Preços + Notícias + Fundamentos + Event Study

> **Copie este arquivo inteiro para o Codex.**
>
> Este prompt descreve a primeira fase de um sistema pessoal de apoio à análise de ações para investimento de longo prazo.  
> O objetivo NÃO é prever se uma ação “vai subir ou cair”, NÃO é gerar ordem de compra/venda e NÃO é construir ainda um sistema de valuation.  
> A Fase 1 existe para construir uma base histórica confiável, reproduzível e auditável que permita, nas fases posteriores, investigar preço justo, margem de segurança, qualidade, padrões históricos e oportunidades.

---

# 0. SUA MISSÃO

Quero que você atue como engenheiro de software, engenheiro de dados e analista quantitativo responsável por construir **de ponta a ponta a Fase 1** de um sistema pessoal de análise histórica de ações negociadas na B3.

O sistema deve ser desenvolvido com prioridade absoluta para:

1. qualidade dos dados;
2. rastreabilidade;
3. reprodutibilidade;
4. ausência de vazamento de informação futura;
5. tolerância a falhas de fontes externas;
6. idempotência;
7. facilidade de manutenção;
8. arquitetura preparada para as próximas fases;
9. uso local/pessoal;
10. clareza do código e da documentação.

Não pule diretamente para dashboard, IA sofisticada, machine learning ou recomendação de investimento.

A entrega desta fase deve permitir responder perguntas como:

- Qual era o preço de PETR4 em determinada data?
- Qual foi o retorno da ação nos dias, semanas, meses e anos posteriores?
- Quais notícias relevantes sobre a Petrobras apareceram naquele período?
- Em que horário/data a notícia foi publicada?
- O mercado estava aberto ou fechado quando a notícia saiu?
- Qual foi o primeiro pregão que poderia reagir à notícia?
- A ação caiu ou subiu mais do que o Ibovespa?
- A reação durou 1 dia, 5 dias, 20 dias, 60 dias, 1 ano?
- Quais eram os fundamentos conhecidos pelo mercado naquela data?
- O dado fundamentalista utilizado já havia sido publicado naquela data ou estamos acidentalmente usando informação futura?
- Eventos semelhantes aconteceram outras vezes?
- Qual foi a reação mediana das ações a esse tipo de evento?
- Existem notícias duplicadas publicadas por vários portais?
- A notícia parece realmente falar da empresa ou apenas mencionar seu nome?
- O preço utilizado está ajustado ou não ajustado?
- Houve dividendo, JCP, split, grupamento ou outra ação corporativa?
- Existe algum buraco, anomalia ou divergência nos dados?

A resposta a essas perguntas deve vir dos dados armazenados no projeto, e não depender de consultas manuais.

---

# 1. PRINCÍPIO CENTRAL DO PROJETO

A Fase 1 é composta por quatro pilares:

## 1. PREÇOS
Histórico de preços, volume, retornos, benchmark e ações corporativas.

## 2. NOTÍCIAS
Coleta histórica, normalização, deduplicação, relação com empresas, categorização, relevância e sentimento/contexto.

## 3. FUNDAMENTOS
Demonstrações financeiras históricas com prioridade para dados oficiais da CVM e tratamento point-in-time.

## 4. EVENT STUDY
Ligação entre eventos/notícias e a reação posterior da ação, controlando o movimento geral do mercado.

A arquitetura deve fazer esses quatro pilares conversarem entre si.

---

# 2. O QUE NÃO FAZER NESTA FASE

Não implementar agora:

- recomendação automática de compra;
- recomendação automática de venda;
- “preço-alvo” final;
- valuation completo por DCF;
- margem de segurança;
- score final de atratividade;
- carteira automática;
- execução de ordens;
- integração com corretora;
- day trade;
- previsão de candles;
- redes neurais para prever preço;
- machine learning para prever retorno;
- dashboard elaborado;
- aplicativo mobile;
- autenticação;
- multiusuário;
- SaaS;
- pagamento;
- infraestrutura cloud desnecessária.

Pode deixar interfaces, schemas e módulos preparados para expansão futura, mas NÃO desenvolver funcionalidades das Fases 2–5.

---

# 3. STACK PADRÃO

Utilize preferencialmente:

- Python 3.12+
- DuckDB como banco analítico local
- Parquet para armazenamento de datasets intermediários/arquivamento
- Pandas ou Polars conforme fizer mais sentido
- PyArrow
- yfinance
- requests/httpx
- BeautifulSoup apenas quando realmente necessário
- trafilatura apenas como extrator opcional de texto público de páginas
- pydantic para configuração/modelos quando útil
- pydantic-settings ou python-dotenv para configuração
- tenacity para retry/backoff
- typer para CLI
- rich para logs/CLI amigável
- pytest
- ruff
- mypy opcional, mas desejável
- pre-commit opcional, desejável
- Jupyter apenas para exploração e exemplos, nunca como coração do pipeline

Evite criar dependência obrigatória de:

- PostgreSQL;
- Redis;
- Kafka;
- Airflow;
- Docker;
- Kubernetes;
- AWS;
- GCP;
- serviços pagos.

É um projeto pessoal/local.

Se Docker facilitar reprodutibilidade, pode criar `Dockerfile` e `docker-compose.yml` opcionais, mas o projeto deve funcionar nativamente com Python sem Docker.

---

# 4. BANCO DE DADOS

Use **DuckDB** como armazenamento analítico principal.

Motivos:

- projeto local;
- excelente para séries temporais e analytics;
- integração direta com Parquet;
- baixo overhead operacional;
- fácil backup;
- SQL completo;
- facilita migração futura.

Banco padrão:

```text
data/market_history.duckdb
```

Não trate DuckDB como simples arquivo descartável. Defina schema, migrations/versionamento simples e índices/ordenação quando necessário.

Também preserve dados brutos em disco sempre que possível:

```text
data/
├── raw/
├── staging/
├── curated/
├── exports/
└── market_history.duckdb
```

O banco deve conter dados normalizados/curados; dados crus devem ser preservados para auditoria e reprocessamento.

---

# 5. ESTRUTURA DE DIRETÓRIOS

Crie algo próximo de:

```text
stock-research/
├── README.md
├── pyproject.toml
├── uv.lock / requirements.lock
├── .env.example
├── .gitignore
├── Makefile
├── config/
│   ├── settings.yaml
│   ├── companies.yaml
│   ├── news_taxonomy.yaml
│   └── ticker_aliases.yaml
├── data/
│   ├── raw/
│   │   ├── prices/
│   │   ├── news/
│   │   ├── cvm/
│   │   └── reference/
│   ├── staging/
│   ├── curated/
│   ├── exports/
│   └── market_history.duckdb
├── src/
│   └── stock_research/
│       ├── __init__.py
│       ├── cli.py
│       ├── config.py
│       ├── logging.py
│       ├── db/
│       │   ├── connection.py
│       │   ├── schema.sql
│       │   └── migrations/
│       ├── models/
│       ├── sources/
│       │   ├── prices/
│       │   │   ├── base.py
│       │   │   ├── yfinance_source.py
│       │   │   └── brapi_source.py
│       │   ├── news/
│       │   │   ├── base.py
│       │   │   ├── gdelt_doc.py
│       │   │   └── article_extractor.py
│       │   ├── fundamentals/
│       │   │   ├── base.py
│       │   │   ├── cvm_dfp.py
│       │   │   ├── cvm_itr.py
│       │   │   └── company_registry.py
│       │   └── benchmarks/
│       ├── pipelines/
│       │   ├── prices.py
│       │   ├── news.py
│       │   ├── fundamentals.py
│       │   └── events.py
│       ├── transforms/
│       │   ├── prices.py
│       │   ├── news.py
│       │   ├── fundamentals.py
│       │   └── calendar.py
│       ├── analytics/
│       │   ├── returns.py
│       │   ├── abnormal_returns.py
│       │   ├── event_study.py
│       │   └── similarity.py
│       ├── quality/
│       │   ├── checks.py
│       │   └── reports.py
│       └── utils/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/
├── notebooks/
│   └── 01_phase1_validation.ipynb
└── docs/
    ├── architecture.md
    ├── data_dictionary.md
    ├── sources.md
    ├── methodology_event_study.md
    └── limitations.md
```

Se houver uma organização melhor, pode ajustá-la, mas explique no README.

---

# 6. CONFIGURAÇÃO INICIAL DAS EMPRESAS

Não tente começar baixando toda a B3.

Crie uma configuração simples permitindo trabalhar primeiro com um universo pequeno e expandir depois.

Exemplo inicial:

```yaml
companies:
  - ticker: PETR4
    yahoo_symbol: PETR4.SA
    company_name: Petrobras
    aliases:
      - Petrobras
      - Petróleo Brasileiro
      - PETR4
      - PETR3

  - ticker: VALE3
    yahoo_symbol: VALE3.SA
    company_name: Vale
    aliases:
      - Vale
      - Vale S.A.
      - VALE3

  - ticker: ITUB4
    yahoo_symbol: ITUB4.SA
    company_name: Itaú Unibanco
    aliases:
      - Itaú
      - Itaú Unibanco
      - ITUB4
```

O sistema deve suportar posteriormente dezenas/centenas de tickers sem mudar a arquitetura.

Crie também um cadastro mestre de instrumentos.

---

# 7. TABELA `instruments`

Campos mínimos:

```text
instrument_id
ticker
yahoo_symbol
company_name
legal_name
cnpj
cvm_code
isin
asset_type
share_class
sector
subsector
segment
currency
exchange
active
valid_from
valid_to
created_at
updated_at
```

Não assuma que ticker é identificador eterno.

Empresas podem:

- mudar ticker;
- mudar nome;
- incorporar outra empresa;
- sofrer cisão;
- deixar de ser listadas;
- trocar classe;
- reorganizar capital.

Crie suporte para aliases.

---

# 8. TABELA `ticker_aliases`

```text
alias_id
instrument_id
ticker
valid_from
valid_to
source
confidence
```

Isso é importante para evitar survivorship bias e permitir análises históricas futuras.

Mesmo que o MVP não resolva todos os tickers antigos automaticamente, a estrutura deve permitir.

---

# 9. FONTE DE PREÇOS — YFINANCE

## 9.1 Fonte principal do MVP

Use `yfinance` como fonte principal para histórico diário.

Documentação oficial atual:

- https://ranaroussi.github.io/yfinance/
- https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html
- https://github.com/ranaroussi/yfinance

Observações:

- é uma biblioteca open source;
- utiliza dados disponibilizados pelo Yahoo Finance;
- não é afiliada oficialmente ao Yahoo;
- a documentação indica uso para pesquisa/educação e lembra que os dados do Yahoo Finance são destinados a uso pessoal;
- isso é compatível com o objetivo deste projeto, que é pessoal;
- não depender dela como única fonte de verdade sem validação;
- implementar um adapter para permitir troca futura de provedor.

Para ações brasileiras:

```text
PETR4 -> PETR4.SA
VALE3 -> VALE3.SA
ITUB4 -> ITUB4.SA
WEGE3 -> WEGE3.SA
```

Benchmark:

```text
Ibovespa -> ^BVSP
```

---

# 10. CONFIGURAÇÃO DO YFINANCE

Para histórico diário, prefira explicitamente:

```python
yf.download(
    tickers=...,
    start=...,
    end=...,
    interval="1d",
    auto_adjust=False,
    actions=True,
    repair=True,
    keepna=True,
    progress=False
)
```

Antes de fixar a chamada, valide a API real da versão instalada.

Pontos obrigatórios:

1. `auto_adjust=False`
   - precisamos preservar OHLC bruto e preço ajustado separadamente;
   - nunca esconder ajuste dentro dos valores sem rastreabilidade.

2. `actions=True`
   - capturar dividendos e splits quando disponível.

3. `repair=True`
   - utilizar recurso de reparo do yfinance, mas registrar isso em metadata;
   - nunca tratar o dado reparado como se fosse diretamente a fonte original.

4. `keepna=True`
   - ajuda auditoria;
   - remover/normalizar NaNs somente na camada curated.

5. intervalo:
   - `1d`.

6. `start`:
   - inclusivo.

7. `end`:
   - a documentação atual do yfinance informa que é exclusivo;
   - ajustar a data final de forma consciente.

Nunca assumir comportamento de uma biblioteca sem teste.

Crie um teste de integração validando essa semântica.

---

# 11. DADOS DE PREÇO A ARMAZENAR

Tabela:

## `daily_prices`

Campos mínimos:

```text
instrument_id
trade_date
open
high
low
close
adj_close
volume
currency
source
source_symbol
is_repaired
raw_file
ingested_at
```

Chave lógica:

```text
instrument_id + trade_date + source
```

Não use apenas ticker.

---

# 12. PREÇO AJUSTADO X NÃO AJUSTADO

Essa distinção é obrigatória.

Preserve:

- `open`;
- `high`;
- `low`;
- `close`;
- `adj_close`.

Calcule retornos de duas formas quando necessário:

### Price return

Usando fechamento não ajustado.

### Total/adjusted return

Usando série ajustada quando confiável.

Para estudos de longo prazo, dividendos e splits podem alterar drasticamente o resultado.

Não substitua silenciosamente `close` por `adj_close`.

Todo relatório deve dizer qual série foi utilizada.

---

# 13. AÇÕES CORPORATIVAS

Crie tabela:

## `corporate_actions`

```text
action_id
instrument_id
action_date
action_type
value
currency
ratio
source
raw_payload
ingested_at
```

Tipos:

```text
dividend
jcp
split
reverse_split
bonus
subscription
other
```

No yfinance, dividendos e splits podem vir pelas APIs de actions.

Não presuma que o yfinance identifica corretamente JCP separadamente.

Quando a fonte não diferenciar dividendos/JCP, use categoria compatível com a informação realmente disponível.

Nunca invente classificação.

---

# 14. RETORNOS

Crie tabela/materialized dataset:

## `daily_returns`

Campos:

```text
instrument_id
trade_date
close
adj_close
return_1d_price
return_1d_adjusted
log_return_1d
volume
volume_avg_20
volume_ratio_20
benchmark_return_1d
excess_return_1d
created_at
```

Calcular:

```text
retorno simples = P_t / P_t-1 - 1
log return = ln(P_t / P_t-1)
```

Nunca preencher artificialmente retorno em dia sem pregão.

---

# 15. BENCHMARK

Use Ibovespa:

```text
^BVSP
```

Armazene como instrumento próprio ou em tabela de benchmark.

O pipeline precisa sincronizar o benchmark para toda janela histórica analisada.

O Event Study depende dele.

Se em algum período `^BVSP` estiver indisponível, registrar claramente.

---

# 16. CALENDÁRIO DE NEGOCIAÇÃO

Não use calendário civil para D+1, D+5 etc.

Construa `trading_calendar` prioritariamente a partir das datas válidas do benchmark/mercado.

Tabela:

```text
trade_date
is_trading_day
previous_trading_day
next_trading_day
trading_day_index
source
```

Sábado, domingo e feriado não podem contar como D+1.

Uma notícia de sexta à noite deve normalmente ter como primeiro pregão potencialmente afetado a segunda-feira, salvo feriado.

---

# 17. TIMEZONE

Timezone oficial do projeto:

```text
America/Sao_Paulo
```

Armazene timestamps internamente em UTC quando adequado, mas preserve:

```text
published_at_utc
published_at_local
source_timezone
```

Nunca descarte timezone de notícias.

---

# 18. BACKFILL DE PREÇOS

Crie comando:

```bash
stock-research sync-prices --ticker PETR4 --start 2005-01-01
```

E:

```bash
stock-research sync-prices --all --start 2005-01-01
```

Comportamento:

1. buscar instrumento;
2. converter para símbolo Yahoo;
3. consultar yfinance;
4. salvar resposta bruta;
5. normalizar;
6. validar;
7. upsert;
8. recalcular retornos somente para intervalo afetado;
9. gerar relatório de qualidade.

O processo deve ser idempotente.

Executar duas vezes não pode duplicar dados.

---

# 19. ATUALIZAÇÃO INCREMENTAL DE PREÇOS

Crie:

```bash
stock-research update-prices
```

O sistema deve:

1. descobrir última data armazenada por instrumento;
2. buscar somente janela necessária;
3. incluir pequena sobreposição, por exemplo 5–10 pregões;
4. atualizar dados eventualmente corrigidos pelo provedor;
5. registrar diferenças.

Não baixar 20 anos novamente todo dia.

---

# 20. FONTE SECUNDÁRIA — BRAPI

Use Brapi apenas como fonte complementar/validação no plano gratuito.

Documentação:

- https://brapi.dev/docs
- https://brapi.dev/docs/acoes
- https://brapi.dev/docs/acoes/historico
- https://brapi.dev/faq/quais-as-limitacoes

Situação validada em agosto/2026:

Plano gratuito:

- R$ 0;
- até 15.000 requisições/mês;
- 1 ação por requisição;
- histórico de até 3 meses;
- cotações com defasagem informada pelo provedor.

Portanto:

**NÃO usar a Brapi gratuita como fonte principal do backfill histórico.**

Usar para:

- validar preços recentes;
- resolver informações complementares;
- conferir ticker;
- comparar divergências;
- obter dados quando for conveniente.

Configuração:

```env
BRAPI_TOKEN=
```

Se não existir token:

- pipeline principal continua funcionando;
- validação Brapi é ignorada com aviso claro;
- nunca quebrar o sistema.

---

# 21. VALIDAÇÃO CRUZADA DE PREÇOS

Implemente comando:

```bash
stock-research validate-prices --ticker PETR4 --days 60
```

Quando Brapi estiver disponível:

comparar:

```text
trade_date
close_yfinance
close_brapi
difference_abs
difference_pct
status
```

Threshold configurável.

Exemplo:

```yaml
price_validation:
  warning_pct: 0.005
  error_pct: 0.02
```

Não corrigir automaticamente preço divergente sem regra documentada.

Gerar relatório.

---

# 22. CONTROLE DE QUALIDADE DE PREÇOS

Detectar:

- duplicatas;
- datas fora de ordem;
- volume negativo;
- OHLC <= 0;
- high < low;
- high < open/close;
- low > open/close;
- retorno diário absurdo;
- gap muito grande;
- ausência prolongada de dados;
- moedas inesperadas;
- valores 100x maiores/menores;
- alterações históricas após nova sincronização.

Não excluir anomalia silenciosamente.

Classificar:

```text
INFO
WARNING
ERROR
```

---

# 23. NOTÍCIAS — OBJETIVO

A notícia não é um enfeite.

Ela é parte fundamental do sistema.

O objetivo é criar uma base que permita investigar:

```text
notícia/evento
↓
primeiro pregão que poderia reagir
↓
reação da ação
↓
reação relativa ao mercado
↓
persistência/reversão
↓
fundamentos conhecidos na época
```

---

# 24. FONTE DE NOTÍCIAS — GDELT

Fonte gratuita principal:

**GDELT Project**

DOC API:

```text
https://api.gdeltproject.org/api/v2/doc/doc
```

Documentação/referência:

- https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
- https://blog.gdeltproject.org/doc-2-0-updates-1-5-year-searching-and-updated-mobile-interface/
- https://blog.gdeltproject.org/gdelt-2-0-our-global-world-in-realtime/

O GDELT deve ser acessado por adapter próprio.

Não espalhar chamadas HTTP GDELT pelo projeto.

---

# 25. IMPORTANTE SOBRE HISTÓRICO DO GDELT

Não assuma cobertura perfeita.

O GDELT possui diferentes produtos, APIs e datasets.

A arquitetura deve separar:

```text
GDELT DOC API
```

de:

```text
GDELT GKG/raw/BigQuery
```

Para o MVP:

1. implementar primeiro a DOC API;
2. registrar limitações;
3. permitir paginação/janelas de data;
4. não afirmar que ausência de resultado significa ausência de notícia;
5. deixar interface preparada para uma fonte histórica adicional.

Para aprofundamento histórico posterior dentro da própria Fase 1, prever adapter opcional para GDELT 2.0 GKG, disponível desde 2015 em dados públicos/BigQuery/raw feeds.

NÃO faça o sistema depender de BigQuery para funcionar.

---

# 26. QUERY DE NOTÍCIAS

Cada empresa deve possuir aliases.

Exemplo Petrobras:

```text
"Petrobras"
"Petróleo Brasileiro"
"PETR4"
"PETR3"
```

Mas cuidado:

buscar ticker isolado pode gerar ruído.

Crie query builder.

Exemplos conceituais:

```text
("Petrobras" OR "Petróleo Brasileiro")
```

ou:

```text
("Petrobras" OR "PETR4") sourcelang:portuguese
```

Não usar apenas uma query fixa.

Salvar a query utilizada em cada execução.

---

# 27. RAW NEWS

Toda resposta do provedor deve ser arquivada antes da transformação.

Exemplo:

```text
data/raw/news/gdelt/PETR4/2024/05/...
```

Salvar:

- request;
- parâmetros;
- timestamp;
- resposta;
- status HTTP;
- headers úteis;
- versão do parser.

Isso permite reprocessar sem consumir novamente API.

---

# 28. TABELA `news_articles`

Campos mínimos:

```text
article_id
canonical_url
url
domain
source_name
title
language
published_at_utc
published_at_local
seen_at
country
query_used
provider
provider_id
raw_file
ingested_at
```

Se houver:

```text
tone
image_url
source_country
```

Não tornar campos opcionais obrigatórios.

---

# 29. TABELA `news_company_links`

Uma notícia pode afetar várias empresas.

```text
article_id
instrument_id
match_method
relevance_score
match_terms
is_primary_company
review_status
created_at
```

Não colocar `ticker` diretamente como único relacionamento em `news_articles`.

---

# 30. DEDUPLICAÇÃO DE NOTÍCIAS

Isso é obrigatório.

Muitos portais republicam a mesma notícia.

Implementar várias camadas:

### Camada 1 — URL

normalizar:

- http/https;
- `www`;
- parâmetros UTM;
- fragmentos;
- trailing slash.

### Camada 2 — título

normalizar:

- lowercase;
- espaços;
- pontuação;
- acentos apenas quando apropriado;
- prefixos repetitivos.

Calcular hash.

### Camada 3 — similaridade

Comparar títulos próximos temporalmente.

Pode usar:

- RapidFuzz;
- TF-IDF;
- sentence-transformers opcional.

Criar:

```text
duplicate_cluster_id
```

Não apagar cópias.

Escolher artigo canônico e manter todas as ocorrências.

---

# 31. NÃO CONFUNDIR REPETIÇÃO COM IMPORTÂNCIA

Se 50 sites publicarem a mesma matéria de agência:

isso não são 50 eventos independentes.

Preservar:

```text
article_count
unique_domains
duplicate_cluster_size
first_seen
last_seen
```

Esses dados podem inclusive ser sinal de repercussão.

---

# 32. TEXTO COMPLETO DA NOTÍCIA

Não é obrigatório para que a Fase 1 funcione.

Prioridade:

1. título;
2. URL;
3. fonte;
4. data/hora;
5. metadados do GDELT.

Se for tentar extrair texto público:

- usar `trafilatura`;
- respeitar robots/termos quando aplicável;
- definir timeout;
- não burlar paywall;
- não quebrar pipeline quando falhar;
- nunca exigir login;
- armazenar status.

Campos:

```text
article_text
text_extraction_status
text_extracted_at
text_hash
```

Status:

```text
success
unavailable
blocked
paywall
timeout
parse_error
not_attempted
```

---

# 33. ANÁLISE DE NOTÍCIA

Criar tabela:

## `news_analysis`

```text
article_id
instrument_id
category
subcategory
sentiment
sentiment_score
relevance_score
novelty_score
impact_score
is_company_specific
is_macro
is_sector
is_rumor
is_official_source
analysis_method
analysis_model
analysis_version
explanation
analyzed_at
```

Muito importante:

`sentiment` e `impact` são coisas diferentes.

Exemplo:

“Petrobras anuncia investimento de R$ 100 bilhões.”

Pode ser linguisticamente positiva, mas impacto econômico depende do contexto.

Não reduzir toda notícia a positivo/negativo.

---

# 34. TAXONOMIA DE EVENTOS

Criar `config/news_taxonomy.yaml`.

Categorias iniciais:

```text
earnings
guidance
dividend
jcp
capital_allocation
share_buyback
share_issue
merger_acquisition
asset_sale
management_change
ceo_change
board_change
regulation
government_intervention
tax
lawsuit
investigation
corruption
credit_rating
debt
financing
default_risk
production
capacity
operational_incident
accident
environmental
commodity_price
exchange_rate
interest_rate
inflation
macro
sector
competition
contract
customer
supplier
product
technology
labor
strike
political
geopolitical
rumor
analyst_rating
other
```

Não tentar deixar perfeito na primeira execução.

Deve ser versionado e expansível.

---

# 35. CLASSIFICAÇÃO DE NOTÍCIAS — ABORDAGEM

Não faça uma dependência obrigatória de API paga.

Implemente interface:

```python
class NewsClassifier:
    def classify(...)
```

Providers possíveis:

```text
heuristic
local_model
llm_optional
manual
```

Default do MVP:

```text
heuristic + regras + revisão manual de eventos importantes
```

Opcionalmente, se houver modelo local multilíngue adequado, permitir ativação.

Se futuramente houver chave de LLM, permitir provider separado.

Mas:

- nenhuma chave paga deve ser obrigatória;
- sempre armazenar método/modelo/versão;
- nunca misturar classificações de modelos diferentes sem metadata;
- não tratar saída do modelo como verdade.

---

# 36. RELEVÂNCIA DA NOTÍCIA PARA A EMPRESA

Criar score de 0 a 1.

Exemplo de features:

- nome da empresa no título;
- ticker no título;
- quantidade de menções;
- entidade principal;
- domínio;
- similaridade semântica;
- categoria;
- número de empresas mencionadas;
- presença de alias forte.

Classificação sugerida:

```text
>= 0.80 -> alta
0.50–0.79 -> média
< 0.50 -> baixa
```

Threshold configurável.

Não excluir automaticamente notícias de baixa relevância na camada raw.

---

# 37. NOVELTY SCORE

Uma matéria republicada 50 vezes não representa 50 novidades.

Calcule novidade comparando:

- cluster;
- títulos recentes;
- categoria;
- entidades;
- proximidade temporal.

O primeiro artigo do cluster tende a ter maior novidade.

Republicações posteriores recebem menor score.

---

# 38. EVENTOS

Crie tabela:

## `events`

```text
event_id
instrument_id
event_type
event_subtype
event_title
event_description
event_time_utc
event_time_local
event_date
effective_trade_date
time_precision
source_type
source_id
relevance_score
sentiment
impact_score
confidence
created_at
```

---

# 39. `effective_trade_date`

Campo crítico.

Regra conceitual:

### notícia antes/durante o pregão
Pode usar o mesmo pregão como primeiro pregão de reação, dependendo do horário.

### notícia após fechamento
Usar próximo pregão.

### notícia em sábado/domingo/feriado
Usar próximo pregão.

### horário desconhecido
Marcar:

```text
time_precision = date_only
```

Não fingir precisão inexistente.

Para notícias apenas com data:

adotar política documentada e conservadora.

Exemplo:

```text
effective_trade_date = próximo pregão
```

para evitar atribuir movimento anterior à publicação.

---

# 40. HORÁRIO DO MERCADO

Não hard-code regras históricas complexas sem fonte.

Crie configuração para horário regular atual e abstração para ajustes futuros.

Como primeira aproximação:

- determine pregões pelo calendário;
- use timestamp quando disponível;
- se houver dúvida, registre flag `market_session_uncertain`.

Nunca atribuir causalidade a uma notícia que saiu depois do movimento.

---

# 41. AGRUPAMENTO DE NOTÍCIAS EM EVENTOS

Um evento econômico pode gerar dezenas de artigos.

Exemplo:

```text
Petrobras troca CEO
```

Pode haver 100 artigos.

Não criar 100 eventos independentes.

Crie clustering por:

- empresa;
- categoria;
- janela temporal;
- similaridade de título/conteúdo.

Tabela:

## `event_articles`

```text
event_id
article_id
relationship
is_primary
```

---

# 42. FUNDAMENTOS — FONTE PRINCIPAL

Usar prioritariamente dados oficiais da:

**CVM — Comissão de Valores Mobiliários**

Dados Abertos:

```text
https://dados.cvm.gov.br/
```

DFP:

```text
https://dados.cvm.gov.br/dataset/cia_aberta-doc-dfp
```

ITR:

```text
https://dados.cvm.gov.br/dataset/cia_aberta-doc-itr
```

Cadastro de companhias:

```text
https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/
```

A CVM disponibiliza arquivos estruturados, normalmente CSV compactado em ZIP, separados por ano.

---

# 43. DFP

DFP = Demonstrações Financeiras Padronizadas.

Usar para informações anuais.

Implementar pipeline:

```text
download
→ salvar ZIP raw
→ checksum
→ extrair
→ detectar encoding/separador
→ validar schema
→ staging
→ normalização
→ DuckDB
```

Não confiar em parse manual frágil.

---

# 44. ITR

ITR = Informações Trimestrais.

Mesma arquitetura do DFP.

Importante:

dados trimestrais podem ser cumulativos em determinadas demonstrações.

Não transformar automaticamente em “trimestre isolado” sem regra contábil explícita.

Quando necessário, derivar:

```text
Q2 isolado = acumulado 6M - Q1
Q3 isolado = acumulado 9M - acumulado 6M
Q4 = anual - acumulado 9M
```

Somente para contas/demonstrações onde essa lógica é válida.

Documentar.

---

# 45. NÃO HARD-CODE O SCHEMA DA CVM SEM VALIDAR

Antes de escrever parser definitivo:

1. baixar um ZIP real;
2. ler metadata da CVM quando disponível;
3. listar arquivos;
4. listar headers;
5. identificar encoding;
6. identificar tipos;
7. gerar testes.

O código deve tolerar:

- colunas novas;
- ordem diferente;
- anos com pequenas diferenças;
- arquivos atualizados.

Quando schema esperado mudar:

- falhar de forma explícita;
- salvar raw;
- gerar mensagem clara.

Nunca produzir dados errados silenciosamente.

---

# 46. RAW FUNDAMENTALS

Preservar os campos da CVM.

Tabela base:

## `financial_statement_facts`

Campos conceituais:

```text
fact_id
cvm_code
cnpj
instrument_id
document_type
statement_type
reference_date
period_start
period_end
filing_received_at
version
account_code
account_description
value
currency
scale
fiscal_year_order
source_file
source_row_hash
ingested_at
```

Os nomes exatos devem refletir os dados reais da CVM.

Não perder `account_code`.

---

# 47. POINT-IN-TIME — REGRA MAIS IMPORTANTE DOS FUNDAMENTOS

Uma demonstração referente a 31/12/2023 não estava necessariamente disponível ao mercado em 31/12/2023.

Precisamos saber:

```text
data de referência
```

e:

```text
data em que o documento foi entregue/publicado
```

Sempre que a fonte permitir, armazenar a data real de recebimento/publicação.

Em análises históricas:

```text
fundamento disponível em D
=
último documento cuja publicação/recebimento <= D
```

NUNCA:

```text
usar o balanço de 2024 para analisar uma compra em 2023.
```

Crie testes automáticos contra look-ahead.

---

# 48. REAPRESENTAÇÕES

Empresas podem reapresentar demonstrações.

Não sobrescrever história silenciosamente.

Preservar:

- versão;
- data de recebimento;
- situação;
- source hash.

Para análise point-in-time:

usar a versão que estava disponível naquele momento.

Para análise “as-reported-latest”:

pode existir view separada.

---

# 49. MÉTRICAS FUNDAMENTALISTAS DERIVADAS

Na Fase 1, podemos derivar métricas básicas para contextualização histórica.

Exemplos:

```text
revenue
ebit
ebitda (somente se metodologia definida)
net_income
cash
gross_debt
net_debt
equity
operating_cash_flow
capex
free_cash_flow
assets
liabilities
```

Indicadores:

```text
net_margin
roe
roic
net_debt_ebitda
revenue_growth_yoy
net_income_growth_yoy
```

Porém:

- toda fórmula deve ser documentada;
- manter os fatos originais;
- não inventar valor quando conta necessária estiver ausente;
- retornar NULL + motivo;
- bancos e seguradoras exigem métricas diferentes;
- não aplicar `net_debt/EBITDA` cegamente a instituições financeiras.

---

# 50. SETORES ESPECIAIS

Criar flag:

```text
financial_company
utility
commodity_exposed
holding
```

Não precisa solucionar valuation setorial nesta fase.

Serve para impedir uso inadequado de métricas.

---

# 51. TABELA `fundamental_metrics`

```text
instrument_id
reference_date
available_from
period_type
metric_name
metric_value
unit
calculation_version
source_document_ids
quality_flag
created_at
```

`available_from` é obrigatório.

---

# 52. MAPEAMENTO CVM -> TICKER

Esse é um problema real.

Não tente resolver apenas por nome textual.

Usar:

- CNPJ;
- código CVM;
- razão social;
- aliases;
- ISIN quando disponível;
- mapeamento manual versionado.

Arquivo:

```text
config/company_mapping.yaml
```

Exemplo conceitual:

```yaml
PETR4:
  cnpj: "..."
  cvm_code: "..."
  legal_name: "PETRÓLEO BRASILEIRO S.A. - PETROBRAS"
```

O Codex deve preencher valores reais apenas quando obtidos de fonte confiável.

Não inventar CNPJ/código CVM.

---

# 53. EVENT STUDY — OBJETIVO

A finalidade é medir a reação associada a um evento.

Não afirmar:

```text
“a notícia causou queda de 8%”
```

Preferir:

```text
“a notícia foi seguida por queda de 8%”
```

ou:

```text
“o evento esteve associado a retorno anormal de -X%”
```

Correlação temporal não prova causalidade.

---

# 54. JANELAS DO EVENT STUDY

Calcular retornos posteriores:

```text
D0
D+1
D+2
D+5
D+10
D+20
D+60
D+120
D+252
D+504
D+756
```

Equivalências aproximadas:

```text
1 pregão
1 semana
1 mês
3 meses
6 meses
1 ano
2 anos
3 anos
```

Use número de pregões, não dias corridos.

Se não existir horizonte completo:

retornar NULL e:

```text
is_censored = true
```

---

# 55. RETORNO ABSOLUTO

Para cada evento:

```text
R_i(h) = P_i(t+h) / P_i(t) - 1
```

Calcular para:

- preço;
- preço ajustado.

Identificar claramente qual versão está sendo exibida.

---

# 56. RETORNO EXCEDENTE SIMPLES

Primeira medida:

```text
ExcessReturn = Return_stock - Return_IBOV
```

Isso já ajuda a diferenciar:

```text
ação caiu 8%
mercado caiu 7%
```

de:

```text
ação caiu 8%
mercado subiu 1%
```

---

# 57. MARKET MODEL

Implementar opcionalmente, mas ainda dentro da Fase 1:

Janela de estimação sugerida:

```text
[-252, -30]
```

Estimar:

```text
R_stock = alpha + beta * R_market + epsilon
```

Depois:

```text
ExpectedReturn = alpha + beta * R_market
AbnormalReturn = ActualReturn - ExpectedReturn
```

E:

```text
CAR = soma dos abnormal returns
```

O modelo precisa:

- mínimo de observações;
- tratamento de NaN;
- flag de baixa amostra;
- versão metodológica.

---

# 58. TABELA `event_studies`

```text
event_study_id
event_id
instrument_id
effective_trade_date
benchmark_id
method
estimation_window_start
estimation_window_end
observations
alpha
beta
r_squared
return_d0
return_d1
return_d5
return_d20
return_d60
return_d120
return_d252
return_d504
return_d756
excess_d1
excess_d5
excess_d20
excess_d60
excess_d252
car_window_0_1
car_window_0_5
car_window_0_20
data_quality
method_version
calculated_at
```

Pode normalizar horizontes em tabela filha se ficar melhor.

---

# 59. PRE-EVENT WINDOW

Calcular também comportamento anterior:

```text
D-1
D-5
D-20
D-60
```

Isso é essencial.

Uma notícia pode simplesmente confirmar algo que o mercado já vinha precificando.

Exemplo:

```text
ação caiu 18% nos 20 pregões anteriores
notícia oficial sai hoje
ação cai apenas 2% depois
```

Sem janela anterior, a análise seria incompleta.

---

# 60. VOLUME E ANORMALIDADE

Para cada evento, calcular:

```text
volume atual
média 20 dias
mediana 20 dias
volume_ratio_20
zscore_volume
```

Opcionalmente:

```text
volatilidade pré-evento
volatilidade pós-evento
```

Isso ajuda a medir importância.

---

# 61. NOTÍCIA + FUNDAMENTO POINT-IN-TIME

Para cada evento, criar uma visão:

## `event_context`

Que permita recuperar:

```text
evento
preço na data
retornos prévios
retornos posteriores
benchmark
últimos fundamentos disponíveis
data de publicação dos fundamentos
notícias relacionadas
```

Essa view será a base das próximas fases.

---

# 62. NÃO USAR FUNDAMENTOS FUTUROS

Implemente query utilitária:

```python
get_fundamentals_as_of(instrument_id, date)
```

Regra:

```text
available_from <= date
```

ordenar por:

```text
available_from DESC
```

Nunca simplesmente:

```text
reference_date <= date
```

Isso ainda pode introduzir look-ahead.

---

# 63. CONTROLE DE VAZAMENTO FUTURO

Crie suite específica de testes.

Exemplo:

Para evento em:

```text
2023-05-01
```

nenhum registro utilizado na feature histórica pode ter:

```text
available_from > 2023-05-01
```

O teste deve falhar se encontrar.

Isso vale para:

- fundamentos;
- classificações que dependam de informação futura;
- ticker aliases;
- revisões;
- benchmarks.

---

# 64. DATA LINEAGE

Toda tabela derivada deve permitir descobrir:

```text
de qual fonte veio?
quando foi baixada?
qual arquivo raw?
qual versão do código?
qual versão da transformação?
```

Criar tabela:

## `ingestion_runs`

```text
run_id
pipeline
provider
started_at
finished_at
status
records_raw
records_inserted
records_updated
records_rejected
config_hash
code_version
error_message
```

---

# 65. HASH E REPRODUTIBILIDADE

Arquivos raw devem receber SHA256.

Registrar:

```text
file_path
sha256
downloaded_at
source_url
```

Se arquivo da CVM mudar:

detectar.

Não substituir raw histórico sem guardar versão ou hash.

---

# 66. LOGGING

Logs estruturados.

Exemplo:

```text
2026-08-08 10:30 INFO prices PETR4 fetching 2010-01-01..2026-08-08
2026-08-08 10:31 INFO prices PETR4 4120 rows
2026-08-08 10:31 WARNING prices PETR4 2 suspicious gaps
```

Logs em console + arquivo.

Nunca logar token.

---

# 67. RETRY

APIs externas falham.

Implementar:

- timeout;
- retry;
- exponential backoff;
- jitter;
- limite de tentativas;
- tratamento HTTP 429;
- tratamento 500/502/503/504.

Nunca fazer loop infinito.

---

# 68. RATE LIMIT

Criar throttling configurável por provider.

Mesmo APIs sem limite explícito devem ser usadas com respeito.

Config:

```yaml
providers:
  gdelt:
    requests_per_second: ...
  brapi:
    requests_per_second: ...
```

Escolher valores conservadores.

---

# 69. CACHE

Evitar baixar mesma janela repetidamente sem necessidade.

Cache baseado em:

```text
provider
endpoint
query
start
end
parameters_hash
```

Se `--force`:

refazer.

---

# 70. CLI — COMANDOS OBRIGATÓRIOS

Criar CLI clara.

Exemplos:

```bash
stock-research init
```

```bash
stock-research add-company PETR4
```

```bash
stock-research sync-prices --ticker PETR4 --start 2010-01-01
```

```bash
stock-research sync-prices --all --start 2010-01-01
```

```bash
stock-research update-prices
```

```bash
stock-research validate-prices --ticker PETR4
```

```bash
stock-research sync-cvm --year 2025
```

```bash
stock-research sync-cvm --from-year 2010
```

```bash
stock-research sync-news --ticker PETR4 --start 2020-01-01 --end 2020-12-31
```

```bash
stock-research analyze-news --ticker PETR4
```

```bash
stock-research build-events --ticker PETR4
```

```bash
stock-research run-event-study --ticker PETR4
```

```bash
stock-research audit
```

```bash
stock-research status
```

```bash
stock-research pipeline --ticker PETR4 --start 2015-01-01
```

---

# 71. COMANDO `status`

Mostrar algo como:

```text
PETR4

Prices:
2010-01-04 -> 2026-08-07
4,100 pregões
última atualização: ...

News:
2017-01-01 -> 2026-08-08
18,431 registros raw
6,142 clusters
1,380 alta relevância

Fundamentals:
DFP: 2010 -> 2025
ITR: 2011 -> 2026
último documento disponível: ...

Events:
842

Event studies:
831 completos
11 incompletos
```

Valores são apenas exemplo.

---

# 72. COMANDO `audit`

Gerar relatório completo:

```text
data/exports/data_quality_YYYYMMDD.html
```

ou Markdown.

Incluir:

- cobertura temporal;
- missing;
- duplicatas;
- anomalias;
- divergências de preços;
- notícias sem timestamp;
- notícias sem empresa;
- eventos sem preço;
- eventos sem benchmark;
- documentos CVM sem mapeamento;
- gaps;
- look-ahead violations;
- percentuais.

---

# 73. RELATÓRIO DE EMPRESA

Criar export simples:

```bash
stock-research report PETR4
```

Saída:

```text
data/exports/PETR4_phase1_report.html
```

Não precisa dashboard web.

Relatório deve conter:

1. cobertura de preço;
2. gráfico básico;
3. maiores altas;
4. maiores quedas;
5. maiores volumes;
6. eventos relevantes;
7. notícias associadas;
8. retornos posteriores;
9. excesso vs Ibovespa;
10. fundamentos disponíveis nas datas;
11. qualidade dos dados.

O objetivo é validar a engenharia.

---

# 74. EVENT BROWSER

Criar export tabular:

```text
event_date
event_type
title
source
relevance
return_pre_20
return_d1
return_d5
return_d20
return_d60
return_d252
excess_d20
excess_d252
```

Ordenável.

CSV + Parquet.

---

# 75. TESTES UNITÁRIOS

Cobrir obrigatoriamente:

- ticker -> Yahoo symbol;
- parsing yfinance;
- preço ajustado/não ajustado;
- cálculo de retornos;
- D+N por pregões;
- timezone;
- notícia após fechamento;
- notícia em fim de semana;
- URL canonicalization;
- deduplicação;
- classificação heurística;
- parse CVM;
- point-in-time;
- reapresentação;
- market model;
- abnormal returns;
- idempotência.

---

# 76. TESTES DE INTEGRAÇÃO

Criar testes pequenos contra fontes reais, mas marcados:

```text
@pytest.mark.integration
```

Não rodar em toda execução automaticamente.

Testar:

### yfinance

PETR4.SA em uma janela pequena.

### GDELT

query pequena.

### CVM

um arquivo anual pequeno/real.

### Brapi

somente se token/config disponível.

---

# 77. FIXTURES

Salvar fixtures pequenas no repositório para testes offline.

Nunca depender de internet para toda suíte.

---

# 78. TESTE DE SANIDADE — PETR4

Ao final, realizar pipeline completo para PETR4.

Sugestão de janela inicial de validação:

```text
2018-01-01 até hoje
```

Depois testar backfill maior.

Verificar manualmente alguns pontos conhecidos.

Não codificar resultados esperados de mercado sem fonte.

---

# 79. SEGUNDO TESTE — VALE3

Executar também em VALE3.

Importante porque:

- exposição a commodity;
- notícias setoriais;
- eventos operacionais;
- comportamento diferente de Petrobras.

---

# 80. TERCEIRO TESTE — ITUB4

Executar ITUB4 para validar limitações das métricas fundamentalistas de instituições financeiras.

O sistema deve impedir aplicação automática de métricas inadequadas.

---

# 81. CONFIGURAÇÃO `.env.example`

Exemplo:

```env
BRAPI_TOKEN=
OPENAI_API_KEY=
GOOGLE_CLOUD_PROJECT=
DATA_DIR=./data
DUCKDB_PATH=./data/market_history.duckdb
LOG_LEVEL=INFO
```

Mas:

- `OPENAI_API_KEY` opcional;
- `GOOGLE_CLOUD_PROJECT` opcional;
- projeto deve funcionar sem ambos.

---

# 82. CONFIGURAÇÃO `settings.yaml`

Exemplo:

```yaml
project:
  timezone: America/Sao_Paulo

prices:
  primary_provider: yfinance
  benchmark: ^BVSP
  interval: 1d
  auto_adjust: false
  repair: true

news:
  primary_provider: gdelt
  dedup_window_hours: 72

event_study:
  horizons:
    - 1
    - 5
    - 20
    - 60
    - 120
    - 252
    - 504
    - 756
  estimation_start: -252
  estimation_end: -30

quality:
  fail_on_lookahead: true
```

---

# 83. VERSÕES DAS DEPENDÊNCIAS

No início:

1. verificar versão estável atual;
2. testar;
3. gerar lock file.

Para `yfinance`, a documentação/repositório consultados em agosto/2026 mostram linha 1.x ativa.

Não use:

```text
yfinance
```

sem lock em produção local.

Após validar:

fixar a versão resolvida no lockfile.

O README deve registrar versão testada.

---

# 84. README

README deve permitir que alguém com Python instalado faça:

```bash
git clone ...
cd ...
python -m venv .venv
...
stock-research init
stock-research pipeline --ticker PETR4 --start 2018-01-01
```

Explicar:

- objetivo;
- limitações;
- fontes;
- instalação;
- comandos;
- dados gerados;
- metodologia;
- como adicionar empresa;
- como atualizar;
- como reprocessar;
- como fazer backup.

---

# 85. DOCUMENTO `sources.md`

Registrar:

## Yahoo/yfinance

- uso;
- limitações;
- símbolo `.SA`;
- preço ajustado;
- possíveis mudanças de API.

## Brapi

- uso auxiliar;
- necessidade de token;
- limitação do plano gratuito observada no momento da implementação.

## CVM

- oficial;
- DFP;
- ITR;
- periodicidade;
- schema.

## GDELT

- cobertura;
- limitações;
- DOC API;
- GKG opcional.

---

# 86. DOCUMENTO `limitations.md`

Seja explícito.

Exemplos:

- yfinance não é feed oficial da B3;
- Yahoo pode corrigir séries;
- preços antigos podem apresentar ajustes;
- GDELT não captura toda notícia existente;
- timestamp pode ser impreciso;
- conteúdo de matéria pode ficar indisponível;
- classificação de sentimento pode errar;
- relação notícia/preço não prova causalidade;
- CVM pode ter reapresentações;
- ticker history pode estar incompleto;
- métricas contábeis podem depender do setor;
- survivorship bias ainda exige cuidado;
- delisted securities precisam de tratamento posterior;
- Event Study não é previsão.

---

# 87. VERSIONAMENTO DAS TRANSFORMAÇÕES

Cada transformação relevante precisa de versão.

Exemplo:

```text
news_classifier_v1
event_clustering_v1
fundamental_metrics_v1
event_study_v1
```

Se a metodologia mudar:

não misturar silenciosamente resultados antigos e novos.

---

# 88. BANCO — VIEWS ÚTEIS

Criar views:

```text
v_latest_prices
v_price_returns
v_news_with_company
v_canonical_news
v_events
v_event_context
v_fundamentals_as_reported
v_fundamentals_latest_restated
v_event_study_summary
v_data_coverage
```

---

# 89. QUERIES DE VALIDAÇÃO

Criar exemplos SQL em:

```text
docs/example_queries.sql
```

Exemplo:

### maiores quedas PETR4

```sql
SELECT *
FROM daily_returns
WHERE instrument_id = ...
ORDER BY return_1d_adjusted
LIMIT 20;
```

### notícias próximas às maiores quedas

join por janela de data.

### eventos de troca de CEO

filtro por `event_type`.

### retorno mediano D+20 por categoria

agregação.

---

# 90. ANÁLISE POR CATEGORIA DE EVENTO

Criar função:

```python
summarize_event_type(
    ticker="PETR4",
    event_type="management_change"
)
```

Retornar:

```text
n_events
median_d1
median_d5
median_d20
median_d60
median_d252
mean_d20
positive_rate_d20
positive_rate_d252
median_excess_d20
median_excess_d252
```

Sempre mostrar tamanho da amostra.

Não mostrar “padrão” como confiável quando N for pequeno.

---

# 91. INTERVALOS E ROBUSTEZ

Sempre apresentar:

- média;
- mediana;
- desvio;
- quartis;
- mínimo;
- máximo;
- N.

Outliers podem distorcer média.

---

# 92. SIGNIFICÂNCIA ESTATÍSTICA

Pode implementar testes simples, mas sem exagerar.

Exemplos:

- bootstrap de mediana;
- t-test somente quando premissas fizerem sentido;
- intervalo de confiança.

Não concluir automaticamente causalidade/significância econômica.

---

# 93. EVENTOS SOBREPOSTOS

Um grande problema:

duas notícias relevantes podem acontecer em dias próximos.

Adicionar:

```text
overlapping_event_count
```

e flag:

```text
is_confounded
```

Exemplo:

Petrobras divulga balanço e no mesmo dia governo anuncia troca de CEO.

Não atribuir toda reação a apenas uma notícia.

---

# 94. EVENTOS MACRO/SETORIAIS

Mesmo sem construir ainda um pipeline macro completo, notícias podem ser classificadas como:

```text
company_specific
sector
macro
```

Isso é importante.

Se minério cai 10% e todas mineradoras caem:

não interpretar como notícia específica da Vale.

---

# 95. PREPARAÇÃO PARA MACRO FUTURO

Não implementar grande módulo macro agora.

Mas deixe tabela/interface futura:

```text
macro_series
```

para posteriormente incluir:

- Selic;
- CDI;
- IPCA;
- dólar;
- Brent;
- minério;
- PIB;
- desemprego.

Não bloquear a Fase 1 por isso.

---

# 96. NÃO TENTAR “ADIVINHAR” NOTÍCIAS AUSENTES

Se houve movimento de 20% e nenhuma notícia foi encontrada:

registrar:

```text
news_explanation_status = unresolved
```

Não inventar causa.

Pode gerar fila:

```text
manual_review_queue
```

---

# 97. FILA DE REVISÃO MANUAL

Criar view:

```text
v_manual_review_queue
```

Critérios possíveis:

- retorno absoluto > threshold;
- volume > 3x média;
- sem evento associado;
- notícia com relevância ambígua;
- conflito entre classificadores;
- timestamp ausente.

Isso será muito útil para melhorar a base.

---

# 98. MANUAL OVERRIDES

Permitir correção manual sem editar tabela bruta.

Criar:

```text
manual_overrides
```

Exemplo:

```text
entity_type
entity_id
field_name
old_value
new_value
reason
created_at
```

Transformações curated devem respeitar overrides.

---

# 99. AUDITORIA DE ALTERAÇÕES

Se uma execução atualizar um preço histórico:

registrar:

```text
old_value
new_value
provider
detected_at
```

Tabela:

```text
data_changes
```

Especialmente importante para séries ajustadas.

---

# 100. BACKUP

Documentar:

```text
data/market_history.duckdb
data/raw/
config/
```

como itens de backup.

Criar comando opcional:

```bash
stock-research backup
```

Que gere:

```text
backups/YYYYMMDD_HHMM/
```

Sem depender de cloud.

---

# 101. PERFORMANCE

O MVP não precisa ser ultraotimizado.

Mas:

- evitar loops Python por milhões de linhas quando SQL/Polars resolver;
- batch insert;
- Parquet;
- DuckDB;
- não carregar GDELT inteiro em memória;
- processar janelas.

---

# 102. IDEMPOTÊNCIA

Todos os pipelines devem ser reexecutáveis.

Estratégias:

- natural keys;
- hashes;
- upsert;
- merge;
- staging;
- transactions.

Falha no meio não deve corromper banco.

---

# 103. TRANSAÇÕES

Carga:

```text
download
→ raw salvo
→ parse
→ staging
→ validação
→ transaction
→ curated
```

Se curated falhar:

raw continua preservado.

---

# 104. ERROR HANDLING

Erro de uma empresa não deve necessariamente abortar todas.

Exemplo:

```text
PETR4 OK
VALE3 OK
ABCD3 FAILED: ticker unavailable
ITUB4 OK
```

Retorno final deve indicar falhas.

---

# 105. DATA QUALITY SCORE

Pode criar score por dataset:

```text
coverage
completeness
freshness
consistency
cross_source_agreement
```

Mas não esconder os detalhes em um número único.

---

# 106. PRIMEIRA ENTREGA FUNCIONAL

Não tente fazer tudo em um único commit gigante.

Ordem:

## Milestone 1
Estrutura + banco + config + CLI.

## Milestone 2
yfinance + preços + benchmark + corporate actions.

## Milestone 3
retornos + calendário + qualidade.

## Milestone 4
CVM cadastro + DFP + ITR raw/staging.

## Milestone 5
fundamentos normalizados + point-in-time.

## Milestone 6
GDELT raw + notícias normalizadas.

## Milestone 7
deduplicação + company linking.

## Milestone 8
classificação/categorização de notícias.

## Milestone 9
event clustering + effective trade date.

## Milestone 10
Event Study.

## Milestone 11
relatórios + audit.

## Milestone 12
pipeline ponta a ponta + documentação.

Após cada milestone:

- rodar testes;
- atualizar README;
- registrar decisão arquitetural relevante.

---

# 107. NÃO PROSSIGA COM CÓDIGO QUE NÃO RODA

A cada milestone:

```bash
ruff check .
pytest
```

Se usar typing:

```bash
mypy src
```

Corrigir antes de seguir.

---

# 108. CRITÉRIO DE ACEITE — PREÇOS

Considerar concluído quando:

- PETR4.SA é baixada;
- OHLCV armazenado;
- close e adj_close separados;
- corporate actions preservadas;
- ^BVSP armazenado;
- retornos calculados;
- update incremental funciona;
- execução duplicada não duplica;
- auditoria encontra anomalias;
- Brapi opcional não bloqueia.

---

# 109. CRITÉRIO DE ACEITE — NOTÍCIAS

Concluído quando:

- consegue buscar PETR4 por período;
- raw preservado;
- normalização funciona;
- URL canonicalizada;
- duplicatas agrupadas;
- notícia ligada ao instrumento;
- relevância calculada;
- categoria armazenada;
- timestamp tratado;
- pipeline não quebra por artigo inacessível.

---

# 110. CRITÉRIO DE ACEITE — FUNDAMENTOS

Concluído quando:

- consegue baixar DFP real;
- consegue baixar ITR real;
- raw preservado;
- parser validado;
- companhia mapeada por identificador confiável;
- contas armazenadas;
- versão/reapresentação preservada;
- `available_from` disponível ou explicitamente marcado quando fonte não permitir;
- consulta point-in-time funciona;
- teste de look-ahead passa.

---

# 111. CRITÉRIO DE ACEITE — EVENT STUDY

Concluído quando:

dado um evento real:

```text
event_time
instrument
```

o sistema consegue gerar:

```text
effective_trade_date
return_pre_20
return_d1
return_d5
return_d20
return_d60
return_d252
benchmark_return
excess_return
alpha
beta
abnormal_return
CAR
volume_ratio
latest_fundamentals_as_of_event
```

quando os dados existirem.

---

# 112. CRITÉRIO DE ACEITE — PIPELINE TOTAL

Este comando:

```bash
stock-research pipeline --ticker PETR4 --start 2018-01-01
```

deve executar:

```text
prices
benchmark
returns
fundamentals
news
dedup
classification
events
event studies
quality audit
report
```

com resumo final.

---

# 113. OUTPUT FINAL DO PIPELINE

Exemplo:

```text
PHASE 1 COMPLETE — PETR4

Prices:
✓ 2,100 trading days

Benchmark:
✓ IBOV synchronized

Corporate actions:
✓ 84 records

Fundamentals:
✓ DFP
✓ ITR

News:
✓ 8,241 raw
✓ 3,810 canonical
✓ 742 high relevance

Events:
✓ 386

Event studies:
✓ 361 complete
! 25 censored/incomplete

Quality:
✓ no look-ahead violations
! 14 news timestamps uncertain
! 2 historical price anomalies

Report:
data/exports/PETR4_phase1_report.html
```

Os números acima são apenas exemplo.

---

# 114. SEGURANÇA E PRIVACIDADE

Como é local:

- tokens apenas `.env`;
- `.env` no `.gitignore`;
- não enviar dados pessoais;
- não expor servidor publicamente;
- nenhuma necessidade de autenticação nesta fase.

---

# 115. LICENÇAS E TERMOS

Não copie bases comerciais sem autorização.

Documentar uso de cada fonte.

Para yfinance/Yahoo:

registrar no README que:

- yfinance é open source;
- não é afiliado ao Yahoo;
- os dados possuem termos próprios;
- projeto é pessoal/research.

Para GDELT/CVM:

registrar links oficiais.

---

# 116. DISCLAIMER NO SOFTWARE

Adicionar:

```text
Este sistema é uma ferramenta pessoal de pesquisa.
Os resultados não constituem recomendação de investimento.
Dados podem conter erros, atrasos ou lacunas.
Análises históricas não garantem retornos futuros.
```

Não espalhar alertas repetitivos na interface; basta README/relatório.

---

# 117. O QUE SIGNIFICA “FASE 1 PRONTA”

Não significa:

“tem muitos dados”.

Significa:

> conseguimos reconstruir de forma confiável o contexto histórico de uma ação em determinada data utilizando somente as informações que estavam disponíveis naquele momento, relacionar notícias/eventos à evolução posterior do preço e medir a reação da ação de forma absoluta e relativa ao mercado.

Se isso não estiver verdadeiro, a Fase 1 não está pronta.

---

# 118. PERGUNTAS QUE O SISTEMA DEVE CONSEGUIR RESPONDER AO FINAL

Exemplos:

```text
Quais foram as 20 maiores quedas diárias de PETR4 desde 2018?
```

```text
Quais notícias apareceram entre D-1 e D+1 dessas quedas?
```

```text
Qual era o retorno do Ibovespa nesses dias?
```

```text
Qual foi o excesso de retorno de PETR4?
```

```text
Quais eventos foram classificados como troca de gestão?
```

```text
Como PETR4 performou 20, 60 e 252 pregões depois desses eventos?
```

```text
Quais fundamentos estavam disponíveis ao mercado na data?
```

```text
O lucro já estava deteriorando ou a queda ocorreu sem deterioração fundamental observável?
```

```text
Quantas ocorrências semelhantes existem?
```

```text
Qual foi a mediana do retorno posterior?
```

```text
Quantos casos foram positivos?
```

```text
Quais eventos estão contaminados por outros eventos simultâneos?
```

---

# 119. NÃO CRIAR UMA “CAIXA PRETA”

Toda resposta futura precisa ser explicável.

Se o sistema disser:

```text
evento relevante = 0.87
```

deve ser possível descobrir:

```text
por quê?
```

Se disser:

```text
retorno anormal = -5.2%
```

deve mostrar:

```text
retorno da ação
retorno esperado
benchmark
alpha
beta
janela
```

---

# 120. PREPARAÇÃO PARA A FASE 2

A Fase 2 será valuation + qualidade.

Portanto, sem implementá-la ainda, assegure que a Fase 1 deixe disponível:

```text
prices point-in-time
shares/outstanding quando disponível
financial statements
fundamental metrics
corporate actions
historical context
news/events
benchmark
```

Isso permitirá posteriormente calcular:

- múltiplos históricos;
- preço justo;
- DCF;
- margem de segurança;
- qualidade;
- valuation relativo.

Não calcule ainda o score final.

---

# 121. PREPARAÇÃO PARA A FASE 3

A Fase 3 será backtesting.

Portanto:

- não vazar futuro;
- manter ticker history;
- manter versões;
- manter datas de disponibilidade;
- manter delistings quando adicionados;
- permitir snapshot histórico.

Sem isso, backtest futuro será enganoso.

---

# 122. COMO TRABALHAR

Ao iniciar:

1. leia este documento inteiro;
2. apresente rapidamente a arquitetura que pretende adotar;
3. crie backlog/milestones;
4. inicialize projeto;
5. implemente um milestone por vez;
6. teste;
7. não pule etapas;
8. ao detectar limitação de fonte, documente e crie fallback;
9. não invente dados;
10. não esconda erro;
11. prefira dado ausente a dado incorreto;
12. mantenha o projeto executável durante todo o desenvolvimento.

---

# 123. REGRA DE DECISÃO QUANDO HOUVER DÚVIDA

Prioridade:

```text
correção
>
rastreabilidade
>
reprodutibilidade
>
simplicidade
>
performance
>
estética
```

---

# 124. FONTES INICIAIS DE REFERÊNCIA

## yfinance

```text
https://ranaroussi.github.io/yfinance/
https://ranaroussi.github.io/yfinance/reference/api/yfinance.download.html
https://github.com/ranaroussi/yfinance
```

## Brapi

```text
https://brapi.dev/docs
https://brapi.dev/docs/acoes
https://brapi.dev/faq/quais-as-limitacoes
```

## CVM

```text
https://dados.cvm.gov.br/
https://dados.cvm.gov.br/dataset/cia_aberta-doc-dfp
https://dados.cvm.gov.br/dataset/cia_aberta-doc-itr
https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/
```

## GDELT

```text
https://api.gdeltproject.org/api/v2/doc/doc
https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
https://blog.gdeltproject.org/doc-2-0-updates-1-5-year-searching-and-updated-mobile-interface/
https://blog.gdeltproject.org/gdelt-2-0-our-global-world-in-realtime/
```

Antes de implementar integração:

- consultar documentação atual;
- não depender exclusivamente de exemplos antigos;
- adaptar parser ao payload real;
- criar fixture;
- criar teste.

---

# 125. ENTREGA FINAL ESPERADA DO CODEX

Ao terminar a Fase 1, entregar:

```text
1. Código completo
2. Ambiente reproduzível
3. Banco DuckDB
4. Schemas
5. Pipeline de preços
6. Pipeline de notícias
7. Pipeline CVM
8. Event Study
9. CLI
10. Testes
11. Data quality report
12. Relatório PETR4
13. Relatório VALE3
14. Relatório ITUB4
15. README
16. Arquitetura
17. Dicionário de dados
18. Metodologia
19. Limitações
20. Lista de pendências reais
```

E uma conclusão objetiva contendo:

```text
O que está funcionando
O que foi validado
Quais fontes foram utilizadas
Quais limitações permanecem
Quais dados possuem maior risco de inconsistência
Quais partes exigem revisão manual
Se existe qualquer risco de look-ahead
Se a Fase 1 está ou não pronta para servir de base à Fase 2
```

---

# 126. DEFINIÇÃO FINAL

A Fase 1 não deve tentar responder:

> “Devo comprar PETR4 hoje?”

Ela deve construir infraestrutura suficiente para responder com segurança histórica:

> “O que o mercado sabia sobre a Petrobras naquele momento, quanto a ação valia, quais notícias/eventos estavam ocorrendo, como os fundamentos conhecidos se apresentavam e como o preço reagiu posteriormente em relação ao mercado?”

Somente depois dessa base estar comprovadamente correta será iniciada a Fase 2 — qualidade + valuation + preço justo + margem de segurança.

**Não atropele essa progressão.**
