# Metodologia — Event Study

Implementação: [`src/stock_research/analytics/event_study.py`](../src/stock_research/analytics/event_study.py)
(matemática pura, testada) + [`src/stock_research/pipelines/event_study.py`](../src/stock_research/pipelines/event_study.py)
(orquestração).

## Princípio central

O sistema **nunca afirma causalidade**. A saída sempre é da forma "o evento foi seguido por
retorno anormal de -5,2%", nunca "o evento causou uma queda de 5,2%". Correlação temporal
entre notícia e preço não prova causalidade — essa frase está em toda documentação do
projeto de propósito, porque é fácil de esquecer no meio da implementação.

## `effective_trade_date` — pré-requisito

Antes de qualquer retorno ser calculado, o evento precisa de uma data de referência honesta:
o primeiro pregão que **poderia** ter reagido à notícia. Ver
[`docs/sources.md`](sources.md) e `transforms/events.py` para a regra completa
(fase1.md §39-41). Sem isso, qualquer D+N calculado a partir de um dia errado contamina
todo o resto.

## Retorno absoluto por horizonte

Para cada evento, em cada horizonte `h` (número de **pregões**, não dias corridos):

```
h > 0:  R(h) = P(D+h) / P(D0) - 1        (retorno do evento até h pregões depois)
h < 0:  R(h) = P(D0)  / P(D+h) - 1        (retorno de h pregões antes até o evento)
h = 0:  R(0) = 0                          (identidade trivial — mesma data, mesmo preço)
```

A direção do horizonte negativo importa: `R(-20)` mede "quanto a ação se moveu nos 20
pregões **antes** do evento", no sentido do tempo — não o inverso. Isso é o que permite ler
"a ação caiu 18% nos 20 pregões anteriores" (fase1.md §59) da forma que a frase realmente
quer dizer.

Sempre calculado sobre a série **ajustada** (`daily_prices.adj_close`) — dividendos e splits
no meio da janela não podem distorcer o retorno.

Horizonte sem preço disponível (evento recente demais, ou gap de dados) volta
`is_censored = true` e `return_actual = NULL`. Nunca um número extrapolado.

## Retorno excedente simples

```
ExcessReturn(h) = R_ação(h) - R_benchmark(h)
```

Primeira medida de "a ação se moveu mais ou menos que o mercado". Usa o Ibovespa como
benchmark (`instruments.is_benchmark = true`).

## Market model (retorno anormal)

Estimado por regressão linear simples (OLS de um fator) numa **janela de estimação**
anterior ao evento — por padrão `[-252, -30]` pregões (`config/settings.yaml
event_study.estimation_start/estimation_end`):

```
R_ação,t = alpha + beta * R_benchmark,t + epsilon_t
```

Só entram na regressão pares `(t)` onde **os dois** retornos (ação e benchmark) existem —
um par desalinhado enviesaria alpha/beta em silêncio.

A partir de alpha/beta estimados:

```
ExpectedReturn(h) = alpha + beta * R_benchmark(h)
AbnormalReturn(h)  = R_ação(h) - ExpectedReturn(h)
```

`low_sample = true` quando `observations < event_study.min_observations` (padrão 60):
alpha/beta ainda são calculados e retornados — nunca escondidos — mas sinalizados como
estatisticamente fracos. Fica a critério de quem consome a decisão de descartar ou não.

## CAR (Cumulative Abnormal Return)

Soma cumulativa dos retornos anormais, **apenas para horizontes positivos**:

```
CAR(h) = Σ AbnormalReturn(i), para i = 1 até h
```

CAR não é calculado para horizontes pré-evento — acumular "antes" não tem o mesmo
significado econômico que acumular "depois". Se qualquer horizonte no meio da cadeia estiver
censurado, o CAR de todos os horizontes posteriores também fica `NULL` — nunca uma soma
parcial apresentada como se fosse completa.

## Volume e volatilidade

- `volume_ratio_20` / `volume_zscore_20`: reaproveitados de `daily_returns` (calculados no
  Milestone 2) na data do evento.
- `volatility_pre_20` / `volatility_post_20`: desvio padrão populacional dos retornos diários
  ajustados nos 20 pregões antes/depois do evento.

## `data_quality`

- `ok`: nenhum horizonte censurado.
- `partial`: pelo menos um horizonte calculado, pelo menos um censurado.
- `insufficient`: modelo de mercado não pôde ser estimado (menos de 2 observações válidas na
  janela de estimação, ou benchmark sem variância) ou todos os horizontes censurados.

## Confounding (eventos simultâneos)

Calculado em `pipelines/events.py`, não aqui: dois eventos do mesmo instrumento com o mesmo
`effective_trade_date` marcam `overlapping_event_count`/`is_confounded` um no outro
(fase1.md §93). Um event study com `is_confounded = true` não pode ter sua reação atribuída
com confiança a um único evento — releia o evento antes de interpretar o número.

## Limitações desta fase

- Beta é estimado uma única vez por evento (janela fixa `[-252, -30]`), não é um beta
  "corrente" recalculado continuamente.
- Não há correção para autocorrelação ou heterocedasticidade nos resíduos (OLS simples).
- Significância estatística (testes t, bootstrap) não é calculada nesta fase — ver
  fase1.md §92, deixado para as Fases seguintes junto com agregações por categoria de evento
  (fase1.md §90, também fora do escopo da Fase 1).
