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

## O que esta fase deliberadamente não faz

Recomendação de compra ou venda, preço-alvo, DCF, margem de segurança, score de
atratividade, carteira, integração com corretora, previsão de preço.

Isso não é escopo pendente — é escopo **recusado** nesta fase. A base precisa estar
comprovadamente correta antes de qualquer conclusão ser construída sobre ela.
