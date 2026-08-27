# Limitações

Leia antes de confiar em qualquer número produzido por este sistema. Ser explícito sobre o
que os dados **não** conseguem sustentar é parte do produto, não uma ressalva burocrática.

## Preços

- **`yfinance` não é feed oficial da B3.** É uma biblioteca open source não afiliada ao
  Yahoo, que consome dados destinados a uso pessoal.
- **O Yahoo corrige séries retroativamente.** Um preço baixado hoje pode diferir do mesmo
  preço baixado mês passado. Por isso existe a tabela `data_changes`.
- **`repair=True` altera valores.** É útil, mas o dado reparado não é a fonte original — daí
  a coluna `is_repaired`.
- **Séries ajustadas antigas são frágeis.** Quanto mais para trás, maior a chance de o fator
  de ajuste divergir entre provedores.
- **Preço ajustado ≠ preço negociado.** Relatórios sempre declaram qual série usaram.

## Notícias

- **GDELT não captura toda notícia existente.** Ausência de resultado **não** significa
  ausência de notícia.
- **Cobertura varia com o tempo.** Períodos mais antigos são mais esparsos; comparar volume
  de notícias entre décadas é enganoso.
- **A DOC API não cobre antes de 2017.** Confirmado na Fase 1.1: janelas de 2015 são
  rejeitadas explicitamente (`"Invalid query start date."`, HTTP 200 — não é rate limit).
  `pipelines/news.py` usa `2017-01-01` como corte mínimo. **O ecossistema GDELT tem dado mais
  antigo** — o GKG (Global Knowledge Graph) e os datasets raw cobrem aproximadamente desde
  fevereiro/2015 — mas isso é um produto diferente (BigQuery/arquivos raw, não a DOC API usada
  aqui) e fica fora do escopo do MVP (fase1.md 25 já excluía GKG/BigQuery como dependência
  obrigatória). Se o histórico 2015-2016 vier a ser necessário, a via é essa, não insistir na
  DOC API pra esse período — ela já provou que recusa.
- **Rate limit do GDELT é mais rígido na prática do que o documentado.** O erro 429 real diz
  "uma requisição a cada 5 segundos", mas em teste isolado (sem rajada) esse intervalo não foi
  suficiente — sugere limite por IP compartilhado. Backfills grandes podem precisar rodar bem
  mais devagar que `providers.gdelt.requests_per_second` sozinho garante. Ver docs/sources.md.
- **GDELT busca full-text, não só título** (validado contra tráfego real — ver docs/sources.md).
  Um artigo pode aparecer numa busca por "Petrobras" sem o nome no título porque a empresa é
  citada no corpo. `match_method='query'` sempre grava com `review_status='pending_review'` e
  a relevância (Milestone 7) é calculada a partir do título; sem extração de texto completo
  (opcional, fase1.md 32), não há como confirmar menções que só existem no corpo.
- **Timestamps podem ser imprecisos.** Daí `time_precision` (`exact` / `hour` / `date_only` /
  `unknown`) e a política conservadora de `effective_trade_date`.
- **Conteúdo desaparece.** Portais removem matérias; a extração de texto pode falhar sem que
  isso seja erro do pipeline.
- **Sentimento erra.** A heurística é um ponto de partida, não verdade. `sentiment` e
  `impact_score` são grandezas diferentes e ficam separados de propósito.
- **Republicação não é repercussão.** 50 portais republicando a mesma matéria de agência não
  são 50 eventos — daí os clusters.

## Fundamentos

- **Empresas reapresentam demonstrações.** A versão que existe hoje pode não ser a que o
  mercado via na época. Para análise histórica, use `available_from`, não `reference_date`.
- **`available_from` depende da fonte.** Quando a CVM não informa a data de recebimento, o
  campo fica explicitamente marcado como incerto em vez de estimado.
- **ITR é cumulativo em algumas demonstrações.** Derivar trimestre isolado só é válido para
  contas específicas; onde a regra contábil não se aplica, o valor fica `NULL` com motivo.
- **Métricas dependem do setor.** `net_debt/EBITDA`, `EV/EBITDA` e capex **não se aplicam a
  bancos e seguradoras**. As flags em `instruments` existem para bloquear esse uso, não para
  resolver valuation setorial.
- **Conta ausente vira `NULL` + motivo.** Nunca um zero ou uma estimativa silenciosa.

## Valuation e DCF

- **`margin_of_safety` não é recomendação.** É `(fair_value − preço) / fair_value`, um
  número para o usuário olhar. O sistema não diz "compre" nem "venda".
- **Premissa nunca sai `quality_flag='ok'`.** ΔWC assumido 0 por falta de capital de giro,
  capex de linha combinada, custo de dívida no piso do risk-free, `payout_ratio` default no
  Residual Income — tudo degrada o snapshot para `estimated`, com o `quality_reason`
  nomeando a premissa (`fase2_plan.md` §36).
- **`cost_of_debt` da V1 é custo marginal ESTIMADO por proxy contábil.** A despesa
  financeira da DRE (`3.06.02.01`, ou o nível 2 como fallback) sobre a dívida bruta mede
  **custo histórico / embedded** — o juro dos contratos já existentes — e **não**
  necessariamente o custo corrente de captar dívida nova. Para PETR4 e VALE3 essa proxy dá
  ~6-8% (dívida legada barata), abaixo do risk-free de ~12,4%, então o piso do soberano
  prevalece e `company_credit_spread = 0`. O DCF de não-financeira sai `estimated` enquanto
  for essa proxy. Além disso, empresas declaram a conta de juros puros de forma
  inconsistente: a VALE3 preenche `3.06.02.01` com **zero** e só reporta o nível 2 (tratado
  como ausente, nunca como juro zero).
  - **Evolução futura (não implementada):** hierarquia de fontes para o spread de crédito,
    da melhor para a pior — **bond/emission spread** (spread observado das emissões da
    própria empresa) → **CDS** (credit default swap, quando houver liquidez) → **synthetic
    credit spread** (rating sintético a partir de cobertura de juros, à la Damodaran) →
    **accounting proxy** (o atual). Cada nível grava a fonte e degrada a qualidade conforme
    desce.
- **`terminal_growth` é único para todas as empresas** (nominal, do config). Não há
  diferenciação por maturidade de setor ou exposição a commodity.
- **Beta é histórico** (regressão semanal vs. IBOV), sem ajuste Blume nem beta setorial.
- **Bancos usam RI + DDM, não FCFF.** `payout_ratio` observado quando há `dividends_ttm` e
  lucro positivo; caso contrário assume 0,5 e marca `estimated`.

## Event study

- **Correlação temporal não prova causalidade.** O sistema diz "o evento foi seguido de
  queda de 8%", nunca "a notícia causou queda de 8%".
- **Eventos se sobrepõem.** Balanço e troca de CEO no mesmo dia tornam impossível atribuir a
  reação a um só — daí `is_confounded` e `overlapping_event_count`.
- **`alpha` e `beta` exigem amostra.** Janela curta produz parâmetros instáveis; a flag
  `low_sample` sinaliza em vez de esconder.
- **Horizontes longos censuram.** Um evento de 6 meses atrás não tem D+252. Retorna `NULL`
  com `is_censored = true`, jamais um número extrapolado.
- **Amostra pequena não é padrão.** Toda agregação por tipo de evento mostra o `N`.

## Universo e viés

- **Survivorship bias não está resolvido.** O universo atual só tem empresas vivas. Ações
  deslistadas precisam de tratamento antes de qualquer backtest da Fase 3.
- **Histórico de tickers é incompleto.** A estrutura (`ticker_aliases`) existe, mas não está
  populada com trocas históricas de código.
- **Universo pequeno.** Três empresas escolhidas para exercitar casos distintos
  (commodity, estatal, banco), não para representar o mercado.

## Infraestrutura

- **Sem `DATABASE_URL`, o backend é PostgREST** — mais lento e sem transação multi-tabela.
  Uma falha no meio de uma carga multi-tabela pode deixar estado parcial. O bruto em disco
  permite reprocessar.
- **Projeto pessoal.** Sem alta disponibilidade, sem replicação, sem SLA. Backup é
  responsabilidade manual.

## O que estas fases deliberadamente não fazem

Recomendação de compra ou venda, preço-alvo como conselho, score de atratividade,
carteira, integração com corretora, previsão de preço.

Isso não é escopo pendente — é escopo **recusado**. A base precisa estar comprovadamente
correta antes de qualquer conclusão ser construída sobre ela.

> **Nota sobre DCF e margem de segurança.** A Fase 1 recusava esses cálculos. A Fase 2 os
> introduziu (`valuation_snapshots`, `compute-dcf`), mas como **números para o usuário
> olhar, com qualidade rastreável** — nunca como recomendação. Todo snapshot carrega
> `quality_flag` + `quality_reason`; premissa jamais vira `ok`. Ver a seção
> "Valuation e DCF" acima.
