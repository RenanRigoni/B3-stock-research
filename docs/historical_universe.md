# Universo histórico point-in-time (Fase 3, M1)

Reconstrói **quais empresas / instrumentos poderiam realmente ter sido selecionados em cada
data histórica** — a base anti-survivorship de todo backtest da Fase 3.

Referência de decisões: [`docs/fase3_handoff_v2.md`](fase3_handoff_v2.md). Spec: `fase3.md`
§4-16, §75-76. Execução: [`docs/fase3_plan.md`](fase3_plan.md).

---

## 1. Regra bitemporal — gate de elegibilidade por tabela

Duas linhas do tempo, papéis **não intercambiáveis**:

| Linha do tempo | Colunas | Papel |
|---|---|---|
| **Tempo efetivo** | `valid_from`, `valid_to`, `listing_start`, `listing_end` | Reconstrói o universo. Responde "essa empresa/instrumento existia e negociava em D?" |
| **Tempo de transação** | `source_available_from`, `source_observed_at`, `ingested_at` | Proveniência e reprodutibilidade. **Nunca** filtra elegibilidade. |

Por quê: a *existência* de uma companhia negociando em 2013 era **fato público em 2013**. O
pipeline ter baixado o cadastro da CVM em 2026 não torna esse fato desconhecível em 2013.
Contraste: uma DFP de 2015 reapresentada em 2020 **genuinamente não era conhecida** em 2015 —
aí `available_from` é obrigatório e absoluto.

### Gate por tabela (a exceção é ENUMERADA, não um princípio generalizável)

| Tabela | Gate | Por quê |
|---|---|---|
| `company_lifecycle` | **datas efetivas** (`valid_from`/`valid_to`) | metadado estrutural; era público em D |
| `instrument_lifecycle` | **datas efetivas** (`valid_from`/`valid_to`/`listing_*`) | idem |
| `financial_statement_facts` | `available_from <= as_of` | reapresentação é futuro real |
| `cvm_documents` | `available_from <= as_of` | idem |
| `fundamental_metrics` | `available_from <= as_of` | derivado de fato contábil |
| `share_count_history` | `available_from <= as_of` | insumo de market cap = sinal |
| `equity_risk_premium_assumptions` | `available_from <= as_of` | premissa de valuation |
| `risk_free_assumptions` | `available_from <= as_of` | idem |
| `wacc_assumptions` | `available_from <= as_of` | idem |
| `valuation_snapshots` | `as_of_date <= as_of` | sinal derivado |
| `quality_scores` | `as_of_date <= as_of` | sinal derivado |
| `news_articles`, `events` | data de publicação `<= as_of` | sinal |

**Somente as duas tabelas de lifecycle** usam tempo efetivo. Estender essa lista exige
escalar para o Opus (Handoff §16).

### Rename de proveniência como guarda (Handoff §3)

`available_from` significa "o gate point-in-time" em todo o resto do codebase. Nas tabelas de
lifecycle os campos de proveniência são `source_available_from` / `source_observed_at` /
`ingested_at` — o prefixo `source_` / o nome `ingested_at` quebram o reflexo de digitar
`available_from` num `WHERE` de elegibilidade.

---

## 2. Predicado de elegibilidade (contrato)

```text
company_eligible_at(D):
    valid_from <= D
    AND (valid_to IS NULL OR valid_to >= D)

instrument_eligible_at(D):
    valid_from    <= D
    AND (valid_to     IS NULL OR valid_to     >= D)
    AND listing_start <= D
    AND (listing_end  IS NULL OR listing_end  >= D)

# NENHUMA referência a source_available_from / source_observed_at / ingested_at.
# NULL em listing_start / valid_from NUNCA cai no filtro implicitamente:
#   resolver por fallback documentado (quality_flag='estimated')
#   ou marcar NOT_ELIGIBLE_DATA e CONTAR no relatório.
```

`get_investable_universe_as_of(D)` implementa o predicado como **função pura**, espelhando
`analytics/fundamentals.select_point_in_time`. Não materializa `universe_snapshots` em V1.
O retorno **não contém** `valid_to` nem `listing_end` (Handoff §5.3 — não vazar futuro para
a camada de estratégia).

---

## 3. Fontes reais — schema validado contra arquivo (2026-08-30)

### 3.1 CVM cadastro — `cad_cia_aberta.csv`

`https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv` — CSV puro (sem ZIP),
enc **cp1252**, separador `;`. Já em disco: `data/raw/cvm/registry/cad_cia_aberta.csv`.

**Header real (46 colunas):**
`CNPJ_CIA; DENOM_SOCIAL; DENOM_COMERC; DT_REG; DT_CONST; DT_CANCEL; MOTIVO_CANCEL; SIT;
DT_INI_SIT; CD_CVM; SETOR_ATIV; TP_MERC; CATEG_REG; DT_INI_CATEG; SIT_EMISSOR;
DT_INI_SIT_EMISSOR; CONTROLE_ACIONARIO; ...(endereço/contato/auditor)`

**Conteúdo (2677 linhas):** `SIT` = `ATIVO` 757 / `CANCELADA` 1912 / `SUSPENSO(A) - DECISÃO ADM`
8. **1912/1912 canceladas têm `DT_CANCEL`** + `MOTIVO_CANCEL` (texto livre; ver §4).

**Limitação:** é *snapshot do estado atual*. Dá `DT_REG` / `DT_CANCEL` (permite reconstruir o
intervalo efetivo da companhia), mas **não** traz histórico de transições de `SIT`/razão
social, e **não tem ticker, ISIN nem classe**. A verificar em iteração futura: se
`cia_aberta-cad` publica snapshots datados ou `inf_cadastral_*` mensais.

### 3.2 CVM FCA — `fca_cia_aberta_valor_mobiliario_YYYY.csv`

Dataset `cia_aberta-doc-fca`, `https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FCA/DADOS/fca_cia_aberta_YYYY.zip`
— ZIPs anuais **2010→2026**, 9 CSVs por ZIP. Membro relevante enc **cp1252**, sep `;`.

**Header real (byte-idêntico 2010→2023 — 8 anos inspecionados):**
```
CNPJ_Companhia; Data_Referencia; Versao; ID_Documento; Nome_Empresarial;
Valor_Mobiliario; Sigla_Classe_Acao_Preferencial; Classe_Acao_Preferencial;
Codigo_Negociacao; Composicao_BDR_Unit; Mercado;
Sigla_Entidade_Administradora; Entidade_Administradora;
Data_Inicio_Negociacao; Data_Fim_Negociacao;
Segmento; Data_Inicio_Listagem; Data_Fim_Listagem
```

Índice `fca_cia_aberta_YYYY.csv` (enc cp1252): `CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;CD_CVM;
CATEG_DOC;ID_DOC;DT_RECEB;LINK_DOC` — `DT_RECEB` é a proveniência (`source_available_from`),
join por `(CNPJ, Data_Referencia, Versao)` ou por `ID_Documento`↔`ID_DOC`.

**Mapeamento para `instrument_lifecycle`:**

| coluna FCA | coluna lifecycle | grupo |
|---|---|---|
| `Data_Inicio_Negociacao` | `valid_from` (e `listing_start` de `Data_Inicio_Listagem`) | EFETIVA |
| `Data_Fim_Negociacao` | `valid_to` / `trading_status='delisted'` | EFETIVA |
| `Data_Inicio_Listagem` / `Data_Fim_Listagem` | `listing_start` / `listing_end` | EFETIVA |
| `Codigo_Negociacao` | `ticker` (nullable — ver limitação) | atributo |
| `Valor_Mobiliario` + `Sigla_Classe_Acao_Preferencial` | `share_class` (ON/PN/PNA/PNB/UNT) | atributo |
| `Mercado` | `market` (`bolsa` / `balcao_organizado` / `balcao_nao_organizado`) | atributo |
| `Sigla_Entidade_Administradora` | `listing_venue` (BM&FBOVESPA → B3) | atributo |
| `Segmento` | (registrado em `quality_reason`/atributo livre) | atributo |
| `DT_RECEB` (índice) | `source_available_from` | PROVENIÊNCIA |
| `Data_Referencia` (ano do FCA) | `source_reference_year` + `source_observed_at` | PROVENIÊNCIA |

### 3.3 Limitação real descoberta — `Codigo_Negociacao` vazio até 2017

Medido nos arquivos reais:

| ano FCA | linhas | com `Codigo_Negociacao` | linhas de ação | ação com código |
|---|---|---|---|---|
| 2010 | 635 | **0 (0%)** | 425 | 0 |
| 2015 | 658 | **0 (0%)** | 432 | 0 |
| 2017 | 628 | **0 (0%)** | 411 | 0 |
| 2018 | 751 | 477 (64%) | 578 | 469 |
| 2019 | 779 | 534 (69%) | 584 | 524 |
| 2020 | 856 | 552 (64%) | 622 | 549 |
| 2023 | 1011 | 572 (57%) | 626 | 566 |

**A FCA dá classe + mercado + segmento + datas de negociação/listagem desde 2010, mas o
ticker (`Codigo_Negociacao`) só a partir de 2018.** Consequência para a V1 (dentro do
envelope do Handoff §4.2 / §8.5 / §11):

- `instrument_lifecycle.ticker` é **nullable**; `share_class` + `company_id` são os
  discriminadores. As datas efetivas — que são o que o predicado §2 usa — estão completas.
- Linha FCA sem `Codigo_Negociacao` → `ticker = NULL`, `quality_flag = 'incomplete'`,
  `quality_reason` = "FCA nao informa codigo de negociacao antes de 2018".
- Detecção de **mudança de ticker** só é possível 2018+ (onde o código existe) e para o
  universo semeado manualmente. Reportado honestamente, não mascarado.
- Universo de teste (PETR3/PETR4, VALE3, **VALE5**, ITUB3/ITUB4): ticker semeado via
  `instruments` + `ticker_aliases` com `valid_from`/`valid_to` das classes reais.

### 3.4 Caso VALE — estrutura de classes 2012 ≠ pós-2017

- **FCA `valor_mobiliario` da VALE só reporta "Ações Ordinárias" em 2010→2016** — não há
  linha histórica de VALE5/PNA. Em 2017 a linha ON muda `Data_Inicio_Negociacao`
  `2003-12-12 → 2017-12-22` (data real da unificação de classes), mas sem linha PNA com
  `Data_Fim_Negociacao`.
- **A estrutura por classe é provável via `share_count_history`** (FRE, já no banco,
  point-in-time): VALE **PN = 2.108.579.618 ações em 2010–2016 → 12 em 2017**; ON
  3,25 bi → 5,28 bi em 2017.
- V1: `instrument_lifecycle` recebe uma linha semeada de VALE5 PNA com
  `valid_from ≈ 2000` (ou primeira referência), `valid_to = 2017-12-22`,
  `source = 'seed_manual'`, `quality_flag = 'estimated'` — para o backtest histórico
  enxergar a classe PN vigente em 2012. Market cap histórico já soma Σ por classe vigente
  na data (`valuation_multiples`); falta só ter a classe registrada.

### 3.5 Sucessão societária

`MOTIVO_CANCEL` dá o *tipo* do evento mas **não nomeia o sucessor**. `successor_company_id` /
`predecessor_company_id` ficam majoritariamente NULL na V1 (`event_type='cancellation'` +
`reason` sempre gravados; política conservadora). Verificar se o ZIP do FCA tem arquivo de
eventos societários utilizável.

### 3.6 Cobertura histórica

- Piso real: **2010** (preços `yfinance`, CVM DFP/ITR, FCA — todos começam em 2010).
- Notícias / eventos: **2017** (2015 rejeitado pela GDELT DOC API, confirmado na Fase 1.1).
- Split temporal (`fase3.md` §46): **não fixar** antes de medir cobertura ano a ano do
  universo expandido.

---

## 4. `MOTIVO_CANCEL` → categoria de evento

Texto livre da CVM, normalizado para categoria (raw sempre preservado em `reason`):

| padrão no `MOTIVO_CANCEL` | categoria (`event_type` = `cancellation`, sub-motivo) |
|---|---|
| `INCORPORAÇÃO` / `INCORPORAÇÃO DE AÇÕES` / `ELISÃO POR INCORPORAÇÃO` | `incorporation` |
| `CANCELAMENTO VOLUNTÁRIO` / `...INSTRUÇÃO CVM N° 361/02` (OPA) | `voluntary_delisting` |
| `FALÊNCIA` / `LIQUIDAÇÃO` (extrajudicial/judicial) | `bankruptcy_liquidation` |
| `ATENDIMENTO ÀS NORMAS DA INSTR CVM 03/78` e afins | `regulatory` |
| demais | `other` |

Contagens reais por categoria entram no relatório do M1.

---

## 5. Modos de falha silenciosa desta correção (Handoff §5) — regras obrigatórias

1. **`listing_start` / `valid_from` NULL** nunca cai no filtro implicitamente. Ordem de
   resolução: (a) fallback documentado (`company.valid_from`, ou primeira cotação observada)
   com `quality_flag='estimated'`; (b) senão, `NOT_ELIGIBLE_DATA` **contabilizado no
   relatório**. Nunca drop mudo (`fase3.md` §94).
2. **`listing_end` NULL após o instrumento sumir do FCA** → derivar `listing_end` = última
   referência FCA observada (ou última cotação), `quality_flag='estimated'`. Teto de
   fallback: `listing_end IS NULL AND company.valid_to IS NOT NULL` → `listing_end :=
   company.valid_to`, `estimated`.
3. **`valid_to` / `listing_end` = fato futuro** → só respondem "estava vivo em D?"; nunca
   expostos à camada de estratégia. Engine reage no fim **efetivo**, não no anúncio (viés
   conservador, documentado).
4. **Fontes discordam** (cancelamento CVM × fim de negociação FCA) → datas do **instrumento**
   são autoritativas para negociabilidade; `company_lifecycle` dá status/motivo/sucessor e
   teto de fallback. Divergência com ambas presentes → linha em `quality_findings`, mantém a
   data do instrumento, flagged. Nunca `AND` estrito que encolhe o universo em silêncio.

---

## 6. Resultado do M1 (executado 2026-08-30, contra dados reais)

### Ingestão

| | valor |
|---|---|
| `companies` (total após M1) | **2530** (era 3; +2527 do cadastro CVM) |
| `company_lifecycle` linhas | **2566** (2530 companhias + 36 intervalos extras de CNPJs com múltiplos registros) |
| — `registered` | 663 |
| — `canceled` | 1895 |
| — `suspended` | 8 |
| CNPJs com >1 registro no cadastro | 140 (147 linhas extras; 36 viram intervalo distinto) |
| `instrument_lifecycle` linhas | **1448** (1447 `cvm_fca` + 1 `seed_manual`) |
| — com ticker | 778 |
| — sem ticker (FCA pré-2018) | 670 |
| — tickers distintos | 757 |
| — `trading` / `delisted` | 508 / 940 |
| — quality: estimated / ok / inconsistent / incomplete | 868 / 537 / 22 / 21 (**0 `missing_input`**) |
| FCA anos ingeridos | 2010–2026 (17) |
| candidatos FCA → intervalos mesclados | 8595 → 1467 |
| `fallback_valid_from` (data efetiva NULL → `company.valid_from`) | 171 |
| `NOT_ELIGIBLE_DATA` (sem fallback) | **0** |
| `derived_listing_end` (instrumento sumiu do FCA) | 827 |
| `source_disagreement` (cadastro × FCA > 180d) → `quality_findings` | 240 |

### Cobertura temporal

- `company_lifecycle.valid_from`: 1923-05-23 → 2026-07-17. `cvm_cancel_date`: 1984 → 2026.
- `instrument_lifecycle.valid_from`: 1926 → 2026.
- Universo investível (companhias / instrumentos elegíveis) por data:
  2011 → 657 / 570 · 2014 → 646 / ~570 · 2020 → 604 / 494 · 2024 → 692 / 524 · 2026 → 664 / 481.

### Cancelamentos por categoria (`reason_category`)

regulatory 899 · voluntary_delisting 608 · incorporation 291 · other 80 ·
bankruptcy_liquidation 15 · (sem motivo) 2.

### VALE — validação obrigatória (`fase3.md` §10)

`instrument_lifecycle` da VALE (company_id 2):

| classe | ticker | valid_from | valid_to | fonte | flag |
|---|---|---|---|---|---|
| ON | *(NULL — FCA pré-2018)* | 2003-12-12 | 2017-12-31 | cvm_fca | estimated |
| ON | VALE3 | 2017-12-22 | *(vigente)* | cvm_fca | ok |
| PNA | VALE5 | 2000-01-01 | 2017-12-22 | seed_manual | estimated |

`get_investable_universe_as_of`: **2012 → {ON, PNA(VALE5)}** ; **2020/2024 → {ON/VALE3}**.
A estrutura acionária de 2012 é comprovadamente diferente da de 2020+. Corrobora
`share_count_history`: VALE PN = 2.108.579.618 ações (2010–2016) → 12 (2017+).

### Anti-survivorship — validação

`SETIBA PARTICIPAÇÕES S/A` (cancelada 2014-01-08): **presente** no universo de 2013,
**ausente** no de 2016. Conhecer o cancelamento (snapshot CVM baixado em 2026) nunca a
exclui de datas anteriores ao cancelamento efetivo.

### Invariância à proveniência

`test_universe_is_invariant_to_provenance_dates` (fixture) **passa**; e verificado também
contra o **dado real do banco**: deslocar `source_available_from` / `source_observed_at` /
`ingested_at` em ±10 anos → universo idêntico (646 companhias / 968 instrumentos em
2014-06-15, os dois sentidos). `analytics/universe.py` não referencia nenhuma coluna
`source_*` em `WHERE` (só no docstring do contrato).

### Idempotência

`sync-cvm-lifecycle --stage all` rodado 2×: `company_lifecycle` 2566, `instrument_lifecycle`
1448, `companies` 2530 — contagens idênticas.

### Limitações reais do M1 (V1)

1. **`Codigo_Negociacao` da FCA vazio 2010–2017.** 670/1448 linhas de
   `instrument_lifecycle` sem ticker (classe + datas presentes; `quality_flag='incomplete'`).
2. **`valid_from` de ticker é a data de listagem da CLASSE, não do ticker.** Tickers
   sucessivos (SSBR3→ALSO3→ALOS3) chegam todos com o mesmo `Data_Inicio_Negociacao`. O
   universo colapsa para 1 ticker por `(company, classe)` na data usando a regra do
   "intervalo mais apertado" — heurística V1, não a data exata da troca. Precisão de troca
   de ticker: ~1 ano, lado do fim (`derived_listing_end`).
3. **VALE5 / PETR4-PN históricos entram por seed manual**, não por ingestão — a FCA não
   lista a classe PN dessas companhias nos anos antigos. Só VALE5 foi semeada (o caso
   obrigatório); outras classes PN históricas ficam para expansão de universo (M14).
4. **`successor_company_id` / `predecessor_company_id` = NULL** em toda linha. `MOTIVO_CANCEL`
   dá o tipo (`incorporation` etc.) mas não nomeia o sucessor. Curadoria/fonte adicional
   fica para depois.
5. **Universo de instrumentos < universo de companhias** em toda data: nem toda companhia
   registrada tem linha de ação na FCA (ou só tem linha code-less pré-2018 que ganhou
   `listing_end` derivado). O gate **companhia** é o anti-survivorship confiável; o gate
   **instrumento** é parcial no M1.
6. **`cad_cia_aberta.csv` é snapshot** — sem histórico de transições de `SIT`/razão social.
   `DT_REG`/`DT_CANCEL` bastam para o intervalo efetivo, mas mudanças intermediárias de
   status não são reconstruíveis.
7. **20 intervalos FCA descartados** por CNPJ ausente de `companies` (empresas no FCA que
   não estão no cadastro atual — muito antigas/estrangeiras).

---

## 7. Universo INVESTÍVEL (Fase 3 M2)

### 7.1 As duas camadas

```
structural_universe(D)  = companhia/instrumento existia e NEGOCIAVA em D
                          (só TEMPO EFETIVO do lifecycle — §2, inalterado)

investable_universe(D)  = structural
                          + instrumento identificável   (resolution)
                          + série de preço ligada        (price link)
                          + liquidez suficiente          (liquidity)
                          + dados mínimos                (data minimums)
```

Uma companhia em `company_lifecycle` **nunca** vira ativo investível automaticamente. A
sequência é obrigatória e **cada reprovação tem motivo explícito e é contada** — a soma de
elegíveis + reprovados bate sempre com o total estrutural (testado contra dado real em 2013,
2020 e 2024).

Motivos (vocabulário fechado): `NOT_ELIGIBLE_DATA`, `unresolved_instrument`,
`back_projected_instrument`, `no_price_link`, `illiquid`, `insufficient_trading_history`,
`insufficient_data`.

### 7.2 `resolution_status(row, D)` — identificabilidade

**Não é coluna estática** — é função de `(linha, D)`. A mesma linha de `ALOS3`
(`valid_from` 2011, primeira FCA 2023) é `resolved` em 2024 e `back_projected` em 2013.

| status | condição |
|---|---|
| `resolved` | ticker casa o formato B3 **e** `source_reference_year_first <= year(D)` |
| `back_projected` | ticker bem-formado, mas a primeira FCA que o reportou é posterior a D |
| `unresolved_no_code` | `ticker IS NULL` (FCA pré-2018) |
| `unresolved_invalid_code` | ticker presente mas fora do formato (os 82 valores de texto livre) |
| `seeded` | `source='seed_manual'` — curadoria, sempre `estimated` |

Só `resolved` e `seeded` são identificáveis. O sufixo `B` entra no regex de propósito:
`ETRO3B`, `QVQP3B`, `OPSE3B` são códigos reais de Bovespa Mais, não lixo.

`source_reference_year_first` (migração `20260830055948`) é **tempo de transação** e entra
**apenas** na camada investível. Isso **não** é o bug bitemporal da v1: a v1 dizia "o
pipeline baixou em 2026, logo nada é elegível em 2013" (falso — a existência era pública);
aqui é "nenhuma fonte, presente ou passada, diz qual era o ticker em 2013, logo não há como
ligar série de preço" (verdadeiro, conservador, e **medido**).

### 7.3 Colapso de nomenclatura

Granularidade do universo estrutural: **uma entrada por `(company_id, share_class)`**.
`SSBR3` → `ALSO3` → `ALOS3` são a **mesma** ação ordinária da ALLOS; contá-las como três
inflaria `structural_instruments`. A variante escolhida em D é a de melhor
`resolution_status` e, entre `resolved`, a de ticker **observado mais recente que já existia
em D** — nunca o ticker atual retroagido. As demais entram em `naming_variants_collapsed`.

Verificado contra dado real: ALLOS 2020 → `ALSO3` (observado 2019), 2024 → `ALOS3`
(observado 2023), 2013 → `back_projected` (a FCA não publicava código).

### 7.4 `instruments` = identidade, não elegibilidade

688 instrumentos históricos identificados cadastrados no M2 (695 no total). Regras:

- Só `resolution_status` em `{resolved, seeded}`. `NÃO HÁ` / `000000` **nunca** viram
  instrumento.
- Todos `active = false`. **`active` é escopo operacional dos pipelines, não vigência** —
  ligar para `true` faria 688 tickers entrarem em `sync-prices --all` sem autorização.
- `valid_from` / `valid_to` permanecem NULL: vigência é do `instrument_lifecycle`, fonte única.
- Zero colisões de ticker entre companhias (verificado antes do backfill).

---

## 8. `liquidity_metrics` (M2)

Tabela própria, **nunca** `fundamental_metrics`: dado de mercado não tem semântica de
`available_from`, e forçá-la fabricaria um gate falso no ponto exato que a Fase 1/2 protege.

- **Volume financeiro = `close` BRUTO × volume.** `adj_close` é **proibido** — é recalculado
  retroativamente a partir de proventos e splits *futuros* (a Fase 1.1 mediu 81% das linhas
  do PETR4 mudando entre duas leituras da mesma série ajustada). Há teste que varre o AST dos
  módulos e falha se `adj_close` aparecer em qualquer string executável.
- Janelas de **20 e 60 pregões** via `trading_calendar`, nunca dias corridos.
- Média sobre a janela **esperada** (pregão sem negócio = volume zero) — dividir só pelos dias
  negociados superestimaria papel ilíquido. `trading_days_*` × `expected_trading_days_*`
  expõem a esparsidade.
- **Mediana de 60** além da média: um leilão isolado infla a média de 20 dias.
- Só `trade_date <= as_of_date`.

Estado: **24.822 linhas**, 6 instrumentos, 2010-01-04 → 2026-08-26.

---

## 9. Resultado do M2 (executado 2026-08-30, dados reais)

### 9.1 Cobertura por data

| data | struct. co | struct. inst | resolved | c/ preço | investível | unresolved | banda |
|---|---|---|---|---|---|---|---|
| 2010-06-30 | 647 | 574 | 1 | 0 | 0 | 99,8% | **severe** |
| 2011-06-30 | 653 | 577 | 1 | 0 | 0 | 99,8% | **severe** |
| 2012-06-30 | 638 | 568 | 1 | 0 | 0 | 99,8% | **severe** |
| 2013-06-30 | 645 | 586 | 1 | 0 | 0 | 99,8% | **severe** |
| 2014-06-30 | 647 | 588 | 1 | 0 | 0 | 99,8% | **severe** |
| 2015-06-30 | 636 | 562 | 1 | 0 | 0 | 99,8% | **severe** |
| 2016-06-30 | 623 | 557 | 1 | 0 | 0 | 99,8% | **severe** |
| 2017-06-30 | 611 | 546 | 1 | 0 | 0 | 99,8% | **severe** |
| 2018-06-30 | 610 | 531 | 363 | 5 | 5 | 31,6% | high |
| 2019-06-30 | 607 | 507 | 391 | 5 | 5 | 22,9% | moderate |
| 2020-06-30 | 601 | 491 | 402 | 5 | 5 | 18,1% | moderate |
| 2021-06-30 | 674 | 540 | 436 | 5 | 5 | 19,3% | moderate |
| 2022-06-30 | 720 | 551 | 448 | 5 | 5 | 18,7% | moderate |
| 2023-06-30 | 691 | 538 | 444 | 5 | 5 | 17,5% | moderate |
| 2024-06-30 | 692 | 518 | 436 | 5 | 5 | 15,8% | moderate |
| 2025-06-30 | 685 | 503 | 425 | 5 | 5 | 15,5% | moderate |
| 2026-06-30 | 664 | 478 | 403 | 5 | 5 | 15,7% | moderate |

Motivos de inelegibilidade (soma das 17 datas): `no_price_link` 3.711 ·
`back_projected_instrument` 3.413 · `unresolved_instrument` 2.046 · `NOT_ELIGIBLE_DATA` 0 ·
`illiquid` 0 · `insufficient_trading_history` 0 · `insufficient_data` 0.

### 9.2 A descontinuidade de 2018 e o gate dominante

**Duas barreiras distintas, em ordem de severidade:**

1. **2010–2017: identificação.** A FCA não publica `Codigo_Negociacao` antes de 2018 →
   `unresolved_rate` de **99,8%** e universo investível **vazio**, mesmo para PETR4/VALE3/ITUB4,
   que têm preço e fundamentos completos desde 2010. Não é falta de dado de mercado — é
   impossibilidade de dizer *qual papel* cada linha do lifecycle era.
2. **2018+: cobertura de preço.** A identificação melhora (363→448 resolvidos), mas
   `daily_prices` cobre **5 ações de 694** (0,7%). `no_price_link` reprova ~430 instrumentos
   por data. O universo investível é de **5 papéis** (PETR3, PETR4, VALE3, ITUB3, ITUB4).

**Os gates de liquidez e dados mínimos nunca chegam a reprovar ninguém** — tudo já foi
barrado antes. Por isso os limiares são, hoje, irrelevantes para o tamanho do universo.

### 9.3 Sensibilidade de limiares (para a decisão do Opus)

`investable_instruments` por limiar de `min_avg_financial_volume_60`:

| limiar | 2013 | 2018 | 2020 | 2024 | 2026 |
|---|---|---|---|---|---|
| sem limiar | 0 | 5 | 5 | 5 | 5 |
| R$ 1 M | 0 | 5 | 5 | 5 | 5 |
| R$ 5 M | 0 | 4 | 5 | 5 | 5 |
| R$ 10 M | 0 | 4 | 5 | 5 | 5 |
| R$ 50 M | 0 | 4 | 4 | 4 | 5 |
| R$ 100 M | 0 | 4 | 4 | 4 | 5 |
| R$ 500 M | 0 | 3 | 3 | 3 | 4 |
| R$ 1.000 M | 0 | 1 | 3 | 2 | 3 |

Por `min_trading_days_60`: 5 investíveis para qualquer limiar de 1 a 55; cai para 4 em 2024
com limiar 60 (VALE3 teve 59 pregões com negócio na janela).

Distribuição real de `avg_financial_volume_60` (BRL/pregão), 2026-06-30: PETR4 2.091 M ·
VALE3 1.527 M · ITUB4 1.230 M · PETR3 618 M · ITUB3 159 M. Em 2013: PETR4 572 M ·
ITUB4 342 M · PETR3 168 M · VALE3 168 M · **ITUB3 6,2 M** (o único que um limiar de
R$ 10 M excluiria).

**Nenhum limiar foi escolhido.** `config/backtest_universe_v1.yaml` mantém
`status: awaiting_opus_thresholds` e `null` em todos — `null` significa gate **não aplicado**,
nunca número inventado (`fase3.md` §15).

### 9.4 Limitações reais do M2

1. **Universo investível vazio antes de 2018.** Nenhum backtest é possível em 2010–2017 sob a
   regra estrita de identificação. Banda `severe` em 8 das 17 datas → gatilho de escalada.
2. **Cobertura de preço de 5/694 (0,7%)** é o gate dominante de 2018 em diante. O universo
   investível de 5 papéis não sustenta conclusão estatística sobre "o mercado brasileiro".
3. **Liquidez e dados mínimos nunca reprovam** — inertes até a cobertura de preço crescer.
4. **20 intervalos degenerados** (`valid_from = valid_to`) permanecem no `instrument_lifecycle`,
   marcados `inconsistent` — instrumentos cujas datas efetivas da FCA já eram inconsistentes.
5. **`share_class` divergente em 2 tickers** (`CGRA4` como ON, `CRTE3` como PN na FCA) —
   resolvido pelo ano de referência mais recente, registrado em `quality_findings`.
6. **Gotcha do backend REST**: uma coluna aliasada `t` colide com o alias `t` do
   `jsonb_agg(t)` dentro do RPC `exec_sql` e a linha inteira volta como escalar. Evitar `as t`.

---

## 10. M2.1A — expansão de cobertura de preços (parcial, PARADO no portão COTAHIST)

Execução iniciada 2026-08-30. **Blocos 1–3 concluídos; Blocos 4–7 bloqueados**
pela escalada abaixo. Ver "HANDOFF PARA SONNET — M2.1 (revisão 2)".

### 10.1 Janela canônica de preço (Bloco 1)

`instrument_price_window` (migração `20260830151258`) — **derivada e recomputável**,
nunca fonte de verdade. Regra:

```
price_valid_from = max(company start, class listing_start,
                       01/01/source_reference_year_first)
```

salvo **exceção de continuidade independente** (`config/price_continuity_exceptions.yaml`).
Ser variante única no FCA **não** prova o símbolo antes do primeiro ano observado — o
Yahoo retroprojeta série de predecessor sob o símbolo atual. Linha do provedor anterior
a `price_valid_from` **não entra** em `daily_prices` canônico: fica no bruto/ledger,
contada como `ticker_identity_not_proven`.

**Caso B** (SSBR3→ALSO3→ALOS3): cada variante truncada em
`date(year_first_do_sucessor − 1, 12, 31)`. ALOS3 (`year_first` 2023) nunca recebe linha
anterior a 2023-01-01. Variantes de **mesmo ano** (BRKM5/BRKM6) são paralelas — nenhuma
trunca a outra.

Resultado (694 instrumentos): `from_precision` **year 594 / day 100 / unknown 0**;
`to_precision` **open 432 / day 258 / year 4**; 67 variantes com sucessor; 53 paralelas
de mesmo ano; **5 exceções de continuidade** (PETR3/PETR4/VALE3/ITUB3/ITUB4 —
`proof: phase1_seed`). **Histórico descartado por `ticker_identity_not_proven` = 0**:
as exceções cobrem exatamente as 5 séries pré-2018 reais e corretamente atribuídas.

### 10.2 Ledger e batch file (Bloco 2)

`price_backfill_runs` + `price_backfill_attempts` (migração `20260830151747`). Uma linha
por `(run, instrumento)` — checkpoint/resume; nenhum instrumento some entre etapas.
`rows_written` = só o que caiu **dentro** da janela; `rows_out_of_window` fica no
bruto/ledger, nunca em `daily_prices`.

`sync-historical-prices` — fluxo **explícito**: só `resolution_status ∈ {resolved,
seeded}` + ticker válido + `company_id` + `instrument_id`. Nunca `sync-prices --all`,
nunca `instruments.active`. `PILOTO 0` congelado em `batches/m21_pilot_00.txt`
(sha256 `0077239418d2…`) **antes de qualquer rede** — 10 casos / 12 instrumentos.

### 10.3 PILOTO 0 — execução real (Bloco 3, run 3, após 2 correções)

Provedor: yfinance (V1, inalterado). 0.5 req/s. **0 CRITICAL.**

| ticker | caso | status | linhas | flag |
|---|---|---|---|---|
| PETR4 | controle / continuidade | resolved | 4139 | ok |
| ITUB3 | empresa re-registrada / continuidade | resolved | 4139 | ok |
| VALE5 | seeded PNA classe extinta | empty_series | 0 | incomplete |
| ALOS3 | cadeia de nome (sucessor) | resolved | 914 | estimated |
| ALSO3 | cadeia de nome (predecessor) | symbol_not_found | 0 | provider_no_data_delisted |
| SSBR3 | cadeia de nome (predecessor) | symbol_not_found | 0 | provider_no_data_delisted |
| CGRA4 | conflito de `share_class` ON/PN | resolved | 742 | estimated |
| KLBN11 | classe UNT | resolved | 2153 | estimated |
| MAGG3 | companhia cancelada 2019 (incorporação) | symbol_not_found | 0 | provider_no_data_delisted |
| ETRO3B | Bovespa Mais sufixo `B`, deslistada | symbol_not_found | 0 | provider_no_data_delisted |
| OGXP3 | falência/recuperação, deslistada | empty_series | 0 | incomplete |
| SMLS3 | janela degenerada | skipped_out_of_scope | 0 | ok |

Linhas gravadas: **12 087**; fora da janela: **0**. `daily_prices` 24 822 → **28 635**
(6 → 9 instrumentos; +3809 dos 3 novos + 4 linhas de cauda de PETR4/ITUB3 além do
último sync da Fase 1). **Idempotência**: 2ª execução → `daily_prices` inalterado,
`price_backfill_attempts` idênticos.

**Discriminador `symbol_not_found` × `empty_series` (MEDIDO, não presumido):** sonda
`yfinance` de 1 mês + `get_history_metadata()` após retorno vazio. `symbol_not_found` =
sonda e metadata vazias (ALSO3, ETRO3B, MAGG3, SSBR3 — deslistadas sem rastro no Yahoo).
`empty_series` = símbolo resolve hoje mas nada na janela pedida (VALE5; **OGXP3 — o
símbolo `OGXP3.SA` hoje devolve dados fora da janela de 2018, forte indício de
reutilização de código**).

**2 bugs reais corrigidos (piloto, dado de produção), com regressão:**
1. `duplicate_series` dava CRITICAL contra a **própria** série já em `daily_prices`
   (fingerprint semeado com o próprio ticker) → guarda `owner != ticker`.
2. `calendar_drift` dava WARN para datas **após o fim** do `trading_calendar` (2 dias de
   defasagem) → só datas interiores (`<= calendar_max`) contam.

### 10.4 PORTÃO COTAHIST — DISPARADO → ESCALADO

Deslistadas no piloto (6: VALE5, ALSO3, SSBR3, MAGG3, ETRO3B, OGXP3): **0 resolvidas
com dado**, 4 `symbol_not_found`, 2 `empty_series`.

- `coverage_delisted / coverage_active` = **0 / 1,0 < 0,60** → dispara.
- `symbol_not_found` entre deslistadas = **4/6 ≈ 67% > 40%** → dispara.
- OGXP3 = **possível reutilização de símbolo** → gatilho independente de escalada
  imediata.

O conjunto de deslistadas do piloto foi **escolhido a dedo** (casos difíceis:
OGX/falência, Magnesita/incorporação, Smiles, Somos) — não é amostra aleatória. A
medição representativa exigiria o lote de 20 estratificado (Bloco 4). Mas as condições
de parada do handoff estão atingidas **e** há indício de reutilização de símbolo →
**PARADO antes do Bloco 4**. `ESCALAR PARA OPUS — ANTECIPAR SPIKE COTAHIST`.
