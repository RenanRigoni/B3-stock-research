# Handoff Fase 3 — v2

Rodada de arquitetura (Opus). Substitui a v1. Escopo desta rodada: validar `main`,
executar M0, resolver M1 (universo histórico / survivorship bias) no nível
metodológico. **Nenhum código de motor de backtest.** Nenhum merge em `main`.

A v1 continha um erro de desenho: usava `available_from` como gate de elegibilidade
nas tabelas de lifecycle. Isso não previne look-ahead — esvazia o universo. A v2
formaliza a separação **bitemporal** e trava a exceção às duas tabelas de lifecycle.

---

## 1. Princípio bitemporal (o centro desta correção)

Duas linhas do tempo, papéis distintos e não intercambiáveis:

| Linha do tempo | Colunas | Papel |
|---|---|---|
| **Tempo efetivo** | `valid_from`, `valid_to`, `listing_start`, `listing_end` | Reconstrói o universo. Responde "essa empresa/instrumento existia e negociava em D?" |
| **Tempo de transação** | `source_available_from`, `source_observed_at`, `ingested_at` | Proveniência e reprodutibilidade. **Nunca** filtra elegibilidade. |

### Por quê

A *existência* de uma companhia negociando em 2013 era **fato público naquele
momento** para qualquer participante do mercado. O nosso pipeline ter baixado o
cadastro da CVM em 2026 não torna esse fato desconhecível em 2013. Portanto o
universo histórico se reconstrói pela **data efetiva do evento** (registro,
cancelamento, início/fim de negociação), não pela data em que ingerimos o
cadastro.

Contraste: uma DFP referente a 2015, reapresentada em 2020, **genuinamente não era
conhecida** em 2015. Aí `available_from` é obrigatório e absoluto.

### Consequência de ter errado na v1

Snapshot do cadastro CVM baixado em 2026-08 tem `source_available_from = 2026-08`
para todas as 2677 companhias. Um filtro `source_available_from <= D` com
`D = 2013` retornaria **conjunto vazio** para toda data histórica. O modo de falha
da v1 era "quebra alto" (universo vazio, fácil de notar). O modo de falha da v2 é
mais perigoso — ver seção 5.

---

## 2. A exceção é enumerada, não é princípio generalizável

Risco concreto: alguém "generaliza" a exceção bitemporal e enfraquece o
point-in-time da Fase 2. Fica travado por tabela.

| Tabela | Gate de elegibilidade / uso | Por quê |
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

**Somente as duas tabelas de lifecycle** usam tempo efetivo. Estender essa lista
exige escalar para Opus (ver seção 12).

---

## 3. Rename das colunas de proveniência (guarda, não cosmética)

`available_from` significa "o gate point-in-time" em **todo o resto** do codebase.
Uma coluna com esse nome numa tabela onde ela **não pode** ser gate é armadilha de
memória muscular — alguém escreve `where available_from <= as_of` por reflexo e
reintroduz o bug em silêncio.

Por isso, nas tabelas de lifecycle, os campos de proveniência são:

- `source_available_from` — quando a fonte oficial tornou o dado disponível (pode
  ser NULL se a fonte não informa).
- `source_observed_at` — quando o nosso pipeline observou o registro na fonte
  (data do snapshot / ano de referência do FCA). **not null.**
- `ingested_at` — `timestamptz not null default now()`.

O prefixo `source_` / o nome `ingested_at` quebram o reflexo de digitar
`available_from` no `WHERE` de elegibilidade.

---

## 4. Schema proposto

Duas tabelas novas, **estritamente aditivas** (nenhuma coluna existente
renomeada / removida / retipada). `instruments` e `instrument_id` continuam a
chave de tudo que a Fase 1/2 já consulta — mesmo padrão do `company_id` aditivo da
Fase 2.

### 4.1 `company_lifecycle`

| grupo | colunas |
|---|---|
| identidade | `company_lifecycle_id` PK, `company_id` FK → `companies` |
| **EFETIVAS** | `valid_from` date **not null**, `valid_to` date (NULL = vigente), `event_date` date, `cvm_registration_date` date, `cvm_cancel_date` date |
| atributos | `registration_status`, `issuer_status`, `event_type`, `reason` (de `MOTIVO_CANCEL`), `successor_company_id` FK nullable, `predecessor_company_id` FK nullable |
| **PROVENIÊNCIA** | `source`, `source_document_ref`, `source_available_from` timestamptz, `source_observed_at` timestamptz **not null**, `ingested_at` timestamptz not null default now(), `run_id` FK → `ingestion_runs` |
| qualidade | `quality_flag`, `quality_reason` |
| natural key | `unique (company_id, event_type, valid_from, source)` |

`registration_status`: `registered` / `canceled` / `suspended` (de `SIT`).
`issuer_status`: `operational` / `pre_operational` / `judicial_recovery` /
`bankrupt` / ... (de `SIT_EMISSOR`).
`event_type`: `registration` / `cancellation` / `status_change`.

Regra: **uma linha por transição**. Registro simples (empresa ativa desde o
registro, nunca cancelada) = 1 linha com `valid_from = DT_REG`, `valid_to = NULL`.

### 4.2 `instrument_lifecycle`

| grupo | colunas |
|---|---|
| identidade | `instrument_lifecycle_id` PK, `instrument_id` FK → `instruments`, `company_id` FK → `companies` |
| **EFETIVAS** | `valid_from` date **not null**, `valid_to` date, `listing_start` date, `listing_end` date |
| atributos | `ticker`, `share_class` (`ON`/`PN`/`PNA`/`PNB`/`UNT`/...), `isin`, `market` (`bolsa`/`balcao_organizado`), `listing_venue` (B3), `trading_status` (`trading`/`suspended`/`delisted`) |
| **PROVENIÊNCIA** | `source`, `source_reference_year` int, `source_available_from` timestamptz, `source_observed_at` timestamptz **not null**, `ingested_at` timestamptz not null default now(), `run_id` FK |
| qualidade | `quality_flag`, `quality_reason` |
| natural key | `unique (company_id, ticker, share_class, valid_from, source)` |

### 4.3 CHECKs e RLS

- `check (valid_to is null or valid_from is null or valid_to >= valid_from)`
- `check (listing_end is null or listing_start is null or listing_end >= listing_start)`
- `enable row level security` sem policy (padrão do projeto — pipeline usa
  `service_role`).

### 4.4 Comment de tabela — obrigatório

Cada tabela carrega comment declarando literalmente:

> `valid_from` / `valid_to` / `listing_*` são **tempo efetivo** (existência /
> negociação real) e são o único gate de elegibilidade do universo.
> `source_*` / `ingested_at` são **tempo de transação** — proveniência e
> reprodutibilidade apenas, **nunca** filtro de elegibilidade. Ver
> `docs/historical_universe.md`.

---

## 5. Modos de falha novos que esta correção cria

A troca de gate move o modo de falha de "quebra alto" (universo vazio) para
**"falha em silêncio"** — e silêncio é o que reintroduz survivorship bias.

### 5.1 `listing_start` NULL exclui em silêncio

`NULL <= D` → NULL → não-verdadeiro → instrumento some do universo sem aviso.
Viola a spec §94 ("não mascarar ausência de dados").

**Regra obrigatória:** NULL em data efetiva **nunca** cai no filtro
implicitamente. Resolução, em ordem:

1. Fallback documentado (`company.valid_from`, ou primeira data de preço
   observada), com `quality_flag = 'estimated'`.
2. Se não houver fallback: status explícito `NOT_ELIGIBLE_DATA`, **contabilizado
   no relatório**. Nunca drop mudo.

### 5.2 `listing_end` NULL cria instrumento zumbi

`valid_to IS NULL` conflaciona "ainda vivo" com "não sabemos quando acabou". Se o
FCA para de reportar um instrumento em ano Y+1 sem `Data_Fim_Negociacao`, ele fica
negociável para sempre — survivorship bias na direção oposta.

**Regra:** sumiço do FCA sem data de fim → derivar `listing_end` = última
referência FCA observada (ou última cotação), com `quality_flag = 'estimated'`.
Cancelamento da companhia é **teto de fallback**:
`listing_end IS NULL AND company.valid_to IS NOT NULL` →
`listing_end := company.valid_to`, `estimated`.

### 5.3 `valid_to` é fato futuro — só pode responder a uma pergunta

`valid_to = 2018-06-30` é informação do futuro relativa a `D = 2013`. Usá-la para
responder *"estava vivo em D?"* é legítimo (a condição não exclui nada antes de
2018). Usá-la para **qualquer outra coisa** é look-ahead: não comprar em 2013
porque sabemos que deslista em 2018, limitar holding period, encurtar forward
return.

**Guarda arquitetural:** `get_investable_universe_as_of(D)` **não expõe**
`valid_to` / `listing_end` à camada de estratégia. O snapshot que
`Strategy.select(snapshot)` recebe (M3, spec §20) não tem esses campos. O engine
mantém o lifecycle internamente só para tratar a saída quando o relógio da
simulação *alcança* a data. Estratégia nunca vê o futuro porque nunca recebe o
campo.

**Corolário V1:** o engine reage no `listing_end` **efetivo**, não no anúncio
(OPA / incorporação são anunciadas meses antes; não temos data de anúncio
confiável). Isso enviesa a saída para *pessimista* (segura até o fim) em vez de
otimista — viés conservador, o lado certo. Documentar.

### 5.4 Fontes discordam

Cancelamento CVM e fim de negociação FCA podem divergir por meses. `AND` estrito
entre os dois encolhe o universo em silêncio.

**Regra:** datas do **instrumento** são autoritativas para negociabilidade;
`company_lifecycle` dá status / motivo / sucessor e serve de teto de fallback.
Divergência com ambas as datas presentes → linha em `quality_findings`, mantém a
data do instrumento (mais específica), flagged.

---

## 6. Predicado de elegibilidade (contrato)

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
# NULL em listing_start NUNCA cai no filtro implicitamente:
#   resolver por fallback documentado (quality_flag='estimated')
#   ou marcar NOT_ELIGIBLE_DATA e CONTAR no relatório.
```

`get_investable_universe_as_of(D)` implementa esse predicado como **função pura**,
espelhando `analytics/fundamentals.select_point_in_time` (seleção pura separada do
acesso a banco, testável sem SQL). **Não materializar** `universe_snapshots` em
V1 — só se performance exigir após expandir o universo.

O retorno **não contém** `valid_to` nem `listing_end`.

---

## 7. M0 — estado e ações

### 7.1 Estado de `main`

- **463 testes verdes**, `ruff` limpo, `mypy` limpo (74 arquivos).
- `stock-research doctor` OK via PostgREST (sem `DATABASE_URL`).
- 6 instrumentos: IBOV, PETR3/PETR4, VALE3, ITUB3/ITUB4 (PETR3/ITUB3 inativos).
- `instruments.valid_from` / `valid_to` existem no schema e estão NULL — nunca
  foram usados.
- `ticker_aliases` só com o self-alias trivial do ticker atual.

### 7.2 MCP Supabase — dois conectores

- **`mcp__claude_ai_Supabase__*`** (conta do Renan) enxerga o projeto certo —
  `bdppudbcjosznkfucekm` "B3 FOCUS", `sa-east-1`, PG 17, único projeto visível.
  Exige `project_id` em cada chamada.
- **`mcp__supabase__*`** (sem param `project_id`) — token diferente, aponta para
  um app de saúde / CRM. **Não usar para B3.**

### 7.3 Ledger de migrations

`list_migrations` do B3 FOCUS para em `20260827000005`. A migration
`20260827000006` (`dcf_and_macro`) **não está no ledger**, mas a DDL está aplicada
no banco (4 tabelas populadas: `risk_free_assumptions` 1,
`equity_risk_premium_assumptions` 1, `wacc_assumptions` 2, `valuation_snapshots`
12).

Contagens de linha do `list_tables` do MCP são `reltuples` stale em tabelas
pequenas — usar `stock-research status` como autoritativo.

### 7.4 Ações M0

1. `git checkout -b fase3-backtesting-engine` a partir de `main`.
2. **Commit isolado de housekeeping** (sem mistura conceitual, spec §2):
   - `insert into supabase_migrations.schema_migrations (version, name)
     values ('20260827000006','dcf_and_macro')` via
     `mcp__claude_ai_Supabase__execute_sql` (`project_id=bdppudbcjosznkfucekm`).
     Não precisa mais do SQL editor do dashboard.
   - corrigir as 4 linhas de `docs/data_dictionary.md` que dizem
     *"(migration ..06, não aplicada)"* → *"aplicada 2026-08-27"*.
3. Criar `docs/fase3_plan.md` (baseline 463 / limpo / limpo), esqueleto de
   `docs/historical_universe.md`, abrir seção "Fase 3" em `docs/roadmap.md`,
   atualizar `docs/survivorship_bias_plan.md` apontando para este handoff.
4. Registrar em `docs/fase3_plan.md` a **regra bitemporal** (tabela da seção 2) e
   o alerta dos dois conectores MCP.
5. Commit `docs: baseline Fase 3 (M0)`. `pytest` / `ruff` / `mypy` verdes antes do
   M1.

---

## 8. M1 — fontes reais validadas

### 8.1 Company lifecycle — CVM cadastro

`https://dados.cvm.gov.br/dados/CIA_ABERTA/CAD/DADOS/cad_cia_aberta.csv`

- **Já em disco**: `data/raw/cvm/registry/cad_cia_aberta.csv` — 2677 linhas,
  separador `;`, encoding **latin-1 / cp1252**.
- Colunas de ciclo de vida confirmadas por inspeção do arquivo real:
  `DT_REG`, `DT_CANCEL`, `MOTIVO_CANCEL`, `SIT`, `DT_INI_SIT`, `SIT_EMISSOR`,
  `DT_INI_SIT_EMISSOR`, `CD_CVM`, `CATEG_REG` (Categoria A = pode negociar ações;
  B = só dívida), `TP_MERC`, `SETOR_ATIV`.
- O parser atual (`sources/fundamentals/company_registry.py`) valida só
  `{CNPJ_CIA, DENOM_SOCIAL, CD_CVM, SIT}` e descarta o resto. `_best_name_match`
  já prioriza `SIT='ATIVO'` — as canceladas passam pelo pipeline e são jogadas
  fora. Precisa estender para retê-las.
- **Limitação**: é um *snapshot do estado atual*. Dá `DT_REG` / `DT_CANCEL`
  (permite reconstruir o intervalo efetivo), mas **não** traz histórico de
  mudança de situação ou de razão social, e **não tem ticker, ISIN nem classe**.
- **Verificar** (não assumido): se o dataset `cia_aberta-cad` publica snapshots
  datados ou `inf_cadastral_*` mensais.

### 8.2 Instrument / ticker lifecycle — CVM FCA

Dataset `cia_aberta-doc-fca`, arquivo `fca_cia_aberta_valor_mobiliario_YYYY.csv`
(Seção 2 do Anexo 22, ICVM 480).

- Confirmado via web: **ZIPs anuais 2010–2026**, atualização semanal, última em
  24-ago-2026. Cada ZIP tem 9 CSVs.
- É a fonte point-in-time de ticker / classe / mercado / entidade administradora /
  datas de início e fim de negociação, por companhia e por ano de referência do
  formulário.
- **Não ingerido hoje** (não existe `sources/fundamentals/cvm_fca.py`).
- **AÇÃO OBRIGATÓRIA antes de escrever parser**: baixar 1 ZIP (ex. 2015), fazer
  sniff do header real e confirmar os nomes de coluna. Esperado, **a validar**:
  `Codigo_Negociacao`, `Valor_Mobiliario`, `Mercado`,
  `Sigla_Entidade_Administradora`, `Data_Inicio_Negociacao`,
  `Data_Fim_Negociacao`, `Classe_Acao_Preferencial` / `Sigla_Classe...`, ISIN se
  houver. Não assumir — mesma disciplina do resto do projeto.
- Verificar no mesmo ZIP se há arquivo utilizável de **eventos societários** (para
  `successor_company_id` / `predecessor_company_id`).

### 8.3 Outras fontes

- **Preço da cauda de deslistadas**: `yfinance` normalmente para de retornar após
  o delisting (documentado em `survivorship_bias_plan.md §3`). Sem fonte gratuita
  robusta. Aceitar cobertura parcial + `quality_flag`.
- **Composição histórica de índice (IBOV / IBrX)**: B3 publica carteiras teóricas
  trimestrais. Fonte para a definição alternativa de "investível = estava no
  índice". **Não validada** nesta rodada — concerne o filtro de investibilidade
  (M2), não M1.
- **Sucessão societária**: `MOTIVO_CANCEL` dá o *tipo* do evento (`INCORPORAÇÃO`,
  `INCORPORAÇÃO DE AÇÕES`, `CANCELAMENTO VOLUNTÁRIO`, `FALÊNCIA/LIQUIDAÇÃO`, ...)
  mas **não nomeia o sucessor**. Successor / predecessor exige FCA "eventos
  societários" (a confirmar) ou curadoria manual por CNPJ.

### 8.4 Cobertura histórica

- Piso real do projeto: **2010** (preços `yfinance default_start`, CVM DFP/ITR
  `default_from_year: 2010`, FCA começa em 2010).
- Notícias / eventos (GDELT DOC API): **2017** (2015 rejeitado pela fonte,
  confirmado na Fase 1.1).
- Consequência: estratégias-base (quality / value) podem chegar a ~2011–2012 (após
  warm-up de ~1 ano de fundamentos + preço); overlays de notícia / evento (M13) só
  a partir de 2017.
- Split temporal (spec §46): **não fixar** 2011-2018 / 2019-2022 / 2023-2026 antes
  de medir cobertura ano a ano do universo expandido.

### 8.5 Ticker history / VALE

- Nunca reescrever ticker antigo como o atual (spec §9). `ticker_aliases` (já
  existe, sem conteúdo real) + `instrument_lifecycle`.
- Reconstrução a partir do FCA: cada linha `(companhia, ano, código de negociação,
  classe)` vira um intervalo. Código diferente entre anos consecutivos da mesma
  companhia / classe = evento de troca de ticker (`valid_to` do antigo,
  `valid_from` do novo, mesmo `company_id`).
- **V1 aceita granularidade anual** (a data do FCA é a `Data_Referencia` do
  formulário). Refino de data exata fica para iteração posterior.
- **VALE como caso de teste** (spec §10): estrutura por classe em 2010
  (VALE3 ON + VALE5 PNA) ≠ 2026 (só VALE3 ON, após a conversão de 2017). O
  `instrument_lifecycle` tem que representar a classe PN com `valid_to ≈ 2017` e a
  conversão como evento. Market cap histórico = Σ por classe **vigente na data** —
  já é assim em `valuation_multiples`, mas hoje só com as classes cadastradas
  hoje.

---

## 9. Migrations propostas

*(não aplicar nesta rodada, além do housekeeping M0)*

1. `20260901000001_company_lifecycle.sql` — cria `company_lifecycle` + índices
   `(company_id, valid_from)` e `(valid_to)` + RLS sem policy + comment
   bitemporal. Aditiva, reversível (`drop table`).
2. `20260901000002_instrument_lifecycle.sql` — cria `instrument_lifecycle` +
   índices `(company_id, valid_from)` e `(listing_start, listing_end)` + RLS +
   comment bitemporal. Aditiva, reversível.
3. *(condicional)* `20260901000003_liquidity_metrics.sql` — só se a liquidez
   (spec §16) for para tabela dedicada em vez de `fundamental_metrics`.

Estilo: seguir os arquivos existentes (cabeçalho citando a seção do `fase3.md`,
natural key `unique`, RLS sem policy). Aplicar via
`mcp__claude_ai_Supabase__apply_migration` (`project_id=bdppudbcjosznkfucekm`) ou
RPC `exec_ddl`. **Nunca** via `mcp__supabase__*`.

---

## 10. Riscos

| Risco | Mitigação |
|---|---|
| Schema real do FCA `valor_mobiliario` não validado — nomes de coluna assumidos | sniff do header antes de codar. **Bloqueante.** |
| FCA anual → precisão de ~1 ano em troca de ticker / delisting na V1 | aceito e documentado; refino posterior |
| Sucessão societária não estruturada → `successor_company_id` majoritariamente NULL; incorporação vira "empresa sumiu" com retorno de saída errado | `event_type='cancellation'` + `reason` sempre gravados; política conservadora documentada |
| **Data efetiva ausente = exclusão silenciosa** (modo de falha novo desta correção) | regra de NULL explícita (seção 5.1) + contagem no relatório |
| **Instrumento zumbi** por `listing_end` NULL implícito | derivação de `listing_end` + teto por cancelamento da companhia (seção 5.2) |
| Divergência cadastro × FCA encolhendo universo em silêncio | instrumento autoritativo + `quality_findings` (seção 5.4) |
| Cauda de preço de deslistada incompleta no yfinance | `quality_flag` + reporte de quantos sinais tiveram saída incerta |
| REST sem transação multi-tabela → estado parcial | brutos em disco + idempotência por natural key (padrão do projeto) |
| Dois conectores MCP → DDL no projeto errado | usar sempre `mcp__claude_ai_Supabase__*` com `project_id` explícito |

---

## 11. Limitações da V1

- Universo histórico V1 será "suficientemente confiável", não completo:
  granularidade anual, sucessão manual, cauda de preço parcial.
- Sem composição histórica de índice em V1 → "investível" = negociada + passa
  filtros de liquidez / dados, não "estava no IBOV".
- Bancos (`quality_bank_v1` incomplete) e DCF `estimated` (PETR4 / VALE3) seguem
  como na Fase 2 — não reabrir; o motor decide via config o que aceita
  (spec §84 / §86).
- Overlays de notícia / evento só a partir de 2017.

---

## 12. Decisões fechadas

1. Baseline M0: 463 testes verdes, `ruff` / `mypy` limpos.
2. `20260827000006` aplicada no banco; falta só o ledger — agora corrigível via
   `mcp__claude_ai_Supabase__execute_sql`.
3. Branch de trabalho: `fase3-backtesting-engine`. Sem merge em `main` sem
   autorização explícita.
4. **Regra bitemporal**: universo reconstruído por tempo efetivo; sinal por tempo
   de transação. Exceção **enumerada** às tabelas `company_lifecycle` e
   `instrument_lifecycle` (seção 2).
5. Colunas de proveniência nas tabelas de lifecycle nomeadas
   `source_available_from` / `source_observed_at` / `ingested_at` — rename
   deliberado para quebrar o reflexo de `available_from` (seção 3).
6. `valid_to` / `listing_end` respondem **apenas** "estava vivo em D?"; não são
   expostos à camada de estratégia (seção 5.3).
7. O engine reage no fim **efetivo**, não no anúncio (viés conservador
   documentado).
8. Datas de instrumento são autoritativas para negociabilidade;
   `company_lifecycle` = status / motivo / sucessor + teto de fallback.
9. NULL em data efetiva nunca filtra em silêncio (seção 5.1).
10. Duas tabelas novas aditivas; `instruments` / `instrument_id` seguem sendo a
    chave de tudo que a Fase 1/2 consulta.
11. `get_investable_universe_as_of` = função pura, espelha
    `select_point_in_time`. Sem materialização em V1.
12. Banco B3 = `bdppudbcjosznkfucekm`, acessível via `mcp__claude_ai_Supabase__*`
    (com `project_id`) ou `stock_research.db` (REST + `exec_ddl`).
13. Nenhuma metodologia da Fase 2 é reaberta.

---

## 13. Decisões ainda abertas

1. Instrumentos deslistados entram **todos** em `instruments` (`active=false`) ou
   só os que têm preço? (volume de linhas / joins).
2. Fallback de `listing_start` NULL: `company.valid_from` vs. primeira cotação
   vs. `NOT_ELIGIBLE_DATA`.
3. Liquidez (spec §16): métricas em `fundamental_metrics` (reuso) vs. nova
   `liquidity_metrics` (clareza).
4. Definição de "investível" em V1: só filtros de liquidez / dados, ou também
   exigir presença em índice (precisa da fonte de carteira histórica B3).
5. Preço de saída em delisting sem cotação: última cotação conhecida vs. zero
   condicional a `MOTIVO_CANCEL`.
6. Janela do universo expandido para o split temporal (§46) — definir após medir
   cobertura por ano.
7. FCA tem arquivo de "eventos societários" utilizável para
   successor / predecessor? (validar ao baixar o ZIP).
8. `backtest_runs.universe_version` (spec §80) referencia o `source_observed_at`
   do lifecycle (vintage) — formato a definir.

---

## 14. Ordem exata de implementação

### M0

1. Branch `fase3-backtesting-engine` a partir de `main`.
2. Rodar `pytest -q` / `ruff check .` / `mypy src`; registrar baseline
   (463 / limpo / limpo) em `docs/fase3_plan.md`.
3. Housekeeping do ledger (`insert` via `execute_sql`) + fix do
   `docs/data_dictionary.md`. Commit isolado.
4. Docs: `docs/fase3_plan.md`, `docs/historical_universe.md` (com a **tabela de
   gates bitemporais** da seção 2 como primeira seção), seção Fase 3 no
   `docs/roadmap.md`, atualizar `docs/survivorship_bias_plan.md`.
5. Commit `docs: baseline Fase 3 (M0)`. Verde antes do M1.

### M1

6. **Validar fontes**: baixar `cad_cia_aberta.csv` fresco (comparar SHA com o de
   disco); baixar `fca_cia_aberta_2015.zip`; sniff dos headers reais; gravar as
   colunas confirmadas em `docs/historical_universe.md`. **Não codar parser
   antes disso.**
7. Estender a leitura do cadastro (`sources/fundamentals/company_registry.py` ou
   novo módulo) para reter **todas** as linhas + os campos de ciclo de vida.
   Normalização pura em `transforms/`.
8. `sources/fundamentals/cvm_fca.py` + transform do `valor_mobiliario`: parser +
   validação de schema explícita (falha dura se o formato mudar — padrão do
   projeto).
9. Migration `20260901000001_company_lifecycle.sql`; aplicar; registrar no ledger.
10. Migration `20260901000002_instrument_lifecycle.sql`; idem.
11. `pipelines/historical_universe.py`: cadastro → `company_lifecycle`; FCA
    multi-ano → `instrument_lifecycle`. Derivação de `listing_end` faltante + teto
    por cancelamento. Divergências → `quality_findings`. Idempotente por natural
    key. `ingestion_runs`.
12. CLI `sync-cvm-lifecycle` (ou estender `sync-cvm`).
13. Backfill: PETR / VALE / ITUB + canceladas do cadastro. Validar VALE (§10):
    classes de 2010 ≠ 2026 aparecem no `instrument_lifecycle`.
14. `analytics/universe.py`: `select_investable_universe` (função pura, espelha
    `select_point_in_time`) + `get_investable_universe_as_of(D)`, implementando o
    predicado da seção 6 e **não expondo** `valid_to` / `listing_end`.
15. `config/backtest_universe_v1.yaml` + registro do vintage do lifecycle.
16. Testes obrigatórios (seção 15). `pytest` / `ruff` / `mypy` verdes.
17. Commits pequenos por etapa. Atualizar `docs/historical_universe.md` e
    `docs/roadmap.md`. **PARAR e reportar antes do M2.**

---

## 15. Testes obrigatórios

### 15.1 Bloco bitemporal (o que a correção exige provar)

1. **`test_company_registered_2010_canceled_2018_appears_in_2013`** — fixture com
   `valid_from=2010-03-01`, `valid_to=2018-06-30`, e
   `source_available_from = source_observed_at = ingested_at = 2026-08-30` (futuro
   em relação a todo D testado). Universo em 2013-06-15 **contém** a empresa.
2. **`test_same_company_still_present_in_2017`** — presente em 2017-12-31.
3. **`test_absent_after_effective_cancellation`** — ausente em 2018-07-01;
   **presente** em 2018-06-30 (fronteira inclusiva).
4. **`test_universe_is_invariant_to_provenance_dates`** — *(o teste decisivo)*:
   computa o universo em D; recomputa com `source_available_from` /
   `source_observed_at` / `ingested_at` deslocados ±10 anos; **resultado
   idêntico**. Falha alto se alguém adicionar o gate de proveniência.
5. **`test_future_delisting_never_excludes_before_effective_date`** — varre D de
   2010-01-01 a 2018-06-30 em passos mensais; empresa presente em **todos**.
   Conhecer o cancelamento em 2026 nunca a exclui antes de 2018.
6. **`test_valid_to_not_exposed_to_strategy_layer`** — o retorno de
   `get_investable_universe_as_of` não contém `valid_to` nem `listing_end`.

### 15.2 Bloco de falha silenciosa (riscos novos)

7. **`test_missing_listing_start_is_reported_not_silently_dropped`** —
   instrumento com `listing_start` NULL: ou resolve por fallback com
   `quality_flag='estimated'`, ou sai como `NOT_ELIGIBLE_DATA` **contabilizado**.
   Nunca some sem contagem.
8. **`test_instrument_absent_from_later_fca_gets_explicit_end`** — instrumento
   presente no FCA até 2016 e ausente depois recebe `listing_end` derivado +
   `estimated`. Não fica negociável para sempre.
9. **`test_company_cancellation_caps_open_instrument`** — `listing_end` NULL +
   `company.valid_to` presente → teto aplicado, flagged.
10. **`test_source_disagreement_creates_quality_finding`** — cadastro e FCA
    divergem → linha em `quality_findings`, instrumento mantido pela data própria,
    sem drop silencioso.

### 15.3 Bloco anti-survivorship / estrutural

11. **Anti-survivorship (spec §76)** — Company A 2010–2026, Company B 2010–2015.
    Universo 2013 = {A, B}; 2020 = {A}.
12. **Ticker history** — código diferente entre anos FCA gera dois intervalos com
    o mesmo `company_id`; o antigo não é reescrito.
13. **VALE classes** — classe PN com `valid_to` ~2017 + conversão como evento;
    market cap de 2012 usa as classes vigentes em 2012.
14. **Company lifecycle** — registro simples = 1 linha `valid_to=NULL`;
    cancelamento = transição com `reason` de `MOTIVO_CANCEL`; `event_type`
    mapeado.
15. **Schema validation** — header FCA / cadastro alterado → parser falha
    explicitamente.
16. **Idempotência** — rodar o pipeline 2× não duplica linha de lifecycle.
17. **Migrations reversíveis** — `drop` das duas tabelas não afeta as tabelas da
    Fase 1/2.

### 15.4 Regressão — sinais mantêm o gate antigo

18. **`test_signal_tables_still_gate_on_available_from`** —
    `get_fundamentals_as_of` e o caminho de valuation seguem rejeitando
    `available_from > as_of`. A exceção de lifecycle **não vazou** para o sinal.

---

## 16. Escalar para Opus novamente se:

- O schema real do FCA `valor_mobiliario` não tiver código de negociação ou datas
  de negociação utilizáveis (quebra a estratégia de reconstrução de ticker).
- Não existir fonte estruturada de sucessão societária e a cobertura manual for
  inviável no universo alvo.
- Fração relevante de instrumentos ficar sem data efetiva resolvível (o fallback
  vira a regra, não a exceção).
- A cobertura de preço de deslistadas for tão ruim que o viés de saída domine os
  resultados.
- Alguém precisar estender a exceção bitemporal **além** das duas tabelas de
  lifecycle.
- A distribuição de qualidade no universo expandido exigir
  `quality_nonfinancial_v2` antes das estratégias.
- O split temporal (§46) não comportar 3 janelas com N suficiente.
- Qualquer pré-requisito exigir tocar metodologia da Fase 2.

**Parar após M1.** Sem M2+, sem merge em `main`, sem descoberta de estratégias,
sem machine learning.
