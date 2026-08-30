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
