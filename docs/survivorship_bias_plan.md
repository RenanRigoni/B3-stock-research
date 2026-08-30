# Plano de survivorship bias

Este documento não resolve o problema — registra o que ele é, por que ele existe hoje,
e o que precisa acontecer antes que qualquer backtest (Fase 3) possa ser confiado. Escrito na
Fase 1.1 (fase1.1.md §35): "não é necessário resolver completamente agora, mas quero
preparação séria."

## 1. Por que existe hoje

O universo deste projeto é definido em `config/companies.yaml`: três tickers escolhidos a dedo
(PETR4, VALE3, ITUB4), todos ativos e negociados hoje. `stock-research init` cadastra só o que
está nesse arquivo; nada no pipeline consulta "quais empresas existiam na B3 em 2015" — só
"quais empresas eu decidi acompanhar".

Isso é survivorship bias por construção: qualquer análise histórica feita sobre este universo
só enxerga empresas que **sobreviveram até hoje**. Empresas que faliram, foram incorporadas,
tiveram o registro cancelado pela CVM ou saíram da bolsa no meio do período analisado
simplesmente não existem no dado, mesmo que tenham sido parte real do mercado na época.

## 2. Como isso afeta a Fase 3 (backtesting)

Um backtest rodado sobre um universo sobrevivente superestima retorno sistematicamente: ele
nunca "compra" uma ação que depois vai a zero, porque essa ação nunca esteve na lista para
começar. Quanto mais longo o backtest e mais setores voláteis envolvidos (small caps,
commodities, financeiras alavancadas), maior a distorção.

Qualquer métrica de Fase 3 construída sobre o universo atual — retorno médio de uma estratégia,
taxa de acerto, drawdown — está sujeita a esse viés até este plano ser executado. Isso vale
mesmo que o motor de backtest em si seja implementado corretamente: o problema é o dado de
entrada, não a lógica de simulação.

## 3. Dados necessários

Para reconstruir um universo point-in-time real, faltam:

- **Cadastro histórico completo de companhias abertas da B3/CVM**, incluindo canceladas,
  incorporadas e com registro suspenso — não só `SIT = ATIVO` (o cadastro CVM usado hoje em
  `sources/fundamentals/company_registry.py` já traz esse campo por linha; falta consumi-lo
  para popular canceladas, não só para descartar como ruído de matching).
- **Datas de entrada e saída de cada ativo na composição de índices relevantes** (Ibovespa
  historicamente, IBrX), se o universo de análise for "o que estava no índice em cada data".
- **Histórico de eventos societários que encerram um ticker**: incorporação, fusão, OPA de
  fechamento de capital, cancelamento de registro — com a data efetiva e, quando aplicável,
  o ticker sucessor.
- **Preços até a última data negociada**, não só até hoje — `yfinance` para um ticker
  deslistado geralmente para de retornar dado após o delisting; pode ser necessário um
  provedor alternativo ou arquivo histórico para o trecho final da vida do ativo.
- **`ticker_aliases` populada de verdade.** A tabela já existe (schema desde a Fase 1) mas
  está vazia — é o lugar certo para registrar que um código foi reutilizado ou trocado
  (ex.: reagrupamento, mudança de razão social) sem duplicar o instrumento.

## 4. Como incorporar empresas deslistadas

Abordagem em camadas, sem exigir tudo de uma vez:

1. **Expandir `company_registry` para reter canceladas.** Hoje `_best_name_match` prioriza
   `SIT = ATIVO` e só cai para o restante do cadastro como fallback de match — o dado de
   canceladas já passa pelo pipeline, só não vira instrumento novo. O primeiro passo é decidir
   o critério de inclusão (ex.: esteve negociada em algum momento dentro da janela de análise)
   e cadastrar essas empresas em `instruments` com um campo de status (`active`, já existe;
   falta um `delisted_at`).
2. **Marcar claramente o que é sobrevivente vs. reconstruído.** Nenhuma análise deve tratar os
   dois grupos como equivalentes sem essa informação visível — a proveniência importa tanto
   quanto para `available_from` em fundamentos.
3. **Aceitar cobertura parcial explicitamente.** Nem toda empresa deslistada terá preço diário
   completo disponível numa fonte gratuita. Onde faltar, documentar o buraco (mesmo padrão já
   usado para preços e notícias nesta fase) em vez de fingir cobertura.

## 5. Como reconstruir o universo histórico da B3

Para responder "quais ações compunham o universo investível em 1º de janeiro de 2015", a
reconstrução correta é por **snapshot de índice na data**, não pelo cadastro atual filtrado
retroativamente:

1. Obter a composição histórica do Ibovespa (ou índice equivalente) por período de vigência —
   a B3 publica carteiras teóricas trimestrais.
2. Para cada período de vigência, gravar o conjunto de tickers válidos naquele período —
   estrutura análoga a `trading_calendar`, mas para "universo investível", com `valid_from`/
   `valid_until`.
3. Um backtest point-in-time correto filtra pelo snapshot da data da decisão, nunca pelo
   universo atual.

Esse desenho ainda não está implementado; este documento existe para que a Fase 3 comece a
partir de um plano, não de uma surpresa.

## 6. O que NÃO pode ser feito em backtest antes disso

Enquanto o universo continuar restrito a PETR4/VALE3/ITUB4 (todas sobreviventes):

- Nenhuma taxa de acerto, retorno médio ou drawdown de uma estratégia de seleção de ações pode
  ser generalizado para "o mercado brasileiro" ou para qualquer universo maior que essas três
  empresas.
- Nenhuma comparação de desempenho entre "empresas que sobreviveram" e "o mercado" é válida,
  porque não há contraparte de empresas que não sobreviveram no dado.
- Nenhuma estratégia que dependa de identificar risco de delisting/falência pode ser testada —
  o dado necessário para isso (histórico de empresas que de fato faliram/saíram) não existe
  neste universo.
- Qualquer resultado de backtest da Fase 3 sobre o universo atual deve vir acompanhado de um
  aviso explícito de survivorship bias, não de uma nota de rodapé.

---

## 7. Status na Fase 3 (2026-08-30) — execução iniciada

O desenho conceitual acima passou a ser implementado no **M1 da Fase 3**. Referência
autoritativa das decisões: [`docs/fase3_handoff_v2.md`](fase3_handoff_v2.md) (Handoff v2,
Opus). Registro de execução: [`docs/fase3_plan.md`](fase3_plan.md). Detalhe do universo
histórico: [`docs/historical_universe.md`](historical_universe.md).

Mudança relevante vs. o plano original deste documento: o universo point-in-time V1 **não**
depende de composição histórica de índice (item §5 acima) — usa **existência efetiva de
registro/negociação + filtros de liquidez/dados** (Handoff §11). Composição de índice fica
para o filtro de investibilidade do M2, se necessário.

Regra bitemporal (Handoff §1-§3): elegibilidade histórica é decidida por **tempo efetivo**
(`valid_from`/`valid_to`/`listing_start`/`listing_end`), nunca por tempo de transação
(`source_available_from`/`source_observed_at`/`ingested_at`). A exceção é **enumerada** às
tabelas `company_lifecycle` e `instrument_lifecycle` — todo o resto (fundamentos, valuation,
quality, ERP/risk-free, notícias, eventos) mantém `available_from <= as_of` estrito.
