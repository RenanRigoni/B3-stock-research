# PROMPT MESTRE — FASE 3

## Universo Histórico + Backtesting + Descoberta e Validação de Estratégias

Estamos iniciando oficialmente a **FASE 3** do projeto.

A Fase 1 construiu a base histórica.
A Fase 2 construiu valuation + qualidade.
Agora a Fase 3 deve responder:

> **Quais combinações de qualidade, valuation, margem de segurança, fundamentos, eventos e contexto histórico realmente tiveram bons resultados de longo prazo quando testadas sem informação futura?**

Esta fase NÃO deve tentar prever o próximo movimento da ação.

O objetivo é descobrir e validar estratégias históricas de longo prazo.

---

# 0. ESTADO ATUAL DO PROJETO

`main` está mergeada e estável.

Estado informado ao fechamento da Fase 2:

```text
460 testes verdes
ruff limpo
mypy limpo
```

Já existem:

```text
companies
instruments
daily_prices
daily_returns
corporate_actions
trading_calendar

cvm_documents
financial_statement_facts
fundamental_metrics

share_count_history
valuation_multiples
quality_scores

risk_free_assumptions
equity_risk_premium_assumptions
wacc_assumptions
valuation_snapshots

news_articles
news_analysis
events
event_studies
```

Fundamentos, TTM e valuation respeitam:

```text
available_from <= as_of_date
```

Essa regra continua sendo ABSOLUTA.

---

# 1. ANTES DE QUALQUER IMPLEMENTAÇÃO

Leia integralmente:

```text
docs/roadmap.md
docs/fase2_plan.md
docs/limitations.md
docs/survivorship_bias_plan.md
data_dictionary.md
```

Examine também:

```text
schema atual
migrations
analytics/
pipelines/
tests/
config/
```

Não redesenhe componentes que já funcionam.

Não reimplemente Fase 1 ou Fase 2.

Qualquer migration nova deve ser:

```text
aditiva
compatível
reversível quando possível
testada
```

---

# 2. HOUSEKEEPING ANTES DA FASE 3

Resolver primeiro:

```text
migration 20260827000006
```

no ledger `schema_migrations`, se ainda estiver pendente.

Isso é housekeeping.

Não misturar esse ajuste com mudanças conceituais da Fase 3.

Registrar no roadmap.

---

# 3. PRINCÍPIO MAIS IMPORTANTE DA FASE 3

Um backtest só pode utilizar aquilo que seria conhecido naquele momento.

Para qualquer data histórica `D`:

```text
universo conhecido em D
preço conhecido em D
fundamento publicado até D
quality score disponível em D
valuation disponível em D
notícia publicada até D
evento conhecido até D
```

Nunca utilizar informação posterior.

Isso inclui também:

```text
empresa que sabemos hoje que sobreviveu
ticker futuro
mudança societária futura
resultado anual ainda não divulgado
reapresentação posterior
delisting futuro
retorno futuro
```

---

# 4. FASE 3.0 — UNIVERSO HISTÓRICO

Essa subfase é obrigatória ANTES de qualquer conclusão séria de backtest.

Objetivo:

> reconstruir quais ações/empresas poderiam realmente ter sido selecionadas em cada data histórica.

---

# 5. SURVIVORSHIP BIAS

Hoje o universo é composto principalmente por empresas atuais.

Isso produz survivorship bias.

Exemplo errado:

```text
pegar empresas que existem em 2026
↓
voltar para 2012
↓
testar estratégia
```

Isso elimina do universo empresas que:

```text
quebraram
foram incorporadas
foram deslistadas
entraram em recuperação
foram adquiridas
deixaram de negociar
```

e artificialmente melhora estratégias.

Portanto:

**NÃO iniciar descoberta de estratégia antes de construir um universo histórico suficientemente confiável.**

---

# 6. INVESTIGAR FONTES PARA UNIVERSO HISTÓRICO B3

Pesquise e valide fontes reais antes de implementar.

Prioridade:

```text
CVM
B3
dados abertos oficiais
```

Investigar:

```text
cadastro histórico de companhias CVM
datas de registro
cancelamento de registro
situação da companhia
histórico de negociação/listagem
ISIN
ticker histórico
classe
eventos societários
```

Se necessário complementar com outras fontes públicas.

Não utilizar Wikipédia como fonte primária.

Não depender de scraping frágil quando houver fonte estruturada.

---

# 7. COMPANY LIFECYCLE

Criar estrutura que permita representar:

```text
company_id
registration_date
listing_start
listing_end
cvm_registration_start
cvm_registration_end
status
delisting_reason
successor_company_id
predecessor_company_id
```

Nomes exatos podem mudar após análise do schema real.

---

# 8. INSTRUMENT LIFECYCLE

Cada ticker/classe deve possuir sua própria vida.

Exemplo:

```text
instrument_id
company_id
ticker
share_class
valid_from
valid_to
listing_start
listing_end
isin
status
```

Não assumir que ticker atual existiu em toda história da companhia.

---

# 9. HISTÓRICO DE TICKERS

Precisamos suportar:

```text
mudança de ticker
mudança de classe
conversão PN → ON
incorporação
cisão
fusão
grupamento
desdobramento
```

O sistema não precisa solucionar todo caso brasileiro já nesta primeira iteração, mas a estrutura precisa comportar.

Nunca reescrever retrospectivamente um ticker antigo como se sempre tivesse tido o ticker atual.

---

# 10. VALE COMO CASO DE TESTE

VALE é importante porque já identificamos mudança histórica real de estrutura acionária.

Utilizar como teste de que:

```text
estrutura por classe em 2010
!=
estrutura por classe em 2026
```

Market cap histórico deve continuar sendo calculado de acordo com a estrutura existente naquele momento.

---

# 11. EMPRESAS DESLISTADAS

Criar capacidade explícita de incluir empresas deslistadas.

Uma empresa que estava disponível em 2014 deve permanecer no universo daquele ano mesmo que não exista em 2026.

Quando preço terminar por:

```text
delisting
incorporação
falência
aquisição
```

o backtest precisa saber diferenciar isso de simplesmente:

```text
missing price
```

---

# 12. TABELA PROPOSTA — `company_lifecycle`

Desenhar e implementar depois de validar fontes:

```text
company_id
valid_from
valid_to
registration_status
listing_status
event_type
event_date
reason
source
available_from
quality_flag
```

---

# 13. TABELA PROPOSTA — `instrument_lifecycle`

```text
instrument_id
valid_from
valid_to
ticker
share_class
trading_status
listing_start
listing_end
source
available_from
quality_flag
```

---

# 14. UNIVERSO POINT-IN-TIME

Criar API/função equivalente a:

```python
get_investable_universe_as_of(date)
```

Resultado:

somente instrumentos que poderiam efetivamente estar disponíveis naquela data.

Nunca:

```text
instrument.valid_from > date
```

Nunca:

```text
listing_start > date
```

E não remover empresa apenas porque sabemos hoje que será deslistada no futuro.

---

# 15. FILTROS DE INVESTIBILIDADE

Não considerar toda empresa negociada automaticamente investível.

Permitir configuração de critérios como:

```text
volume mínimo
número mínimo de pregões
histórico mínimo
preço mínimo
liquidez mínima
dados fundamentalistas disponíveis
```

Não definir valores arbitrários escondidos no código.

Criar configuração versionada:

```text
config/backtest_universe_v1.yaml
```

---

# 16. LIQUIDEZ

Para evitar estratégias que “comprariam” ações praticamente sem negociação:

calcular point-in-time:

```text
average_daily_volume_20
average_daily_volume_60
average_daily_financial_volume_20
average_daily_financial_volume_60
```

Idealmente volume financeiro:

```text
price × volume
```

---

# 17. FASE 3.1 — BACKTEST ENGINE

Depois de o universo histórico estar validado, construir o motor de backtest.

Objetivo:

```text
data histórica
↓
universo elegível
↓
dados disponíveis naquela data
↓
regra da estratégia
↓
seleção
↓
carteira
↓
retorno futuro
↓
benchmark
```

---

# 18. ARQUITETURA

Criar algo próximo de:

```text
analytics/
  backtest/
    engine.py
    universe.py
    portfolio.py
    rebalance.py
    returns.py
    benchmark.py
    costs.py
    metrics.py
    validation.py
```

Evitar arquivo monolítico.

---

# 19. STRATEGY INTERFACE

Criar interface pura.

Exemplo conceitual:

```python
class Strategy:
    def select(self, snapshot):
        ...
```

Uma estratégia recebe somente um snapshot point-in-time.

Ela NÃO recebe preços futuros.

---

# 20. SNAPSHOT HISTÓRICO

Criar estrutura consolidada:

```text
company_id
instrument_id
as_of_date

price
market_cap

quality_score

pe
pb
ev_ebitda
fcf_yield
earnings_yield
dividend_yield

fair_value
margin_of_safety
valuation_method
valuation_quality

roe
roic
net_margin
net_debt_ebitda
fcf
revenue_growth
profit_growth

liquidity
```

Cada valor precisa possuir proveniência temporal correta.

---

# 21. NÃO RECALCULAR O PASSADO COM PREMISSAS FUTURAS

Se existir:

```text
valuation_snapshot 2020
```

ele só pode usar premissas disponíveis em 2020.

Não gerar:

```text
valuation histórico 2020
```

utilizando:

```text
ERP 2026
risk-free 2026
fundamentos reapresentados depois
```

Criar testes específicos.

---

# 22. REBALANCEAMENTO

O motor deve suportar:

```text
mensal
trimestral
semestral
anual
```

Default inicial recomendado para estratégias de longo prazo:

```text
trimestral
```

Mas isso deve estar em config.

---

# 23. EVITAR LOOK-AHEAD NA DATA DE REBALANCEAMENTO

Se rebalanceamento ocorrer em:

```text
2020-05-15
```

só usar documentos com:

```text
available_from <= 2020-05-15
```

O preço de execução deve ser definido claramente.

Exemplo V1:

```text
sinal calculado com fechamento D
execução no fechamento D+1
```

ou:

```text
execução na abertura D+1
```

Escolha uma metodologia e documente.

Não executar retroativamente no mesmo preço que gerou um sinal se isso criar viés.

---

# 24. TRANSACTION COSTS

Mesmo sendo estratégia de longo prazo, incluir custos.

Config:

```text
brokerage
exchange_fees
slippage
taxes_when_applicable
```

Para V1 pessoal podem ser valores pequenos ou zero configurável.

Mas o engine deve suportar.

Nunca esconder custo.

---

# 25. SLIPPAGE

Permitir:

```text
0 bps
5 bps
10 bps
20 bps
```

configurável.

Isso será importante quando universo incluir ações menos líquidas.

---

# 26. DIVIDENDOS

Backtest precisa tratar proventos corretamente.

Preferir retorno total.

Como já existem:

```text
corporate_actions
adjusted prices
```

validar consistência.

Não contar dividendos duas vezes.

Criar teste específico:

```text
adjusted return + dividend cash
```

não pode duplicar provento.

Escolher UM modelo.

---

# 27. SPLITS E GRUPAMENTOS

Portfólio precisa sobreviver a:

```text
split
reverse split
bonus
```

Sem gerar lucro/prejuízo fictício.

---

# 28. DELISTING

Definir metodologia para posição que deixa de negociar.

Não simplesmente apagar a posição.

Investigar evento real.

Quando informação adequada não existir:

```text
quality_flag
```

e política conservadora documentada.

---

# 29. PORTFÓLIO

Suportar inicialmente:

```text
equal_weight
```

Exemplo:

10 empresas:

```text
10% cada
```

Essa deve ser a metodologia padrão V1.

Não implementar otimização Markowitz agora.

---

# 30. NÚMERO DE POSIÇÕES

Config:

```text
top_n
```

Exemplos:

```text
5
10
15
20
```

Estratégias podem também selecionar todas que ultrapassem um threshold.

---

# 31. CASH

Se estratégia encontrar menos empresas que o limite:

não forçar compra de ação ruim.

Permitir caixa.

Exemplo:

```text
top_n = 10
apenas 4 passam nos critérios

25% cada
```

ou:

```text
40% ações
60% cash
```

A regra precisa ser configurável e documentada.

---

# 32. BENCHMARK

Benchmark principal:

```text
IBOV
```

Calcular comparação total.

Opcionalmente depois:

```text
CDI
```

como benchmark de oportunidade.

Não bloquear V1 se CDI ainda não estiver integrado.

---

# 33. MÉTRICAS DE BACKTEST

Obrigatórias:

```text
retorno acumulado
CAGR
volatilidade anualizada
drawdown máximo
tempo de recuperação
Sharpe
Sortino
Calmar
beta
alpha
tracking error
information ratio
turnover
```

Além disso:

```text
número de trades
número de rebalanceamentos
tempo médio em posição
```

---

# 34. HIT RATE

Para análise das escolhas:

```text
% ações positivas em 1 ano
% positivas em 3 anos
% positivas em 5 anos
% que superaram IBOV
```

---

# 35. FORWARD RETURN STUDIES

Além do portfólio tradicional, criar estudo independente:

Para cada sinal histórico:

```text
return_20
return_60
return_120
return_252
return_504
return_756
```

e:

```text
excess_return_vs_ibov
```

Isso permite analisar estratégia sem depender apenas da composição da carteira.

---

# 36. FASE 3.2 — ESTRATÉGIAS

Não comece com machine learning.

Começar com hipóteses claras e interpretáveis.

---

# 37. BASELINE 0 — IBOV

Sempre comparar contra:

```text
buy and hold IBOV
```

---

# 38. BASELINE 1 — EQUAL WEIGHT

Criar estratégia simples:

```text
universo investível
equal weight
rebalanceamento periódico
```

Isso serve para descobrir se nossa estratégia realmente adiciona valor ou apenas captura o mercado.

---

# 39. ESTRATÉGIA QUALITY

Exemplo:

```text
quality_score >= threshold
```

Testar separadamente.

Não incluir valuation ainda.

Objetivo:

> qualidade sozinha produziu retorno superior?

---

# 40. ESTRATÉGIA VALUE

Separadamente:

```text
margin_of_safety >= threshold
```

ou:

```text
P/L abaixo da própria mediana histórica
```

Objetivo:

> valuation sozinho produziu retorno superior?

---

# 41. QUALITY + VALUE

Depois combinar:

```text
quality_score >= X
AND
margin_of_safety >= Y
```

Essa provavelmente será uma das estratégias centrais.

---

# 42. VALUATION RELATIVO À PRÓPRIA HISTÓRIA

Criar sinais como:

```text
P/L atual / mediana P/L 5 anos
EV/EBITDA atual / mediana histórica
P/VP atual / mediana histórica
FCF Yield vs histórico
```

Sempre point-in-time.

Nunca usar mediana de dados futuros.

---

# 43. MARGEM DE SEGURANÇA

Testar thresholds:

```text
> 0%
> 10%
> 20%
> 30%
> 40%
```

Não escolher o melhor depois e declarar vencedor sem validação fora da amostra.

---

# 44. QUALITY THRESHOLDS

Testar:

```text
>= 50
>= 60
>= 70
>= 80
```

Mas lembrar:

```text
quality_nonfinancial_v1
calibration_status = provisional
```

Resultados precisam carregar essa limitação.

---

# 45. NÃO OTIMIZAR THRESHOLDS NO DATASET INTEIRO

Isso seria overfitting.

Separar:

```text
development period
validation period
out-of-sample period
```

---

# 46. SPLIT TEMPORAL

Definir após examinar cobertura real.

Exemplo conceitual:

```text
2011–2018
development

2019–2022
validation

2023–2026
out-of-sample
```

Não usar necessariamente essas datas sem verificar a cobertura.

Escolher regra justificável e registrar antes de olhar os resultados.

---

# 47. WALK-FORWARD

Implementar posteriormente dentro da própria Fase 3 se arquitetura permitir.

Exemplo:

```text
treina/calibra 2011-2016
testa 2017

treina 2011-2017
testa 2018

...
```

Para estratégias baseadas em thresholds simples, "treinar" significa calibrar parâmetros usando apenas passado.

---

# 48. OVERFITTING

Criar mecanismos para detectar:

```text
estratégia excelente em 1 configuração
mas ruim em parâmetros vizinhos
```

Estratégia robusta deve funcionar aproximadamente em:

```text
MoS 20%
MoS 25%
MoS 30%
```

e não apenas:

```text
MoS = 23.71%
```

---

# 49. SENSITIVITY ANALYSIS

Para cada estratégia finalista:

variar:

```text
thresholds
rebalance frequency
top_n
transaction costs
slippage
```

Gerar matriz de resultados.

---

# 50. MÚLTIPLAS HIPÓTESES

Se testarmos 500 estratégias, alguma parecerá boa por sorte.

Registrar:

```text
strategy_id
hypothesis
parameters
created_before_test
```

Evitar alterar hipótese depois de ver resultado e fingir que era original.

---

# 51. STRATEGY REGISTRY

Criar tabela/config:

```text
strategy_id
strategy_name
strategy_version
description
parameters
created_at
hypothesis
status
```

Status:

```text
experimental
validated
rejected
```

Não ter:

```text
best_strategy.py
```

hard-coded.

---

# 52. BACKTEST RUNS

Criar tabela:

```text
backtest_runs
```

Campos conceituais:

```text
run_id
strategy_id
strategy_version

start_date
end_date

universe_version
rebalance_frequency
portfolio_method

transaction_cost_model
slippage_bps

benchmark

code_version
config_hash

started_at
finished_at
status
```

---

# 53. BACKTEST RESULTS

Tabela:

```text
backtest_results
```

Campos:

```text
run_id
cagr
total_return
volatility
max_drawdown
sharpe
sortino
calmar
alpha
beta
turnover
hit_rate
benchmark_return
excess_return
```

---

# 54. PORTFOLIO HISTORY

Tabela:

```text
portfolio_snapshots
```

```text
run_id
date
instrument_id
weight
shares
price
market_value
cash
```

---

# 55. TRADES

Tabela:

```text
backtest_trades
```

```text
run_id
trade_date
instrument_id
side
quantity
price
cost
slippage
reason
```

`reason` é importante.

Exemplo:

```text
entered_quality_value_filter
removed_quality_below_threshold
rebalance
delisting
```

---

# 56. FASE 3.3 — NOTÍCIAS E EVENTOS

Só adicionar notícias DEPOIS de termos baseline de:

```text
quality
value
quality + value
```

Queremos medir se notícia realmente acrescenta informação.

---

# 57. NÃO USAR SENTIMENTO COMO REGRA IMEDIATA

Evitar:

```text
sentimento negativo
→ comprar
```

Isso é simplista.

Criar hipóteses específicas.

---

# 58. HIPÓTESE CENTRAL DE EVENTOS

Uma das perguntas mais importantes do projeto:

> Quando uma empresa de boa qualidade e valuation atrativo sofre uma queda relevante após notícia/evento negativo, mas os fundamentos permanecem preservados, historicamente isso criou oportunidades melhores?

Estruturar teste.

---

# 59. EVENT + QUALITY

Exemplo:

```text
event_negative
AND
quality >= 70
```

Comparar forward returns.

---

# 60. EVENT + VALUE

```text
event_negative
AND
margin_of_safety >= 20%
```

---

# 61. EVENT + QUALITY + VALUE

```text
negative_event
AND
quality >= 70
AND
margin_of_safety >= 20%
```

Essa hipótese deve existir como estratégia separada.

---

# 62. FUNDAMENTOS PRESERVADOS

Precisamos definir objetivamente.

Não escrever:

```text
fundamentos parecem bons
```

Criar critérios.

Exemplo:

```text
ROIC não deteriorou materialmente
margem não colapsou
dívida não explodiu
FCF permanece positivo/normal
receita/lucro sem deterioração estrutural
```

Metodologia precisa ser versionada.

---

# 63. EVENTOS CONFUNDED

Por padrão:

```text
is_confounded = true
```

não deve entrar em análise causal pura.

Pode existir estudo separado.

Nunca misturar sem flag.

---

# 64. AMOSTRA MÍNIMA

Nenhuma estratégia/evento deve ser chamada de padrão robusto com:

```text
N muito pequeno
```

Definir classificação:

```text
insufficient
small
moderate
large
```

Thresholds documentados.

---

# 65. FASE 3.4 — RELATÓRIOS

Criar relatório final por estratégia.

Exemplo:

```text
Strategy:
Quality >= 70
MoS >= 20%

Period:
2012-2026

Rebalance:
Quarterly

Companies:
...

Total return:
CAGR:
IBOV CAGR:

Max drawdown:
IBOV max drawdown:

Alpha:
Sharpe:
Turnover:

Signals:
312

1y positive:
3y positive:
5y positive:

Beat IBOV 3y:
Beat IBOV 5y:
```

---

# 66. EQUITY CURVE

Gerar:

```text
strategy portfolio
IBOV
```

Não precisa dashboard sofisticado.

HTML/PNG/CSV é suficiente.

---

# 67. DRAWDOWN CURVE

Gerar separadamente:

```text
drawdown strategy
drawdown IBOV
```

---

# 68. ROLLING RETURNS

Calcular:

```text
rolling 1y
rolling 3y
rolling 5y
```

Isso é mais informativo para longo prazo do que apenas CAGR final.

---

# 69. DISTRIBUIÇÃO DE RESULTADOS

Mostrar:

```text
percentil 10
25
mediana
75
90
```

para forward returns dos sinais.

Não apenas média.

---

# 70. BEST / WORST CASES

Para cada estratégia:

mostrar:

```text
10 melhores sinais
10 piores sinais
```

Com:

```text
empresa
data
valuation
quality
evento se houver
retorno posterior
```

Isso ajuda a identificar onde a estratégia falha.

---

# 71. EXPLICABILIDADE

O sistema deve sempre conseguir responder:

```text
por que essa empresa entrou na carteira nessa data?
```

Exemplo:

```text
PETR4
2020-04-01

Quality: 76
threshold: >= 70

MoS: 34%
threshold: >= 20%

Liquidity: passed
Universe: eligible

ENTRY
```

---

# 72. NEGATIVE CONTROLS

Criar testes onde não esperamos vantagem.

Exemplo:

```text
seleção aleatória
```

Se nossa estratégia performar igual à aleatória, isso precisa aparecer.

---

# 73. RANDOM PORTFOLIO BASELINE

Opcional mas recomendado:

gerar centenas/milhares de carteiras aleatórias com mesmo:

```text
número de posições
rebalance frequency
universo
```

Comparar percentil da estratégia.

---

# 74. NÃO IMPLEMENTAR MACHINE LEARNING AINDA

Mesmo que pareça tentador.

Fase 3 deve validar primeiro estratégias interpretáveis.

ML pertence à Fase 5.

---

# 75. TESTES OBRIGATÓRIOS

Criar testes para:

```text
universe point-in-time
ticker lifecycle
delisted company inclusion
future company exclusion
look-ahead fundamentals
look-ahead valuation
look-ahead quality
look-ahead ERP
rebalance dates
execution lag
transaction costs
dividends
splits
delisting
portfolio weights
cash
benchmark
forward returns
```

---

# 76. TESTE ANTI-SURVIVORSHIP

Criar fixture:

```text
Company A
existe 2010-2026

Company B
existe 2010-2015 e depois desaparece
```

Backtest em 2013 deve conter:

```text
A + B
```

Backtest em 2020:

```text
somente A
```

Nunca excluir B de 2013 porque sabemos que desapareceu depois.

---

# 77. TESTE ANTI-LOOK-AHEAD DE VALUATION

Exemplo:

```text
rebalance date = 2020-05-15
```

Asserts:

```text
fundamental.available_from <= date
share_count.available_from <= date
ERP.available_from <= date
valuation_snapshot.as_of <= date
quality_score.as_of <= date
```

---

# 78. EXECUTION LAG TEST

Se sinal usa fechamento D:

ordem não pode utilizar preço anterior ao momento do sinal.

Testar explicitamente.

---

# 79. IDEMPOTÊNCIA

Rodar mesmo backtest 2 vezes com:

```text
same data
same strategy
same config
same code
```

deve produzir resultado idêntico.

---

# 80. REPRODUTIBILIDADE

Cada resultado precisa guardar:

```text
strategy version
config hash
code commit
data cutoff
universe version
valuation version
quality version
```

---

# 81. PRIMEIRO UNIVERSO DE TESTE

Antes de expandir toda B3:

usar universo pequeno para validar engine.

Mas:

**não tirar conclusões sobre estratégia usando apenas PETR4/VALE3/ITUB4.**

Elas servem para teste funcional.

---

# 82. EXPANSÃO DO UNIVERSO

Depois do engine funcionar:

expandir gradualmente.

Sugestão:

```text
20 empresas
↓
50 empresas
↓
100+
↓
universo histórico elegível
```

Não tentar ingerir toda história da B3 num único passo sem validar.

---

# 83. PRIORIZAR EMPRESAS COM DADOS CONFIÁVEIS

Na expansão inicial:

priorizar companhias com:

```text
DFP/ITR estruturados
preço histórico confiável
FRE
liquidez
```

---

# 84. BANCOS

`quality_bank_v1` continua:

```text
incomplete
```

Portanto:

não misturar ITUB4 em estratégias que exigem `quality_score >= X` enquanto o score bancário não for comparável ao não-financeiro.

Pode existir:

```text
valuation-only bank strategy
```

se metodologicamente apropriado.

Mas documentar.

---

# 85. QUALITY SCORE PROVISIONAL

`quality_nonfinancial_v1` ainda tem:

```text
calibration_status = provisional
```

Ao aumentar o universo:

analisar distribuição real das métricas.

Não recalibrar silenciosamente.

Se mudar bandas:

```text
quality_nonfinancial_v2
```

e manter V1.

---

# 86. COST OF DEBT ESTIMATED

PETR4 e VALE3 atualmente possuem DCF:

```text
quality_flag = estimated
```

porque cost of debt ainda é proxy contábil.

Backtest precisa decidir se permite:

```text
valuation_quality = estimated
```

Isso deve ser config.

Exemplo:

```yaml
allowed_valuation_quality:
  - ok
  - estimated
```

Resultados devem informar quantos sinais utilizaram cada qualidade.

---

# 87. RESULTADO NÃO DEVE SER “ESTRATÉGIA VENCEDORA”

Classificação sugerida:

```text
REJECTED
INSUFFICIENT_EVIDENCE
PROMISING
ROBUST
```

Uma estratégia só pode chegar a `ROBUST` se:

```text
bom desenvolvimento
bom validation
bom out-of-sample
sensibilidade aceitável
amostra suficiente
sem look-ahead
sem survivorship
custos incluídos
```

---

# 88. ORDEM DE IMPLEMENTAÇÃO

## M0 — Checkpoint

* ledger migration
* branch Fase 3
* baseline dos testes
* docs

## M1 — Universo histórico

* fontes
* company lifecycle
* instrument lifecycle
* delistings
* ticker history

## M2 — Investable universe point-in-time

* `get_investable_universe_as_of`
* liquidez
* eligibility
* testes anti-survivorship

## M3 — Historical snapshot engine

* preço
* fundamentos
* valuation
* quality
* market cap
* point-in-time validation

## M4 — Backtest core

* strategy interface
* rebalance
* execution lag
* portfolio
* cash

## M5 — Returns

* total return
* dividends
* splits
* transaction costs
* benchmark

## M6 — Metrics

* CAGR
* drawdown
* Sharpe
* Sortino
* alpha/beta
* turnover
* rolling returns

## M7 — Persistence

* strategies
* backtest_runs
* portfolio history
* trades
* results

## M8 — Baselines

* IBOV
* equal weight
* random portfolio

## M9 — Quality strategy

* quality isoladamente

## M10 — Value strategy

* valuation isoladamente

## M11 — Quality + Value

* combinação

## M12 — Out-of-sample / walk-forward

* temporal split
* validation
* sensitivity

## M13 — Events/news overlay

* apenas depois das estratégias-base
* event + quality
* event + value
* event + quality + value

## M14 — Expansão do universo

* 20
* 50
* 100+
* revisar qualidade dos dados

## M15 — Auditoria final

* look-ahead
* survivorship
* idempotência
* qualidade
* limites estatísticos

## M16 — Relatório final da Fase 3

---

# 89. REGRA DE PROGRESSÃO

Não pular milestones.

Depois de cada milestone:

```bash
pytest
ruff check .
mypy src
```

Tudo verde antes do próximo.

Bugs encontrados contra dados reais:

```text
documentar
corrigir
criar regression test
```

---

# 90. COMMIT

Fazer commits pequenos e semanticamente claros.

Não guardar toda Fase 3 em um commit gigante.

---

# 91. NÃO MERGEAR AUTOMATICAMENTE

Trabalhar em branch específica.

Sugestão:

```text
fase3-backtesting-engine
```

Pode fazer commits e push da branch durante o desenvolvimento.

**Não fazer merge em `main` sem autorização explícita.**

---

# 92. DOCUMENTAÇÃO

Criar/atualizar:

```text
docs/fase3_plan.md
docs/backtest_methodology.md
docs/historical_universe.md
docs/survivorship_bias_plan.md
docs/strategy_registry.md
docs/limitations.md
docs/roadmap.md
data_dictionary.md
```

---

# 93. FASE 3 NÃO DEVE SER UMA CAIXA PRETA

Para qualquer resultado:

```text
Strategy X CAGR = 17.2%
```

deve ser possível reconstruir:

```text
quais empresas entraram
quando entraram
por que entraram
preço usado
quais dados estavam disponíveis
quando saíram
custos
retorno
```

---

# 94. NÃO MASCARAR AUSÊNCIA DE DADOS

Se algum snapshot histórico não tiver dados suficientes:

```text
NOT_ELIGIBLE_DATA
```

ou status equivalente.

Nunca preencher com dados futuros.

---

# 95. RELATÓRIO FINAL DA FASE 3

Ao final, atualizar `docs/roadmap.md` com:

```text
## Conclusão Fase 3
```

Responder:

### Universo

```text
quantas companies?
quantos instruments?
quantas deslistadas?
cobertura temporal?
survivorship bias resolvido?
limitações restantes?
```

### Engine

```text
rebalanceamentos suportados?
custos?
dividendos?
splits?
delistings?
execution lag?
```

### Estratégias

Para cada estratégia:

```text
strategy_id
hipótese
período
N
CAGR
IBOV CAGR
excess CAGR
max drawdown
Sharpe
alpha
hit rate
turnover
out-of-sample
status
```

### Robustez

```text
sensitivity
walk-forward
random baseline
out-of-sample
```

### Notícias

```text
event overlay melhorou?
piorou?
amostra?
efeito estatisticamente/economicamente relevante?
```

### Qualidade

```text
look-ahead violations
survivorship violations
test count
ruff
mypy
```

---

# 96. DEFINIÇÃO DE PRONTO

A Fase 3 só estará concluída quando pudermos afirmar:

> Em qualquer data histórica do período coberto, o sistema consegue reconstruir o universo investível conhecido naquele momento, selecionar empresas usando somente dados disponíveis naquela data, simular uma carteira com regras realistas e medir seu desempenho posterior sem survivorship bias ou look-ahead bias.

E, além disso:

> As estratégias consideradas interessantes foram avaliadas fora da amostra e comparadas com baselines, custos, parâmetros vizinhos e IBOV.

---

# 97. REGRA FINAL

Não quero que o sistema encontre “a combinação perfeita” olhando todo o passado.

Quero descobrir:

```text
regras simples
explicáveis
economicamente justificáveis
historicamente robustas
que continuem razoáveis fora da amostra
```

Se nenhuma estratégia superar consistentemente os baselines:

**esse também é um resultado válido.**

Não force uma conclusão positiva.

---

# 98. AGORA

Comece pela:

```text
M0 — checkpoint
M1 — universo histórico / survivorship bias
```

Antes de implementar M2 em diante, entregue um relatório curto com:

```text
fontes reais encontradas para universo histórico
cobertura
como serão tratadas empresas deslistadas
como será reconstruído ticker history
schema proposto
riscos/limitações
```

Depois continue milestone por milestone.

Não iniciar Machine Learning.
Não iniciar Fase 4.
Não alterar metodologias da Fase 2 sem necessidade comprovada.
Não fazer merge em `main` sem autorização.
