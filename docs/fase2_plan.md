# Plano — Fase 2 (motor de valuation e qualidade)

**Status: implementação iniciada (2026-08-27) — 1º incremento: §19 (entidade `companies`).**
Fase 1.1 fechou (`FASE 1.1 = CONCLUÍDA` em `docs/roadmap.md`), removendo o bloqueador do
§22, e o usuário autorizou começar pelo §19. Este documento é autocontido — não depende de
nenhuma conversa anterior. Registra (1) as decisões de arquitetura aprovadas para a Fase 2,
(2) os achados reais de investigação que essas decisões pediam e (3) o andamento da
implementação por seção. **Fora do §19, nenhuma migration ou código da Fase 2 foi escrito
ainda** — cada bloco seguinte exige autorização própria.

## 1. Objetivo (de `PROJETO.txt`)

Passar de "o que aconteceu com esta empresa" para "esta ação parece cara, barata ou
razoável, considerando os fundamentos atuais". Cinco entregas: qualidade da empresa,
valuation histórico (múltiplos), valor justo (DCF/múltiplos/pares), margem de segurança,
classificação. Fase 4 (score de atratividade consolidado) é posterior e não faz parte
disto.

## 2. O que a Fase 1/1.1 já entrega (fundação reaproveitável)

`fundamental_metrics` já calcula, point-in-time, por `(instrument_id, reference_date,
period_type, metric_name)`: `assets`, `equity`, `cash`, `liabilities`, `gross_debt`,
`net_debt`, `revenue`, `net_income`, `ebit`, `operating_cash_flow`, `capex`,
`free_cash_flow`, `net_margin`, `roe`, `revenue_growth_yoy`, `net_income_growth_yoy` — com
`quality_flag`/`quality_reason` por métrica ausente, e bloqueio setorial já correto para
bancos (`sector_inadequate`). `daily_prices` (2010→hoje, bruto + ajustado),
`corporate_actions` (dividend/jcp/split, fonte yfinance), `trading_calendar`,
`daily_returns`, `events`/`event_studies` também já existem.

**Garantia a preservar em tudo que a Fase 2 calcular**: `available_from <= as_of_date`,
sem exceção, do mesmo jeito que `get_fundamentals_as_of` já garante para fundamentos
brutos.

---

# DECISÕES DE ARQUITETURA APROVADAS

## 3. Quantidade histórica de ações — CONCLUÍDA E VERIFICADA (2026-08-27)

> **Andamento**: migration `20260827000003_share_count_history.sql` **aplicada** +
> `sources/fundamentals/cvm_fre.py` + `transforms/share_count.py` +
> `pipelines/share_count.py` + CLI `sync-fre` + testes (18 testes; +
> `test_cvm_common` regressão de encoding). `stock-research sync-fre --from-year 2010`
> **rodado**: 2010→2026, 153 linhas em `share_count_history` (17 anos × 3 companhias ×
> ON/PN/TOTAL), ~936 documentos FRE em `cvm_documents`.
>
> **Verificação contra o §13.1**: Petrobras 2024 ON 7.442.231.382 / PN 5.446.501.379 /
> TOTAL 12.888.732.761 ✓ exato. Itaú 2024 ON 5.454.119.395 / PN 5.330.429.488 ✓.
> Vale 2024 ON 4.539.007.568 / PN 12 ✓ (via fallback, ver §24). Unificação de classes da
> Vale em 2017 (PN 2.108.579.618 → 12) reproduzida na série. `quality_flag`: 144 `ok`,
> 6 `estimated` (Vale 2023-24), 3 `inconsistent` (Itaú 2011).
>
> Achados que revêem o §13.1: ver §24-27. Pendência §14.1 (cobertura ano-a-ano) agora
> **fechada** — 2010→2026 baixados e ingeridos sem gap.

**Fonte primária: CVM FRE (Formulário de Referência).** Investigado e validado contra
arquivos reais (não assumido) — ver achados no §11 e §24.

**Fonte secundária, só para validação cruzada**: `yfinance.Ticker.get_shares_full(start,
end)`. **Nunca** `Ticker.info["sharesOutstanding"]` (não é série histórica, é o valor
atual no momento da chamada).

Tabela nova `share_count_history`, com a mesma disciplina point-in-time de
`financial_statement_facts`:

```text
company (FK para a entidade emissor/issuer -- ver §4)
share_class          -- ON, PN, PNA, PNB, etc.
reference_date
available_from
shares_issued
treasury_shares
shares_outstanding
source
source_document
version
quality_flag
```

Reapresentações preservadas (nunca sobrescrever uma versão anterior).

## 4. Market cap por companhia, não por ticker — IMPLEMENTADO (2026-08-27, base FY)

> **Andamento**: `supabase/migrations/20260827000004_valuation_multiples.sql` (aplicada) +
> `analytics/valuation_multiples.py` + CLI `compute-multiples`. PETR3/ITUB3 tiveram preços
> baixados (`sync-prices --ticker`), continuam `active=false`. `market_cap = Σ (close_classe
> × shares_issued_classe)`, todos os insumos preservados em `price_inputs`. `compute-multiples`
> rodado 2026-08-27: Petrobras 567,6 bi (P/L 5,1 / EV/EBITDA 3,9 / P/VP 1,27 / DY 8,3%),
> Vale 348,3 bi (P/L 29,5), Itaú 452,7 bi (P/L 9,9 / EV/EBITDA n/a — banco). Ver §30.

**Não calcular** `preço do PETR4 × todas as ações da Petrobras`. Petrobras e Itaú têm
múltiplas classes de ação (ON + PN) negociadas como tickers separados; VALE3 tem
estrutura diferente (ver §11 — hoje é praticamente mono-classe).

```text
market_cap_companhia = Σ (preço da classe × shares_outstanding da classe)
```

Exemplo: `market_cap(Petrobras) = PETR3_price × PETR3_shares + PETR4_price × PETR4_shares`.
Mesma lógica para ITUB3/ITUB4. VALE3 conforme sua estrutura real (hoje essencialmente uma
classe só — ver §11.3).

**Implica separar `instrument` (ticker, o que já existe) de `issuer`/`company` (a
entidade legal, hoje só implícita via `cnpj`/`cvm_code`).** Ver §12 para o achado sobre a
arquitetura atual. Fundamentos **não são duplicados por ticker** — pertencem à
`company`/`issuer`, não ao `instrument`.

Novos instrumentos a cadastrar: **PETR3** e **ITUB3** (validados no yfinance, ver §10).

## 5. Múltiplos point-in-time — IMPLEMENTADO (2026-08-27, só base FY; TTM pendente)

> **Andamento**: `valuation_multiples` V1 calcula P/L, EV/EBITDA, FCF yield, earnings yield,
> P/VP, dividend yield na base **FY** (último exercício anual com `available_from <=
> as_of_date`). `tests/unit/test_valuation_multiples.py` cobre a montagem + o rigor
> point-in-time (queries filtram `trade_date`/`available_from <= as_of`). **`basis='ttm'`
> ainda não** — a coluna existe, o cálculo TTM (soma de 4 trimestres isolados, exige EBITDA
> trimestral isolado) fica para o próximo incremento; os testes de look-ahead do caminho
> TTM entram junto. Bancos: `ev_ebitda` e `fcf_yield` = NULL (EBITDA/FCF `sector_inadequate`);
> P/L, P/VP, DY, earnings yield seguem válidos.

Padrão: **TTM** (trailing twelve months). Visão auxiliar: **FY** (último ano fiscal
fechado).

```text
P/L            -> Net Income TTM
EV/EBITDA      -> EBITDA TTM
FCF Yield      -> FCF TTM
Earnings Yield -> Net Income TTM
P/VP           -> último patrimônio líquido disponível
Dividend Yield -> proventos trailing 12 meses
```

Regra absoluta, sem exceção: `available_from <= as_of_date`. Nunca usar trimestre ou
documento ainda não publicado naquela data. **Testes de look-ahead específicos para
valuation** são obrigatórios antes de qualquer múltiplo ser considerado correto — mesmo
padrão de `tests/unit/test_lookahead.py`, mas cobrindo o caminho TTM (que soma 4
trimestres — o teste precisa garantir que nenhum dos 4 vaza data futura).

## 6. EBITDA

```text
EBITDA = EBIT + Depreciação + Amortização
```

Só depois de validar D&A em dados CVM reais (não confirmado ainda — ver pendências,
§13). Nunca inventar D&A quando ausente — vira `quality_flag='missing_input'`, mesmo
padrão de `capex`/`free_cash_flow` hoje. Armazenar `quality_flag`, `quality_reason`,
`source_fact_ids` (nome real da coluna hoje é `source_document_ids`, ver nota de
nomenclatura no §14), `calculation_version`.

## 7. ROIC

Só para não-financeiras:

```text
NOPAT = EBIT × (1 - effective_tax_rate)
Invested_Capital = Debt + Equity - Cash
ROIC = NOPAT / Invested_Capital
```

Alíquota efetiva point-in-time quando confiável (derivável do DRE: imposto de renda /
lucro antes de impostos, do mesmo pacote de fatos). Se distorcida por evento
extraordinário ou input ausente: `quality_flag`, nunca valor inventado. **Nunca aplicar a
instituição financeira** — mesma lista de exclusão que já existe em
`_SECTOR_INADEQUATE_FOR_BANKS` em `fundamentals_metrics.py`.

## 8. Quality Score (0–100) — IMPLEMENTADO (2026-08-27)

> **Andamento**: `config/quality_nonfinancial_v1.yaml` (bandas do §17.1, fora do código) +
> `analytics/quality_score.py` + migration `20260827000005_quality_scores.sql` (aplicada) +
> CLI `compute-quality` + `tests/unit/test_quality_score.py` (21 testes). Rodado 2026-08-27:
> **PETR4 63,8** (rentabilidade 24,5/25; crescimento 0/15 — receita/lucro caindo do pico
> 2021), **VALE3 51,0** (lucro CAGR -50% desde o minério de 2021), **ITUB4** `score_status=
> 'incomplete'` perfil `bank` (§9/§18 — sem NIM/eficiência/Basileia). `calibration_status=
> 'provisional'` em todos. Componentes rastreáveis no jsonb `components`. Banda LARGA de
> consistência aplicada a PETR4/VALE3 (`commodity_exposed=true`).

QUALITY e VALUATION ficam **independentes** — o score de qualidade nunca inclui preço,
P/L, EV/EBITDA ou margem de segurança.

Perfil inicial, não-financeiras (`quality_nonfinancial_v1`):

```text
Rentabilidade          25
Solidez financeira     20
Geração de caixa       20
Consistência           20
Crescimento            15
                       ---
                       100
```

Priorizar histórico de 3-5 anos, não só o último período disponível (mesmo espírito de
`revenue_growth_yoy` hoje, mas numa janela maior). Metodologia de ponderação dentro de
cada componente (quais métricas exatas, como normalizar 0-100) ainda não definida —
pendência de decisão antes de codar (§13).

## 9. Bancos — perfil separado

**ITUB4 não pode ser tratado como PETR4/VALE3.** Arquitetura de perfis:

```text
profile = nonfinancial | bank
```

**Nunca calcular para bancos**: Net Debt/EBITDA, EV/EBITDA, FCF tradicional, ROIC
tradicional, FCFF DCF — já é o comportamento correto hoje em `fundamentals_metrics.py`
(`sector_inadequate`), a decisão é continuar essa disciplina na Fase 2, não reabrir.

Metodologia `quality_bank_v1` ainda não tem dados suficientes hoje (ver §13 — gap real,
não resolvido). Enquanto isso: **`score_status = 'incomplete'`, nunca um score artificial
construído sobre campos `sector_inadequate`.** Isso é regra dura, não sugestão.

## 10. DCF — CONCLUÍDO E APLICADO (2026-08-27)

> **Andamento**: módulos puros (`analytics/beta.py`, `analytics/wacc.py`,
> `analytics/dcf.py`, `analytics/residual_income.py`, `analytics/ddm.py`,
> `transforms/risk_free.py`) + fonte `sources/macro/tesouro.py` + orquestração
> `pipelines/valuation_dcf.py` + CLI `compute-dcf`. Configs: `config/wacc_v1.yaml`,
> `config/equity_risk_premium_snapshots.yaml` (ERP Damodaran jan/2026 curado à mão — §16.4).
> Migration `20260827000006_dcf_and_macro.sql` **aplicada**; `compute-dcf` **rodado**,
> 4 tabelas populadas, 12 `valuation_snapshots`.
>
> **Resultado (as_of 2026-08-27)**: PETR4 WACC 15,7% / FCFF R$100,7 bi (média 3a) /
> fair base **R$45,44** / MoS **+9%**. VALE3 WACC 16,3% / FCFF R$20,8 bi / fair
> **R$24,92** / MoS **−215%** (mercado precifica recuperação do minério que não está no
> trailing). ITUB4 (banco) coe 20% → Residual Income base **R$21,06**, DDM **R$20,50**,
> MoS ≈ −90% (ancorado no valor patrimonial, ~R$22; preço R$39 ≈ 1,75× book). Ver §34.
>
> **FCFF V1 = média de 3 anos de `NOPAT + D&A + capex`** (capex negativo), não
> `OCF + capex` (que é mais FCFE). Ignora ΔWC — refinamento futuro documentado.
> Bancos: FCFF não se aplica → Residual Income + DDM (`_run_bank`).
>
> **Pendência residual**: registrar `20260827000006` no ledger
> `supabase_migrations.schema_migrations` (o `exec_sql` RPC não tem permissão nesse schema).

**FCFF DCF**, arquitetura: 5 anos de projeção explícita + Terminal Value (Gordon Growth
como preferencial). **WACC por empresa, nunca uma taxa fixa global.**

Toda premissa é uma linha rastreável, não um número solto no código:

```text
risk_free_rate, beta, equity_risk_premium, cost_of_equity, cost_of_debt, tax_rate,
debt_weight, equity_weight, wacc, terminal_growth, forecast_growth
```

cada uma com `value`, `source`, `as_of_date`, `quality_flag`, `calculation_version`.

**Bancos usam metodologia diferente, nunca FCFF DCF** (ver §11.7 para achados sobre o que
falta): planejado inicialmente **Residual Income / Excess Return Model**, com **Dividend
Discount Model** como segunda metodologia opcional. Implementação separada por
registry/strategy:

```text
valuation_model = fcff | residual_income | ddm
```

## 11. Cenários e margem de segurança

Todo modelo de valor justo permite **pessimista / base / otimista**. Preferir faixa +
valor central + premissas em vez de só `R$ XX,XX` — não retornar falsa precisão.

```text
margin_of_safety = (fair_value - market_price) / fair_value
```

Preserva preço e fair value usados no cálculo. **Não vira recomendação automática de
compra/venda** — isso é decisão do usuário olhando o número, não um veredito do sistema.

## 12. `valuation_snapshots`

Todo cálculo reproduzível, nunca sobrescreve silenciosamente uma versão anterior:

```text
instrument/company, as_of_date, valuation_method, scenario, fair_value, market_price,
margin_of_safety, calculation_version, assumption_set_id, quality_flags, created_at
```

---

# ACHADOS DA INVESTIGAÇÃO (só planejamento — nenhum download foi persistido, arquivos
temporários de teste foram descartados)

## 13.1 Schema real dos arquivos FRE

Baixado e inspecionado `fre_cia_aberta_2010.zip` e `fre_cia_aberta_2024.zip` de
`https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FRE/DADOS/` (disponível ano a ano desde
2010 até 2026, mesmo padrão de URL do DFP/ITR já usado na Fase 1).

**Nomes reais dos arquivos** (levemente diferentes do que se assumia antes de baixar):
`fre_cia_aberta_capital_social_AAAA.csv` (não `fre_cia_capital_social`),
`fre_cia_aberta_capital_social_classe_acao_AAAA.csv`,
`fre_cia_aberta_distribuicao_capital_AAAA.csv`,
`fre_cia_aberta_distribuicao_capital_classe_acao_AAAA.csv` — mais um arquivo pai
`fre_cia_aberta_AAAA.csv` (índice de documentos, equivalente ao índice que o DFP/ITR já
usa) com colunas `CNPJ_CIA; DT_REFER; VERSAO; DENOM_CIA; CD_CVM; CATEG_DOC; ID_DOC;
DT_RECEB; LINK_DOC`.

**`DT_RECEB` é a fonte real de `available_from`** — mesmo papel que
`filing_received_at` já cumpre para DFP/ITR. Confirmado contra dado real: a FRE de
referência 2024-12-31 da Petrobras teve **28 versões** ao longo do ano (`VERSAO` 1 a 28+),
cada uma com seu próprio `DT_RECEB` — reforça que ignorar `available_from` aqui seria
tão grave quanto ignorar em fundamentos.

**Schema estável 2010→2024**: cabeçalho de `capital_social` e `distribuicao_capital`
byte-idêntico entre os dois anos testados. Diferente do DFP/ITR (que teve variação real
de formato entre anos na Fase 1), aqui não foi encontrada divergência — mas só 2 anos
foram testados, não os 15 inteiros.

**Colunas reais de `capital_social`**: `CNPJ_Companhia; Data_Referencia; Versao;
ID_Documento; Nome_Companhia; ID_Capital_Social; Tipo_Capital;
Data_Autorizacao_Aprovacao; Valor_Capital; Prazo_Integralizacao;
Quantidade_Acoes_Ordinarias; Quantidade_Acoes_Preferenciais; Quantidade_Total_Acoes`.

**Achado crítico não antecipado**: cada `(CNPJ, Data_Referencia, Versao)` tem **múltiplas
linhas**, uma por `Tipo_Capital` (`Capital Emitido`, `Capital Subscrito`, `Capital
Integralizado`, e para o Itaú também `Capital Autorizado`). Usar a linha errada
inflaciona ou distorce a contagem:

- **`Capital Integralizado`** (capital efetivamente pago) é a base economicamente
  correta para `shares_issued` — não `Capital Autorizado` (teto que o conselho pode
  emitir sem nova assembleia, um número diferente e maior/menor sem relação direta com
  ações em circulação hoje).
- Exemplo real (referência 2024-12-31, `Capital Integralizado`): PETR4 ON
  7.442.231.382 + PN 5.446.501.379 = 12.888.732.761 total. VALE3 ON 4.539.007.568 + PN
  **12** (praticamente zero) = 4.539.007.580. ITUB4 ON 5.454.119.395 + PN 5.330.429.488 =
  10.784.548.883.

**`distribuicao_capital` traz `Quantidade_Acoes_..._Circulacao`** — isso é
`shares_outstanding` de verdade (ações em circulação, excluindo tesouraria), a métrica
mais direta pra `market_cap`. **Não existe coluna literal de "ações em tesouraria"** em
nenhum dos arquivos inspecionados — é derivável por `shares_issued (capital_social,
Integralizado) - shares_outstanding (distribuicao_capital)`, nunca uma coluna direta.
`distribuicao_capital_classe_acao` quebra o PN em subclasses quando existem (não é o
caso das 3 empresas atuais, mas a estrutura já suporta).

## 13.2 Cobertura histórica real da quantidade de ações

Confirmada estrutura completa desde `Data_Referencia = 2010-01-01` (primeiro ano do
dataset FRE aberto da CVM) até 2024 para as 3 empresas — sem gap encontrado nos 2 anos
testados. Cobertura ano a ano completa (2011-2023) ainda não verificada individualmente
— só os extremos (2010, 2024) foram baixados nesta investigação.

**Achado histórico real, não documentado antes**: VALE3 tinha 2.108.579.618 ações PN em
2010 e **12** em 2024 — consistente com a unificação de classes da Vale em 2017 (fato
econômico real, não peculiaridade do dado). Isso confirma na prática a recomendação do
§4: VALE3 precisa ser tratada pela sua estrutura acionária real por período, não por uma
regra fixa "sempre ON+PN" — o modelo de dados já suporta isso naturalmente (é só uma
FRE diferente por ano), mas a lógica de agregação de market cap não pode assumir que o
número de classes é constante no tempo.

## 13.3 PETR3 e ITUB3 no yfinance desde 2010

**Confirmado contra a API real**: `PETR3.SA` e `ITUB3.SA`, `yf.download(start="2010-01-01",
auto_adjust=False)` — ambos devolveram **4124 linhas, 2010-01-04 a 2026-08-07**, exatamente
igual à cobertura já validada de PETR4/ITUB4 na Fase 1.1. Sem gap, sem surpresa.

**`get_shares_full` (fonte secundária de validação) testado contra PETR4**: devolve uma
série histórica real (1939 pontos, 2011-08-25 a 2026-04-10) — mas **os dados são
visivelmente ruidosos**: a mesma data (`2011-08-25`) aparece repetida dezenas de vezes com
valores diferindo em até ~8% entre si no mesmo dia (13,25B a 14,38B ações), o que não é
fisicamente plausível para uma contagem de ações que não muda diariamente nesse ritmo.
Confirma na prática a decisão do §3: isso serve para *validação cruzada* (checar se a
FRE está na ordem de grandeza certa), nunca como fonte primária ou point-in-time
confiável.

## 13.4 Necessidade de entidade `issuer`/`company`

**Achado de arquitetura, não fabricado — já está implícito no schema atual.**
`financial_statement_facts` e `cvm_documents` (desde a Fase 1) já são chaveados
primariamente por `cvm_code`/`cnpj` (identificadores reais da CVM, no nível da empresa),
com `instrument_id` como FK **nullable e secundária**:

```sql
-- supabase/migrations/20260808074711_initial_schema.sql
create table public.cvm_documents (
  ...
  cvm_code text not null,
  cnpj text,
  instrument_id bigint references public.instruments (instrument_id) on delete set null,
  ...
```

Ou seja: a granularidade "empresa" já existe nos fatos brutos via `cvm_code`/`cnpj`. O
que falta é a camada de cima — `analytics/fundamentals.py`,
`analytics/fundamentals_metrics.py`, `get_fundamentals_as_of`, tudo que a Fase 1 já
implementou, opera sobre `instrument_id` como se fosse "a empresa" (correto hoje, porque
cada empresa só tem 1 ticker cadastrado). No momento em que PETR3 for cadastrado como
segundo instrumento da mesma companhia, essa camada quebra a premissa implícita — dois
`instrument_id` diferentes vão querer os mesmos fundamentos (que são da empresa, não do
ticker).

**Conclusão**: precisa de uma tabela `companies`/`issuers` nova (chave natural = `cnpj`),
com `instruments.company_id` como FK, e a camada de fundamentos/valuation da Fase 2
precisa operar sobre `company_id`, não `instrument_id`. `fundamental_metrics` (Fase 1) e
qualquer tabela nova de valuation devem seguir o mesmo padrão. Isso é uma migration real
de arquitetura, não cosmética — mexe na chave usada por `get_fundamentals_as_of` e por
tudo que consome `fundamental_metrics` hoje. Precisa ser desenhada com cuidado antes de
codar (compatibilidade com o que já existe, sem quebrar Fase 1/1.1).

## 13.5 Dados necessários para WACC

Nenhum dado de mercado necessário para WACC existe hoje no banco. Lista do que falta,
por componente:

- **`risk_free_rate`**: taxa livre de risco Brasil. Candidato natural: Selic ou NTN-B
  (título público indexado). Nenhuma fonte de dado macro está integrada ainda — a tabela
  `macro_series` já existe no schema da Fase 1 (`'Reservado para Selic/CDI/IPCA/dolar/
  commodities. Estrutura criada; pipeline fica para depois.'`), mas está vazia e sem
  pipeline. **Pendência real, não resolvida.**
- **`beta`**: calculável a partir de `daily_returns` (já existe, 2010-2026) vs. o
  benchmark IBOV (já existe) por regressão — tecnicamente sem gap de dado, só falta a
  função de cálculo (janela de regressão, frequência, a decidir).
- **`equity_risk_premium`**: prêmio de risco de mercado. Não há fonte interna; é
  tipicamente um número de referência externo (ex.: séries de Damodaran para Brasil, que
  incluem risco-país) — precisa de decisão de fonte e de como versionar isso ao longo do
  tempo (não é um dado que a CVM ou a B3 publicam).
- **`cost_of_debt`**: aproximável por despesa financeira / dívida bruta média (derivável
  dos fundamentos já existentes, com ressalvas de qualidade) ou por spread de crédito
  observado — não validado ainda.
- **`tax_rate`**: mesma alíquota efetiva do §7 (ROIC), já derivável do DRE.
- **`debt_weight`/`equity_weight`**: derivável de `gross_debt`/`equity` já existentes.

**Resumo**: metade dos insumos do WACC são deriváveis do que já está no banco
(beta, tax_rate, pesos); a outra metade (`risk_free_rate`, `equity_risk_premium`,
validação de `cost_of_debt`) depende de fonte de dado macro/mercado que **não existe
ainda** — é o maior gap de dado do DCF, maior até que o de ações (que já tem fonte
identificada e validada).

## 13.6 Dados necessários para `quality_bank_v1`

Métricas hoje calculadas (`fundamentals_metrics.py`) que **não fazem sentido** para banco
já estão corretamente bloqueadas (`gross_debt`, `net_debt`, `capex`, `free_cash_flow`,
`ebit` → `sector_inadequate`). O que sobra utilizável hoje para ITUB4: `assets`,
`equity`, `revenue` (via `REVENUE_DESC_FIN`), `net_income`, `net_margin`, `roe`.

O que a literatura padrão usa para qualidade de banco e **não existe no banco hoje**:

- **NIM (margem financeira líquida)** — precisa de "receita de intermediação financeira
  líquida de despesa de intermediação" separada, não confirmado se o DRE do ITUB4 tem
  essa linha isolada na granularidade certa.
- **Índice de eficiência** (despesas operacionais / receitas) — não mapeado ainda.
- **Índice de Basileia / capital regulatório** — normalmente vem de Notas Explicativas ou
  do Pilar 3 do banco, não do DFP/ITR estruturado da CVM — fonte diferente, não
  investigada.
- **NPL ratio (inadimplência)** — mesma situação, provavelmente fora do escopo
  estruturado da CVM.
- **ROE já existe** e é a métrica de rentabilidade bancária mais padrão — ponto de
  partida real para uma primeira versão, mesmo incompleta.

**Conclusão**: `quality_bank_v1` hoje teria no máximo ROE + crescimento de
receita/lucro + solidez de patrimônio como componentes confiáveis — não é suficiente
para as 5 dimensões do perfil não-financeiro. Confirma a decisão do §9:
`score_status = 'incomplete'` é o resultado honesto até NIM/eficiência/Basileia serem
resolvidos, o que provavelmente exige uma fonte de dado adicional (não confirmada) além
do DFP/ITR já ingerido.

## 13.7 Modelo Residual Income para bancos

Não testado contra dado real (é metodologia, não uma fonte externa a validar), mas o
desenho depende diretamente do gap do §13.6: Residual Income exige `ROE`, custo de
capital próprio (`cost_of_equity`, um subconjunto do que falta no §13.5) e patrimônio
líquido projetado — todos parcialmente disponíveis (ROE e equity já existem;
cost_of_equity depende do mesmo gap de `risk_free_rate`/`equity_risk_premium`/`beta` do
DCF). Fórmula padrão (referência, não implementação):

```text
Residual_Income_t = Net_Income_t - (cost_of_equity × Equity_{t-1})
Fair_Value_Equity = Equity_atual + Σ (Residual_Income_projetado descontado)
```

Dividend Discount Model (segunda metodologia, mais simples) já tem o insumo principal
disponível hoje: `corporate_actions` (dividend/jcp históricos) — o que falta é
projeção futura de dividendos, decisão de metodologia (payout histórico médio vs.
crescimento assumido), não dado ausente.

---

## 14. Pendências reais que ficam para quando a implementação começar

1. ~~Confirmar cobertura ano-a-ano completa da FRE (só 2010 e 2024 foram testados).~~
   **FECHADA (2026-08-27)**: `sync-fre --from-year 2010` ingeriu 2010→2026 sem gap; schema
   estável nos 3 arquivos usados. Ver §3 e §24-27.
2. ~~Validar D&A (depreciação/amortização) contra o DFP/ITR real das 3 empresas.~~
   **FECHADA (2026-08-27)** — ver §28.
3. Decidir fonte de `risk_free_rate` e `equity_risk_premium` (nenhuma integrada hoje).
4. Desenhar a migration `companies`/`issuers` sem quebrar `instrument_id` como chave já
   usada em toda a Fase 1/1.1 (`get_fundamentals_as_of`, `fundamental_metrics`, eventos,
   event studies, notícias) — provavelmente aditiva (nova tabela + FK), não uma
   reescrita das tabelas existentes.
5. Nomenclatura: este documento usa `source_fact_ids` no §6 seguindo a intenção do
   pedido original, mas a coluna equivalente já existente em `fundamental_metrics` hoje
   se chama `source_document_ids` — decidir se a Fase 2 reaproveita esse nome ou
   introduz um novo, antes de codar.
6. Metodologia de ponderação dentro de cada componente do Quality Score (§8) — quais
   métricas exatas por componente, como normalizar para 0-100.
7. `quality_bank_v1`: decidir se vale a pena buscar fonte adicional (Pilar 3/Basileia) ou
   aceitar `score_status='incomplete'` como resultado permanente até o universo de dados
   mudar.

**Nenhum destes itens é implementado até autorização explícita separada para começar a
Fase 2.**

---

# RODADA FINAL DE INVESTIGAÇÃO (2026-08-09)

Fecha os pontos que ficaram pendentes em §13.5-13.7 e §14. Mesma regra de sempre: nenhuma
fonte foi assumida sem confirmar contra a API/arquivo real. Ainda **nenhum código ou
migration desta fase foi escrito**.

## 15. Risk-free rate — fontes investigadas (revisado 2026-08-09, ver §21)

> **Revisão**: a recomendação original desta seção (Tesouro IPCA+ como fonte primária,
> WACC real) foi **substituída** pela decisão em §21 — DCF V1 é **nominal em BRL**,
> risk-free V1 vem do **Tesouro Prefixado**, não do IPCA+. O investigado abaixo (§15.1 e
> §15.2) continua válido como achado factual (as fontes existem e funcionam do jeito
> descrito) — só a escolha de qual usar como V1 mudou. Ver §21.2 para a metodologia
> nominal atual.

### 15.1 Selic (meta Copom, série BCB-SGS 432) e CDI (série BCB-SGS 12)

Testado ao vivo contra `https://api.bcb.gov.br/dados/serie/bcdata.sgs.{codigo}/dados`
(Sistema Gerenciador de Séries Temporais do Banco Central, API pública, sem
autenticação). Confirmado: `GET .../bcdata.sgs.432/dados?formato=json&dataInicial=01/01/
2010&dataFinal=10/01/2010` devolve `[{"data":"01/01/2010","valor":"8.75"}, ...]` — Meta
Selic de fato em 8,75% a.a. no período, valor real batendo com o histórico público
conhecido. Série 12 (CDI diário) também confirmada, formato análogo.

```text
fonte:              BCB-SGS (séries 432 = Meta Selic, 12 = CDI, 11 = Selic over)
cobertura histórica: décadas (série 432 cobre desde a criação do regime de metas, 1999+;
                     mais que suficiente para 2010-2026, que é a janela real do projeto)
frequência:         diária (a Meta Selic só muda em reuniões do Copom, então o valor
                     fica constante entre reuniões -- a série diária é preenchida com o
                     valor vigente)
point-in-time:      SIM, sem esforço extra -- decisão de política monetária é pública no
                     mesmo dia, nunca há "reapresentação" de Selic
API/download:       JSON via HTTP GET, sem token, sem rate limit documentado agressivo
reprodutibilidade:  alta -- endpoint estável, formato simples, já teria adapter parecido
                     com o de brapi/yfinance (mesmo padrão de retry/timeout)
adequação a WACC:   NOMINAL. Selic/CDI são taxas nominais (não descontam inflação) --
                     serve para um WACC nominal, que depois desconta fluxos de caixa
                     nominais (também em R$ correntes, não deflacionados)
vantagens:          fonte oficial, gratuita, sem ambiguidade, já é a taxa mais citada em
                     valuation brasileiro em geral
limitações:         é a taxa de política monetária/interbancária, não uma taxa de título
                     público de longo prazo -- para um DCF de 5+ anos de horizonte,
                     usar Selic/CDI corrente como proxy de "livre de risco de longo prazo"
                     é uma simplificação comum na prática de mercado brasileira, mas
                     tecnicamente imprecisa (a curva de juros tem inclinação; Selic é a
                     ponta curta)
```

### 15.2 Tesouro IPCA+ (NTN-B) via Tesouro Transparente

Testado ao vivo: dataset CKAN `df56aa42-484a-4a59-8184-7676580c81e3`, recurso
`796d2059-14e9-44e3-80c9-2d9e30b405c1`
(`https://www.tesourotransparente.gov.br/ckan/dataset/.../PrecoTaxaTesouroDireto.csv`).
Confirmado: CSV único de 14,4 MB, **174.699 linhas**, `Tipo Titulo` inclui
`"Tesouro IPCA+"`, `"Tesouro IPCA+ com Juros Semestrais"`, `"Tesouro Selic"`,
`"Tesouro Prefixado"` (e outros mais recentes: Educa+, Renda+). `Data Base` cobre
**2004-12-31 até 2026-08-07** (dado de hoje). Colunas: `Tipo Titulo; Data Vencimento;
Data Base; Taxa Compra Manha; Taxa Venda Manha; PU Compra Manha; PU Venda Manha;
PU Base Manha`.

```text
fonte:              Tesouro Transparente (Tesouro Nacional), CSV único e diário
cobertura histórica: 2004-12-31 -> hoje, folga grande sobre a janela 2010-2026 do projeto
frequência:         diária
point-in-time:      SIM -- é a taxa de negociação do dia, publicada no mesmo dia
API/download:       download direto de um CSV único (14 MB, todos os títulos e datas
                     juntos -- não paginado, não precisa de chamadas repetidas por data
                     como BCB-SGS; mais pesado por chamada, mais simples de processar)
reprodutibilidade:  alta -- é literalmente o arquivo que o Tesouro Direto usa para
                     precificar os próprios títulos ao investidor final
adequação a WACC:   REAL -- Tesouro IPCA+ é indexado à inflação, então sua taxa é uma
                     taxa de juros REAL diretamente observável, sem precisar deflacionar
                     nada. Metodologicamente é a escolha mais correta para um WACC REAL
                     (que desconta fluxos de caixa também em termos reais)
vantagens:          taxa de longo prazo de verdade (títulos com vencimento em 2035,
                     2045 etc. aparecem no arquivo -- dá pra escolher o vencimento mais
                     próximo do horizonte do DCF, em vez de usar só a ponta curta)
limitações:         arquivo único grande (14 MB) tem que ser baixado inteiro e filtrado
                     localmente -- sem endpoint de consulta por data/título isolado
                     confirmado nesta investigação; múltiplos títulos "Tesouro IPCA+"
                     coexistem com vencimentos diferentes no mesmo dia -- precisa de
                     regra clara de qual vencimento usar (ex.: o mais próximo do
                     horizonte de projeção do DCF, tipicamente 5-10 anos)
```

### 15.3 Recomendação (SUPERSEDIDA — ver §21.2)

~~Fonte primária: Tesouro IPCA+~~ — decisão revista em 2026-08-09: DCF V1 é nominal, não
real. Tesouro IPCA+ **permanece suportado como metodologia futura** (`dcf_real`, §21.3),
só deixou de ser o padrão da primeira versão. Selic/CDI continuam como fonte secundária
de validação, papel inalterado.

## 16. Equity Risk Premium — metodologia investigada (revisado 2026-08-09, ver §21.4-21.5)

> **Revisão**: a composição proposta originalmente (ERP maduro + EMBI+/Ipeadata como
> componente automatizável de risco-país) **não é mais a recomendação V1** — a série
> EMBI+ usada (§16.3) foi confirmada **descontinuada em 30/07/2024** (achado do
> usuário, verificado nesta revisão, ver §21.5). Passa a ser usada só como validação
> histórica até essa data, nunca como fonte corrente. A fonte primária de ERP passa a
> ser diretamente os datasets de Damodaran/NYU Stern (country risk premium), com
> disciplina explícita de snapshot point-in-time (§21.4). O investigado abaixo
> permanece como registro do que foi avaliado e por quê certas partes foram
> rejeitadas/rebaixadas.

**Não existe uma série histórica gratuita, ponto-a-ponto, pronta para consumo, de "ERP
Brasil" no sentido em que existe para Selic ou câmbio.** ERP não é uma taxa observada
diretamente no mercado (diferente de Selic/CDI/NTN-B) — é sempre uma **estimativa**,
construída a partir de outras variáveis. Duas abordagens reproduzíveis foram avaliadas:

### 16.1 ERP implícito/realizado calculado internamente (dado que o projeto já tem)

`daily_prices`/`daily_returns` do IBOV já cobrem 2010-2026. Um ERP histórico realizado
(retorno do IBOV no período menos a taxa livre de risco no mesmo período, anualizado)
é **100% calculável com dado que já está no banco**, sem nenhuma fonte nova. Limitação
honesta: é um ERP *realizado*, não *esperado* — janelas de poucos anos de bolsa
brasileira são extremamente ruidosas para servir de estimativa de prêmio de risco
futuro (o método clássico de ERP realizado geralmente pede décadas de dado para ser
minimamente estável, e o projeto só tem 2010+).

### 16.2 Referência externa consagrada — Damodaran (NYU Stern)

As planilhas públicas de Damodaran (`pages.stern.nyu.edu/~adamodar/`) são a referência
mais citada de mercado para ERP por país, incluindo Brasil, combinando prêmio de risco
maduro (EUA) + prêmio de risco-país. **Não são uma API nem uma série histórica
point-in-time redistribuível** — são planilhas atualizadas manualmente pelo autor,
tipicamente uma vez por ano, sem um arquivo único com todo o histórico de valores
anuais publicados ano a ano de forma consistente para download automatizado. Usável como
**ponto de referência atual/pontual**, não como série contínua para preencher
`valuation_snapshots` de datas passadas sem registrar manualmente o valor vigente em
cada época — o que é factível (são poucos números por ano), mas é curadoria manual, não
um pipeline automatizado como o resto do projeto.

### 16.3 Componente de risco-país — EMBI+/Ipeadata (rebaixado a validação histórica)

**Reverificado em 2026-08-09** (o achado original desta seção tinha ficado incompleto —
não tinha checado o fim da série nem a unidade do campo). Confirmado ao vivo contra
`GET http://www.ipeadata.gov.br/api/odata4/ValoresSerie(SERCODIGO='JPM366_EMBI366')`:

- **10.126 pontos**, 1994-04-29 até **2024-07-30** (último ponto: `{"VALDATA":
  "2024-07-30T00:00:00-03:00","VALVALOR":228.0}`) — a série **para** aí. Não há dado
  mais recente sob este código.
- Metadados da própria série (`GET .../Metadados('JPM366_EMBI366')`) confirmam:
  `"SERNOME":"EMBI + Risco-Brasil - INATIVA"` — a Ipeadata já marca a série como
  inativa/descontinuada explicitamente no nome.
- Unidade confirmada pelo comentário oficial da série: **ponto-base**, `"dez
  pontos-base equivalem a um décimo de 1%"` — ou seja 100 pb = 1,00%, 228 pb (último
  valor, jul/2024) = 2,28%. Bate exatamente com o exemplo de conversão já esperado.

**Conclusão**: EMBI+/Ipeadata (`JPM366_EMBI366`) **não serve como fonte corrente** de
risco-país (não teria dado para nenhuma data depois de 30/07/2024). Uso rebaixado para:
validação histórica de spread soberano, sensitivity analysis, e comparação/checagem de
ordem de grandeza contra o `country_default_spread` que a Fase 2 vai usar de outra
fonte (§21.4) — nunca como fonte primária do ERP atual.

### 16.4 Solução metodológica original (substituída — ver §21.4)

A composição proposta originalmente (ERP maduro + EMBI+/Ipeadata automatizado) deixou de
fazer sentido como estava desenhada, porque o componente que se pretendia automatizar
(EMBI+) está descontinuado desde 2024. A metodologia V1 atual está em §21.4 — usa os
próprios datasets de país da Damodaran (que já trazem `mature_market_erp`,
`country_default_spread` e `country_risk_premium` juntos, do mesmo snapshot, sem
precisar compor fontes diferentes) com disciplina explícita de point-in-time por
snapshot.

## 17. Quality Score não-financeiro — metodologia fechada (`quality_nonfinancial_v1`)

Fecha os componentes de `quality_nonfinancial_v1` (distribuição macro já aprovada:
Rentabilidade 25 / Solidez financeira 20 / Geração de caixa 20 / Consistência 20 /
Crescimento 15).

**Janela histórica**: últimos **5 anos fiscais fechados** (DFP anual, não ITR) sempre
que disponíveis; mínimo de **3 anos** para o score ser considerado válido (ver limiar
abaixo). Escolhido 5 como teto porque é o período que `PROJETO.txt` já usa como
referência em outras partes do produto, e 3 como piso porque é o menor número que ainda
permite calcular uma métrica de variabilidade (desvio) com algum sentido.

**Componentes por bloco** (usando só métricas que já existem em `fundamental_metrics`
hoje, nenhuma nova é necessária para fechar esta metodologia):

```text
Rentabilidade (25):
  - net_margin médio dos últimos N anos       (peso 12)
  - roe médio dos últimos N anos               (peso 13)

Solidez financeira (20):
  - net_debt / equity (última posição disponível)     (peso 12)
  - tendência de net_debt/equity nos últimos N anos
    (melhorando / estável / piorando)                  (peso 8)

Geração de caixa (20):
  - free_cash_flow / revenue médio (margem de FCF)     (peso 12)
  - free_cash_flow / net_income médio
    (conversão de lucro em caixa -- >1 é sinal forte)   (peso 8)

Consistência (20):
  - coeficiente de variação (desvio padrão / média) de
    net_margin no período -- quanto menor, melhor       (peso 10)
  - fração de anos do período com net_income positivo   (peso 10)

Crescimento (15):
  - CAGR de revenue no período                          (peso 8)
  - CAGR de net_income no período                        (peso 7)
```

**Normalização 0-100**: por **bandas absolutas fixas e versionadas**, não por percentil
entre empresas. Decisão explícita (ver log §20): com só 3 empresas no universo hoje,
normalização por percentil/ranking entre pares não é estatisticamente defensável — uma
empresa automaticamente vira "a pior" só por ser a 3ª de 3, mesmo com fundamentos
saudáveis em termos absolutos.

### 17.1 Bandas propostas (revisão 2026-08-09 — valores concretos, não mais placeholder)

Interpolação **linear** entre os pontos de referência de cada linha (ex.: um `net_margin`
mediano de 5,5% fica entre a banda de 25 e 50 pontos, interpolado). Cada banda tem
`métrica`, `peso`, `janela`, os 5 marcadores (0/25/50/75/100) e o tratamento de negativo/
outlier/missing específico daquela métrica. Valores calibrados por referência de mercado
brasileiro geral (Selic histórica ~10-13% como piso de referência de retorno,
comportamento típico de large caps B3) — **não validados estatisticamente contra um
universo grande** (só 3 empresas hoje), por isso `calibration_status = 'provisional'`
mesmo depois de implementado (ver §17.4).

```text
BLOCO RENTABILIDADE (peso 25)

  net_margin (mediana, janela 3-5 anos)                              peso 12
    0 pts: <= 0%      25 pts: 3%      50 pts: 8%      75 pts: 15%     100 pts: >= 25%
    negativo -> ancora no piso (0 pts), nunca extrapola abaixo de 0
    outlier -> winsoriza em [-50%, 60%] antes de entrar na mediana
    missing -> exclui o ano do cálculo (nunca vira 0%)

  roe (mediana, janela 3-5 anos)                                     peso 13
    0 pts: <= 0%      25 pts: 5%      50 pts: 12%     75 pts: 18%     100 pts: >= 25%
    negativo -> ancora no piso; outlier -> winsoriza em [-100%, 80%]
    missing -> exclui o ano

BLOCO SOLIDEZ FINANCEIRA (peso 20)

  net_debt / equity (última posição disponível)                      peso 12
    0 pts: >= 1.5x    25 pts: 1.0x    50 pts: 0.5x    75 pts: 0.2x    100 pts: <= 0x
    (net_debt <= 0, ou seja caixa líquido positivo, é o teto -- 100 pts)
    outlier -> winsoriza em [-1.0x, 3.0x] antes de pontuar
    missing -> quality_flag='missing_input', bloco recalculado sem este subitem

  tendência de net_debt/equity (últimos N anos: melhorando/estável/piorando)  peso 8
    piorando: 0 pts   estável: 60 pts   melhorando: 100 pts
    (classificação discreta, não interpolada -- comparação simples do primeiro vs.
    último ano da janela, com banda de +-10% tratada como "estável")
    missing -> exige pelo menos 2 anos com net_debt/equity válido; se não houver,
    quality_flag='insufficient_history' só neste subitem

BLOCO GERAÇÃO DE CAIXA (peso 20)

  free_cash_flow / revenue (mediana, janela 3-5 anos)                peso 12
    0 pts: <= 0%      25 pts: 3%      50 pts: 8%      75 pts: 15%     100 pts: >= 20%
    negativo -> ancora no piso; outlier -> winsoriza em [-50%, 50%]
    missing -> exclui o ano (comum em anos de capex pesado sem free_cash_flow calculável)

  free_cash_flow / net_income (mediana, janela 3-5 anos)             peso 8
    0 pts: <= 0x      25 pts: 0.5x    50 pts: 0.8x    75 pts: 1.0x    100 pts: >= 1.3x
    negativo -> ancora no piso; outlier -> winsoriza em [-2.0x, 3.0x]
    missing -> exclui o ano; se net_income <= 0 no ano, a razão fica indefinida --
    excluído desse ano específico, não vira 0 nem infinito

BLOCO CONSISTÊNCIA (peso 20)

  coeficiente de variação (desvio/média) de net_margin no período    peso 10
    -- banda PADRÃO (commodity_exposed = false):
    0 pts: >= 0.60    25 pts: 0.40    50 pts: 0.25    75 pts: 0.12    100 pts: <= 0.05
    -- banda LARGA (commodity_exposed = true, ex.: PETR4, VALE3):
    0 pts: >= 1.00    25 pts: 0.70    50 pts: 0.45    75 pts: 0.25    100 pts: <= 0.12
    outlier -> winsoriza net_margin de cada ano (mesma banda do bloco Rentabilidade)
    ANTES de calcular o CV, para um ano extremo não inflar o desvio artificialmente
    missing -> exige >= 3 anos válidos; menos que isso -> insufficient_history

  fração de anos com net_income positivo (janela 3-5 anos)           peso 10
    0 pts: 0%         25 pts: 40%     50 pts: 60%     75 pts: 80%     100 pts: 100%
    (não é winsorizado -- é uma contagem, não uma razão contínua)
    missing -> ano sem net_income calculável não conta nem como positivo nem negativo,
    é excluído do denominador (não penaliza nem favorece)

BLOCO CRESCIMENTO (peso 15)

  CAGR de revenue (janela 3-5 anos)                                  peso 8
    0 pts: <= -5%     25 pts: 0%      50 pts: 5%      75 pts: 10%     100 pts: >= 15%
    outlier -> winsoriza CAGR em [-50%, 50%] antes de pontuar
    missing -> exige revenue do primeiro E do último ano da janela; sem os dois
    extremos, CAGR não é calculável -> quality_flag='missing_input' neste subitem

  CAGR de net_income (janela 3-5 anos)                               peso 7
    0 pts: <= -10%    25 pts: 0%      50 pts: 8%      75 pts: 15%     100 pts: >= 25%
    (banda mais tolerante a queda que revenue -- lucro é estruturalmente mais
    volátil que receita, especialmente em cíclicas)
    outlier -> winsoriza em [-80%, 80%]; missing -> mesma regra do CAGR de revenue
```

**Tratamento de outliers**: cada métrica é **winsorizada** (limitada a um piso/teto
antes de entrar na média) antes de agregar no bloco — evita que um ano com patrimônio
líquido perto de zero (ROE explode matematicamente) domine a média dos 5 anos. Além
disso, **Rentabilidade e Geração de caixa usam mediana, não média**, dos N anos —
mediana já é naturalmente resistente a um único ano extraordinário (venda de ativo,
write-off pontual) sem precisar descartar dado nenhum.

**Tratamento de `missing_input`**: um ano com `quality_flag != 'ok'` para uma métrica
específica é **excluído do cálculo daquela métrica** (não vira zero, não polui a média/
mediana) — mesmo princípio que `fundamentals_metrics.py` já aplica com
`_ok_or_missing`. Se **menos de 3 anos utilizáveis** sobrarem para um bloco inteiro
depois de excluir os `missing_input`, aquele bloco específico fica com `quality_flag=
'insufficient_history'` e não entra na soma final ponderada — o score final é
recalculado só com os blocos disponíveis, escalado proporcionalmente (ex.: se
"Crescimento" não pôde ser calculado, os 15 pontos dele não contam nem a favor nem
contra, e o score final é `soma_dos_blocos_disponíveis / peso_total_dos_blocos_disponíveis
× 100`).

**Peso mínimo para score válido**: se **menos de 60% do peso total** (60 dos 100 pontos)
tiver dado suficiente depois das exclusões acima, o resultado inteiro vira
`score_status = 'incomplete'` em vez de um score parcial — abaixo desse limiar, o
número ficaria enganoso demais para ser exibido como se fosse comparável a um score
completo.

**Empresas cíclicas/commodities**: `instruments.commodity_exposed` **já existe no
schema hoje** (Fase 1, não é migration nova) e ainda não é usado em nenhum lugar do
código. `commodity_exposed = true` é o caso de PETR4 e VALE3 hoje — usa a banda LARGA
de Consistência já especificada em §17.1, não a banda padrão.

**Como um único ano não domina o score**: três mecanismos combinados, não um só —
(1) janela de N anos em vez de 1, (2) mediana em vez de média nos blocos mais sensíveis
a evento pontual (Rentabilidade, Geração de caixa), (3) winsorização por métrica antes
de agregar. Nenhum desses é redundante: a janela protege contra "só olhar o último ano",
a mediana protege contra um outlier dentro da janela, a winsorização protege contra um
outlier absurdo o suficiente para distorcer até a mediana em janelas curtas (N=3).

**Preço/valuation nunca entra no Quality Score** — nenhum dos componentes acima usa
preço de mercado, P/L, EV/EBITDA ou margem de segurança, por decisão já aprovada
anteriormente e mantida sem exceção aqui.

### 17.2 Bandas em configuração, não hard-coded

Todas as bandas do §17.1 (os 5 marcadores de cada métrica, os pesos, a janela, os
limiares de winsorização) ficam em `config/quality_nonfinancial_v1.yaml`, no mesmo
espírito de `config/settings.yaml`/`config/news_taxonomy.yaml` já usados na Fase 1 —
nunca dentro da função Python. Isso permite recalibrar sem tocar em código, e o
`calculation_version`/`quality_nonfinancial_v1` no nome do arquivo já amarra a versão
da metodologia ao arquivo de configuração que a implementa (uma v2 do dia que as bandas
forem recalibradas vira um novo arquivo, nunca uma edição silenciosa do v1).

### 17.3 `calibration_status = 'provisional'`

Mesmo depois de implementado, todo resultado de `quality_nonfinancial_v1` carrega
`calibration_status = 'provisional'` — as bandas do §17.1 foram calibradas por
referência de mercado geral (Selic histórica, comportamento típico de large caps B3),
**não por validação estatística contra um universo grande** (hoje só 3 empresas). Isso
fica registrado no output do score, não só neste documento, para que ninguém trate um
score `quality_nonfinancial_v1` como definitivo antes do universo crescer o suficiente
para calibração real (tema ligado a `docs/survivorship_bias_plan.md` — expansão de
universo é pré-requisito indireto para tirar `provisional` daqui).

## 18. Bancos — status confirmado, sem pipeline novo agora

Reconfirma a decisão já registrada em §9/§13.6, sem mudança de escopo: `quality_bank_v1`
permanece **incompleto por desenho** enquanto NIM, índice de eficiência, Basileia e
inadimplência não tiverem fonte confirmada. **Nenhuma integração com Pilar 3/Basileia é
aberta nesta rodada** — só o registro de que dados futuros necessários são: margem
financeira líquida (NIM), índice de eficiência operacional, índice de Basileia
(capital regulatório) e índice de inadimplência (NPL) — nenhum desses quatro tem fonte
estruturada confirmada no DFP/ITR já ingerido; investigação de fonte alternativa
(provavelmente Pilar 3 do próprio banco ou relatórios do Banco Central sobre
instituições financeiras) fica para uma rodada futura, fora do escopo desta.

Residual Income/DDM continuam **planejados, não implementados** — ambos dependem de
`cost_of_equity`, que por sua vez depende do risk-free rate (§15, já resolvido nesta
rodada) e do ERP (§16, metodologia definida nesta rodada, mas com o componente
`mature_market_erp` ainda exigindo curadoria manual periódica, não uma fonte 100%
automatizável). Ou seja: o bloqueador de dado para Residual Income/DDM ficou menor
depois desta investigação (risk-free e risco-país resolvidos), mas não zerado (ERP de
mercado maduro continua sem fonte automatizável).

## 19. Migration `companies`/`issuer` — APLICADA E VERIFICADA (2026-08-27)

> **Andamento**: aplicada ao banco remoto via SQL editor do dashboard e verificada.
> - `supabase/migrations/20260827000001_companies_issuer_entity.sql` — DDL do §19.2 +
>   backfill do §19.3 + cadastro de PETR3/ITUB3 **inativos** (`cvm_code` NULL para não
>   colidir com `instruments_cvm_code_key`).
> - `supabase/migrations/20260827000002_reclaim_free_tier_space.sql` — dropa
>   `financial_statement_facts_company_idx` (índice de ~15 MB sem uso até a camada de
>   valuation existir; org no Free Plan do Supabase acima da cota de Database Size).
> - `pipelines/fundamentals_ingest.py::target_instruments` filtra `active = true` —
>   ingestão de fundamentos continua de instrumento único por CNPJ mesmo com as
>   classes ON cadastradas. Teste: `tests/unit/test_fundamentals_ingest.py`.
> - `docs/data_dictionary.md` atualizado.
>
> **Verificação** (Bloco C, 2026-08-27): companies=3, instruments c/ company_id=5,
> s/ company_id=1 (IBOV), facts/docs/metrics s/ company_id=0, PETR3/ITUB3 inativos=2.
>
> Ativação de PETR3/ITUB3 (preço, notícia) e agregação de fundamentos/market cap por
> `company_id` ficam para o bloco de market cap (§4).

### 19.0 Desenho original (mantido como registro)

### 19.1 Achado confirmado que fundamenta o desenho

`cvm_documents` e `financial_statement_facts` (Fase 1, já em produção) são chaveados
primariamente por `cvm_code`/`cnpj` (nível empresa), com `instrument_id` como FK
**nullable**, secundária. `instruments.cnpj` já existe e já está preenchido
corretamente para os 3 tickers atuais (mesmo CNPJ que aparece em `company_mapping.yaml`,
confirmado por CVM). Ou seja: **o identificador de empresa correto (CNPJ) já está no
banco hoje** — falta só a tabela que o formaliza como entidade própria, com PK e FKs de
verdade, em vez de ficar implícito num campo texto repetido em várias tabelas.

### 19.2 DDL proposto (markdown, não executado)

```sql
-- Nova tabela: entidade emissor/companhia, separada de instrumento/ticker.
create table public.companies (
  company_id         bigint generated always as identity primary key,
  cnpj               text        not null unique,
  cvm_code           text,
  legal_name         text        not null,
  sector             text,
  subsector          text,
  segment            text,
  financial_company  boolean     not null default false,
  utility            boolean     not null default false,
  commodity_exposed  boolean     not null default false,
  holding            boolean     not null default false,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now()
);

comment on table public.companies is
  'Entidade emissora (empresa legal, chave = CNPJ). Um instrumento (ticker/classe de '
  'acao) sempre pertence a uma companhia; uma companhia pode ter varios instrumentos '
  '(ex.: PETR3 + PETR4).';

-- Aditivo: instruments ganha uma FK nova, nada existente muda de nome ou tipo.
alter table public.instruments
  add column company_id bigint references public.companies (company_id);

-- Aditivo: fatos/documentos CVM ganham a mesma FK, em paralelo ao instrument_id ja
-- existente (que continua funcionando exatamente como hoje para a Fase 1/1.1).
alter table public.cvm_documents
  add column company_id bigint references public.companies (company_id);
alter table public.financial_statement_facts
  add column company_id bigint references public.companies (company_id);

-- share_count_history (Fase 2, tabela nova, ainda nao criada) referencia company_id
-- desde o inicio, nunca instrument_id -- quantidade de acoes e um fato por classe
-- dentro de uma companhia, nao um fato "do ticker".
```

### 19.3 Migração de dado (proposta, não executada)

1. `insert into companies (cnpj, cvm_code, legal_name, ...) select distinct cnpj,
   cvm_code, legal_name, ... from instruments where cnpj is not null` — 3 linhas hoje
   (PETR4, VALE3, ITUB4), uma por CNPJ distinto.
2. `update instruments set company_id = companies.company_id from companies where
   instruments.cnpj = companies.cnpj` — preenche os 3 instrumentos existentes.
3. `update cvm_documents/financial_statement_facts set company_id = ... where cnpj =
   companies.cnpj` — preenche o histórico já ingerido (230 mil fatos), sem re-baixar
   nada da CVM.
4. Cadastrar **PETR3** e **ITUB3** como novos `instruments`, cada um com o mesmo
   `company_id` que PETR4/ITUB4 já têm (mesmo CNPJ). VALE3 continua sozinho no seu
   `company_id` (hoje é mono-instrumento; nada muda para ela além de ganhar a FK).
5. `IBOV` (benchmark) e qualquer instrumento sem CNPJ real (índice, não empresa) ficam
   com `company_id null` — a coluna é nullable exatamente por isso, um benchmark não é
   uma companhia.

### 19.4 Por que isso não quebra Fase 1/1.1

Nenhuma coluna existente é renomeada, removida ou tem o tipo alterado.
`get_fundamentals_as_of(instrument_id, ...)`, `fundamental_metrics`, `events`,
`event_studies`, `news_company_links` — tudo que already existe continua consultando por
`instrument_id` exatamente como hoje, bit a bit igual. A Fase 2 introduz um caminho
**novo e paralelo** (`company_id`) para o que precisa ser agregado no nível empresa
(fundamentos consolidados entre PETR3+PETR4, market cap somado, quantidade de ações) sem
tocar no caminho antigo. É estritamente aditivo.

### 19.5 O que fica para quando a implementação começar

- Nome exato de índices/constraints (não desenhado em detalhe aqui, é mecânico).
- Decidir se `financial_statement_facts.company_id` fica `not null` depois de
  populado (hoje toda linha tem `cnpj`, então tecnicamente poderia; deixar nullable no
  desenho por segurança, mesma lógica de `instrument_id` hoje).
- Trigger de `updated_at` (mesmo padrão `set_updated_at()` já usado em outras tabelas).

## 20. Log de decisões novas desta rodada

**As duas primeiras entradas abaixo foram SUPERSEDIDAS em 2026-08-09 (mesmo dia,
rodada seguinte) — ver §21 e §22. Mantidas aqui porque a regra deste documento é nunca
apagar uma decisão registrada, só marcar como substituída.**

```text
[SUPERSEDIDA -- ver §21.2] Decisão: risk-free rate primária = Tesouro IPCA+ (Tesouro
         Transparente); secundária = Selic/CDI (BCB-SGS).
Motivo: Tesouro IPCA+ é taxa REAL de longo prazo observada diretamente, adequada a um
        WACC real; Selic/CDI é taxa de política monetária de curtíssimo prazo, mais
        simples de consultar mas metodologicamente menos apropriada como taxa de
        desconto de DCF multi-anual sem correção de prazo.
Alternativas rejeitadas: usar só Selic/CDI (mais simples de implementar, mas pior
        adequação metodológica); usar EMBI+ diretamente como risk-free (é prêmio de
        risco-país, não taxa livre de risco -- categoria errada).
Impacto: define a fonte de dado que o WACC (§10 da seção de decisões) vai consumir;
        nenhum impacto em Fase 1/1.1.
Data: 2026-08-09
Status: SUPERSEDIDA em 2026-08-09 -- DCF V1 é nominal (não real), risk-free V1 vem do
        Tesouro Prefixado (não do IPCA+). Ver §21.2.

[SUPERSEDIDA -- ver §21.4/§21.5] Decisão: ERP Brasil = composição versionada (ERP
        mercado maduro, curadoria manual periódica + prêmio de risco-país via EMBI+
        Brasil/Ipeadata, automatizável e point-in-time).
Motivo: não existe fonte gratuita de "ERP Brasil" como série histórica pronta; compor
        as duas partes com proveniência registrada é mais honesto que usar um número
        solto sem fonte, e mais correto que fingir uma série automática que não existe.
Alternativas rejeitadas: ERP realizado só com dado interno (IBOV 2010-2026) -- rejeitado
        como fonte ÚNICA por ser jenela curta demais pra estimar prêmio de risco
        esperado com estabilidade, mas mantido como validação cruzada possível.
Impacto: define estrutura de `equity_risk_premium_assumptions` (tabela nova, ainda não
        criada); o componente "mercado maduro" continua exigindo atualização manual
        periódica, não 100% automatizável -- limitação real, registrada, não escondida.
Data: 2026-08-09
Status: SUPERSEDIDA em 2026-08-09 -- o componente EMBI+/Ipeadata que se pretendia usar
        como automatizável está descontinuado desde 30/07/2024 (confirmado nesta
        revisão). ERP V1 passa a vir direto dos datasets de país da Damodaran
        (mature_market_erp + country_risk_premium do mesmo snapshot). Ver §21.4.

Decisão: quality_nonfinancial_v1 fechado -- bandas absolutas (não percentil entre
        pares), mediana nos blocos sensíveis a outlier, winsorização por métrica,
        janela de 3-5 anos, limiar de 60% do peso para score válido.
Motivo: universo de só 3 empresas torna normalização por percentil estatisticamente
        sem sentido (ranking de 3 sempre produz um "pior", mesmo com fundamentos bons
        em termos absolutos); mediana + winsorização protegem contra ano
        extraordinário sem descartar dado.
Alternativas rejeitadas: normalização por percentil/z-score entre as 3 empresas
        (rejeitada pelo motivo acima -- fica correta só quando o universo crescer,
        tema relacionado a `docs/survivorship_bias_plan.md`); usar só o último ano
        fiscal em vez de janela (rejeitada -- não atende ao pedido de "não deixar um
        único ano dominar o score").
Impacto: metodologia pronta para implementação futura; nenhuma métrica nova precisa
        ser calculada além do que `fundamental_metrics` já produz.
Data: 2026-08-09

Decisão: entidade `companies` desenhada como tabela nova + FK aditiva em
        `instruments`/`cvm_documents`/`financial_statement_facts`, nunca substituindo
        `instrument_id`.
Motivo: `cvm_documents`/`financial_statement_facts` já usam `cnpj` como identificador
        de fato desde a Fase 1 -- a entidade "empresa" já existe implicitamente nos
        dados, só falta formalizar como tabela com PK própria. Caminho aditivo garante
        zero risco de quebrar Fase 1/1.1.
Alternativas rejeitadas: reaproveitar `instrument_id` como se fosse "a empresa"
        (rejeitada -- é exatamente o problema que motivou esta investigação: PETR3 e
        PETR4 teriam `instrument_id` diferentes mas são a mesma empresa); renomear/
        migrar `instrument_id` para `company_id` nas tabelas existentes (rejeitada --
        destrutivo, quebraria toda a Fase 1/1.1 sem necessidade).
Impacto: desbloqueia PETR3/ITUB3 como instrumentos e market cap agregado por
        companhia (§4 da seção de decisões); nenhuma migration executada ainda.
Data: 2026-08-09

Nota (2026-08-09, mesmo dia): quality_nonfinancial_v1 acima ganhou bandas concretas
        (0/25/50/75/100 por métrica) em §17.1 -- não é uma decisão nova, é o fechamento
        do que esta entrada já tinha decidido em método, faltando só os números.
```

---

# AJUSTE FINAL — DCF nominal, risk-free V1, ERP V1 (2026-08-09, mesma data)

Revisão sobre §15/§16 depois de nova rodada de decisão do usuário. Estas seções
substituem as recomendações anteriores nos pontos indicados; **nenhum código ou
migration foi escrito**.

## 21.1 DCF V1 é nominal em BRL

Decisão: a primeira implementação do FCFF DCF (§10 da seção de decisões originais)
projeta fluxos **nominais em BRL** — `Revenue`, `EBITDA`, `FCFF` em reais correntes,
descontados por um **WACC nominal**, com `terminal_growth` também nominal. **Nenhuma
mistura de fluxo real com taxa nominal, ou vice-versa** — essa consistência é a
restrição mais importante desta seção inteira, porque é o erro clássico mais fácil de
cometer sem perceber (superestima ou subestima o valor presente dependendo de qual lado
"esqueceu" de descontar a inflação).

`dcf_real` (o desenho anterior, baseado em Tesouro IPCA+) **não foi descartado** — vira
metodologia suportada conceitualmente para o futuro (§21.3), só deixa de ser o padrão
da V1.

## 21.2 Risk-free V1 — Tesouro Prefixado, regra determinística de maturidade

**Fonte bruta**: mesmo arquivo já validado em §15.2
(`PrecoTaxaTesouroDireto.csv`, Tesouro Transparente) — mas filtrando por
`Tipo Titulo` em `{"Tesouro Prefixado", "Tesouro Prefixado com Juros Semestrais"}` em
vez de `"Tesouro IPCA+"`. Já confirmado no §15.2 que esses dois tipos existem no mesmo
arquivo, com histórico desde 2004.

**Regra determinística de seleção de maturidade** (nunca escolha manual):

```text
Para um as_of_date dado:
  1. Filtrar linhas do CSV com Data Base == as_of_date (ou o último Data Base
     disponível <= as_of_date, se o dia exato não for pregão) e Tipo Titulo em
     {"Tesouro Prefixado", "Tesouro Prefixado com Juros Semestrais"}.
  2. Para cada linha, calcular maturidade_anos = (Data Vencimento - Data Base) / 365.25.
  3. Escolher a linha com |maturidade_anos - 10| mínimo (mais próxima de 10 anos).
  4. Em empate exato, preferir "Tesouro Prefixado com Juros Semestrais" sobre
     "Tesouro Prefixado" simples (títulos longos de 10 anos tipicamente só existem
     na modalidade com juros semestrais no mercado brasileiro -- LTN pura raramente
     tem prazo tão longo -- então esse desempate deve ser raro na prática).
  5. government_bond_yield_brl = média entre Taxa Compra Manha e Taxa Venda Manha
     dessa linha (ponto médio bid/ask -- escolha documentada aqui, não implícita).
  6. Guardar junto: bond_maturity (a maturidade real da linha escolhida, que pode não
     ser exatamente 10.00 anos -- é a mais próxima disponível naquele dia), source,
     as_of_date.
```

**Ajuste de risco de crédito soberano**: título público brasileiro em BRL não é
"livre de risco" no sentido teórico estrito (governo pode, na prática, ter risco de
default mesmo em moeda local, ainda que menor que em moeda estrangeira) —

```text
risk_free_nominal_brl = government_bond_yield_brl - brazil_default_spread
```

`brazil_default_spread` vem da mesma fonte usada para o componente de país do ERP
(Damodaran, §21.4) — **é o mesmo número conceitual usado nos dois lugares por design,
não uma coincidência a evitar**; ver §21.6 para por que isso não é dupla contagem.

**Armazenamento** (linha de premissa, não número solto):

```text
tabela (nome provisório) risk_free_assumptions:
  as_of_date, government_yield, default_spread, risk_free_rate, bond_maturity,
  source, methodology, calculation_version
```

## 21.3 DCF real — suportado conceitualmente, não é V1

`dcf_real` continua como metodologia planejada para o futuro (não descartada, só não é
a primeira a ser implementada). Se/quando for implementada, a mesma disciplina de
consistência de §21.1 se aplica ao inverso: `fluxos reais + growth real + WACC real`
todos precisam vir do mesmo referencial — nesse caso, o risk-free volta a ser o Tesouro
IPCA+ já investigado em §15.2 (que continua válido, só não é o caminho V1).

## 21.4 ERP Brasil V1 — datasets de país da Damodaran, com snapshot point-in-time

**Fonte principal**: datasets de "Country Risk Premium" de Aswath Damodaran (NYU
Stern) — já trazem, na mesma linha/snapshot, `mature_market_erp` (ERP do mercado
maduro de referência, tipicamente EUA), `country_default_spread` (o mesmo tipo de
número usado em §21.2) e `total_equity_risk_premium` (soma dos dois, já calculada pelo
próprio Damodaran). Usar os três **do mesmo snapshot**, nunca misturar
`mature_market_erp` de uma atualização com `country_default_spread` de outra.

**Disciplina point-in-time obrigatória** (o desenho que faltava na versão anterior
deste documento):

```text
Para qualquer as_of_date histórico:
  erp_snapshot_date <= as_of_date

  Escolher o snapshot de Damodaran mais recente cuja data de publicação já era
  <= as_of_date -- nunca usar retrospectivamente um snapshot publicado DEPOIS do
  as_of_date do valuation (isso seria look-ahead, a mesma classe de erro que
  available_from já previne em fundamentos e agora precisa ser prevenida aqui
  também, com o mesmo rigor).
```

**Armazenamento**:

```text
tabela (nome provisório) equity_risk_premium_assumptions:
  snapshot_date, mature_market_erp, country_default_spread, country_risk_premium,
  total_erp, source, methodology, available_from, calculation_version
```

`available_from` aqui cumpre exatamente o mesmo papel que já cumpre em
`financial_statement_facts` -- é o campo que os testes de look-ahead de valuation
(§5 da seção de decisões originais) precisam checar.

**Limitação real, não escondida**: os snapshots de Damodaran são publicados
periodicamente (tipicamente anual, às vezes com atualizações no meio do ano), não
diariamente — a série resultante é uma função em degraus (mesmo ERP vale para todo um
período entre duas publicações), não uma curva contínua como Selic ou preço de ação. É
metodologicamente aceitável (é como o mercado profissional realmente usa esse dado),
mas precisa estar claro que não é uma série diária de verdade.

## 21.5 EMBI+/Ipeadata — confirmado descontinuado, rebaixado a validação histórica

Já registrado em §16.3 (revisado nesta mesma rodada): série `JPM366_EMBI366`
descontinuada em **2024-07-30**, confirmado ao vivo (10.126 pontos, último ponto
`{"VALDATA":"2024-07-30","VALVALOR":228.0}`), unidade confirmada como **ponto-base**
pelo próprio metadado da série (`"dez pontos-base equivalem a um décimo de 1%"` — logo
100 pb = 1,00%, 228 pb = 2,28%, batendo com o exemplo de conversão esperado). **Não é
fonte primária de ERP corrente** — uso correto é validação histórica/sensitivity
analysis/comparação de spread soberano só até jul/2024, nunca para `as_of_date`
posterior a essa data.

## 21.6 Como o risco Brasil evita dupla contagem — decomposição explícita do WACC

Exigência do usuário: mostrar matematicamente que risco soberano não entra duas vezes
na mesma conta. Decomposição completa:

```text
PASSO 1 -- limpar o risk-free do risco de crédito soberano embutido no título BRL:

  risk_free_nominal_brl = government_bond_yield_brl - brazil_default_spread
                                                        ^^^^^^^^^^^^^^^^^^^^
                                                        subtraído aqui, 1x

PASSO 2 -- custo de capital próprio, risco Brasil entra de novo, mas por um canal
diferente (prêmio de risco de INVESTIR EM AÇÃO no Brasil, não o mesmo risco de crédito
do título público):

  cost_of_equity = risk_free_nominal_brl
                    + beta × mature_market_erp
                    + country_risk_premium
                                ^^^^^^^^^^^^^^^^^^^
                                somado aqui, 1x -- É DERIVADO do mesmo
                                brazil_default_spread (Damodaran deriva
                                country_risk_premium a partir do default_spread,
                                tipicamente escalado pela volatilidade relativa
                                ações/títulos), mas entra como um termo aditivo
                                separado do risk-free, nunca re-subtraído nem
                                re-somado ao risk-free em si

PASSO 3 -- custo de dívida (nao usa country_risk_premium nenhuma vez -- usa o
spread de crédito da PRÓPRIA EMPRESA sobre o título público, um número diferente
e específico da companhia, não do país):

  cost_of_debt = (risk_free_nominal_brl + company_credit_spread) × (1 - tax_rate)

PASSO 4 -- WACC:

  WACC = equity_weight × cost_of_equity + debt_weight × cost_of_debt
```

**Por que isso está correto e não é dupla contagem**: risco soberano Brasil entra
exatamente **duas vezes no modelo inteiro**, mas por **dois canais diferentes e
teoricamente distintos** — uma vez removido do risk-free (Passo 1, porque um título
público brasileiro não é literalmente livre de risco), e uma vez adicionado ao custo de
capital próprio (Passo 2, porque investir em ação no Brasil carrega risco-país
adicional sobre o que uma ação de mercado maduro carregaria). Isso **não** é o mesmo
que subtrair e depois re-somar o mesmo número no mesmo lugar (isso sim seria dupla
contagem, ou pior, cancelamento acidental) — são dois pontos de entrada diferentes na
cadeia de cálculo, cada um com justificativa própria. O que **seria** dupla contagem, e
precisa ser evitado explicitamente na implementação: usar `country_risk_premium` mais
de uma vez dentro do Passo 2, ou aplicar `brazil_default_spread` tanto no Passo 1
quanto de novo dentro de `company_credit_spread` no Passo 3 (o spread de crédito da
empresa deve ser medido **sobre o título público**, que já embute o risco soberano —
não sobre um benchmark "livre de risco global", senão o risco Brasil apareceria
embutido ali de novo).

**Teste de regressão a criar quando a implementação começar**: dado um conjunto fixo de
premissas, verificar algebricamente que `WACC` calculado bate com o cálculo manual
passo a passo acima — e um teste específico que tentaria (e falharia) uma
implementação hipotética que usasse `government_bond_yield_brl` bruto (sem subtrair
`default_spread`) como risk-free E ainda somasse `country_risk_premium` inteiro no
cost_of_equity, para provar que o teste realmente pegaria a dupla contagem se alguém
reintroduzir esse bug depois.

## 22. Bloqueadores reclassificados (2026-08-09)

```text
EMBI unit:                          RESOLVIDO
ERP methodology:                    RESOLVIDO
Risk-free methodology:              RESOLVIDO
DCF nominal/real consistency:       RESOLVIDO
D&A:                                RESOLVIDO (2026-08-27, §28 -- DFC_MI 6.01.01 + fallback DVA)
FRE year-by-year:                   RESOLVIDO (2026-08-27, §3 -- 2010->2026 ingerido sem gap)
Company migration:                  APLICADA E VERIFICADA (2026-08-27, §19)
quality_bank_v1:                    INCOMPLETE POR DESIGN
Quality Score bands:                IMPLEMENTADO (2026-08-27, §8/§17) -- PETR4 63,8 / VALE3 51,0 / ITUB4 incomplete
Fase 1.1:                           BLOQUEADOR PARA INICIAR IMPLEMENTAÇÃO
```

## 23. Log de decisões — rodada de ajuste final

```text
Decisão: DCF V1 nominal em BRL (revogando WACC real/Tesouro IPCA+ como padrão).
Motivo: simplicidade e consistência de implementação -- projetar cada linha do DRE/DFC
        de forma real exigiria deflacionar consistentemente todo insumo (CVM reporta
        nominal), risco maior de inconsistência silenciosa entre fluxo e taxa.
Alternativas rejeitadas: manter real como V1 (mantida como dcf_real futuro, §21.3, não
        descartada -- só não é a primeira a implementar).
Impacto: risk-free V1 muda de Tesouro IPCA+ para Tesouro Prefixado (§21.2); toda a
        cadeia de premissas do DCF (terminal_growth, WACC) precisa ser nominal também.
Data: 2026-08-09

Decisão: risk-free V1 = government_bond_yield_brl (Tesouro Prefixado, maturidade mais
        próxima de 10 anos, regra determinística) - brazil_default_spread.
Motivo: título soberano em BRL carrega risco de crédito próprio, não é literalmente
        livre de risco -- ajuste padrão (Damodaran) para estimar risk-free em mercados
        emergentes a partir de título público local.
Alternativas rejeitadas: usar o yield bruto do título sem ajuste (mais simples, mas
        embutiria risco soberano dentro do "risk-free", inflando o risk-free e
        distorcendo tudo que é descontado por ele).
Impacto: define risk_free_assumptions (tabela nova); brazil_default_spread precisa vir
        da mesma fonte usada no ERP (Damodaran) para manter os dois consistentes.
Data: 2026-08-09

Decisão: ERP V1 = datasets de país da Damodaran diretamente (mature_market_erp +
        country_risk_premium + total_erp do mesmo snapshot), com regra explícita
        erp_snapshot_date <= as_of_date.
Motivo: EMBI+/Ipeadata (a peça que se pretendia automatizar) está descontinuada desde
        30/07/2024 -- confirmado nesta rodada, não dava mais pra ser a fonte corrente.
        Os datasets de Damodaran já entregam os componentes prontos e consistentes
        entre si, sem precisar compor fontes diferentes.
Alternativas rejeitadas: continuar tentando compor com EMBI+ (rejeitada -- sem dado
        pós-2024, inviável como fonte corrente); ERP realizado só com IBOV interno
        (mantida só como validação cruzada, não fonte primária, mesmo motivo de antes).
Impacto: define equity_risk_premium_assumptions (tabela nova); EMBI+/Ipeadata rebaixado
        a uso histórico (até jul/2024) dentro do mesmo desenho.
Data: 2026-08-09

Decisão: bandas concretas de quality_nonfinancial_v1 propostas e registradas em §17.1;
        ficam em config/quality_nonfinancial_v1.yaml, nunca hard-coded; todo resultado
        carrega calibration_status='provisional'.
Motivo: usuário pediu valores exatos antes de considerar o planejamento fechado;
        'provisional' registra honestamente que a calibração é por referência de
        mercado geral, não validação estatística (universo de 3 empresas é pequeno
        demais para isso).
Alternativas rejeitadas: deixar as bandas sem valores concretos até a implementação
        (rejeitada -- era exatamente o gap que esta rodada pediu para fechar); marcar
        como definitivo sem o status provisional (rejeitada -- seria overclaim dado o
        tamanho do universo atual).
Impacto: fecha o único ponto metodológico que ainda estava aberto no plano da Fase 2;
        nenhuma migration nova (as bandas vivem em YAML, não em schema).
Data: 2026-08-09
```

---

# ACHADOS DA IMPLEMENTAÇÃO — BLOCO 1 (`share_count_history` + FRE, 2026-08-27)

## 24. `Quantidade_Acoes_*_Circulacao` da FRE é FREE FLOAT, não "emitidas − tesouraria"

O §13.1 diz que `distribuicao_capital` traz "`shares_outstanding` de verdade (ações em
circulação, excluindo tesouraria)". **Impreciso.** Revalidado contra FRE 2024 e 2013
reais:

- PETR4 2024: ON em circulação = 3.483.155.534 de 7.442.231.382 emitidas (Capital
  Integralizado) = **46,8%**, batendo exatamente com `Percentual_Acoes_Ordinarias_Circulacao:
  46.803`. Isso exclui o **bloco de controle** (a União), não só tesouraria. É **free float**.
- Consequência 1: o denominador de market cap (§4) é **`shares_issued`** (Capital
  Integralizado ON/PN), nunca o campo `_Circulacao`.
- Consequência 2: `treasury_shares = issued − circulação` daria um número absurdo (subtrairia
  o controlador). A FRE **não traz tesouraria de forma consistente**: 2013 tem arquivo
  dedicado (`valor_mobiliario_tesouraria_*`), 2024 **não tem** (o formato FRE mudou muito —
  2024 troca esses arquivos por declaração de gênero/raça de administradores). `treasury_shares`
  e `shares_outstanding` ficam `NULL` com `quality_flag='missing_input'` na V1.

Schema real da tabela V1 (difere do rascunho do §3): `shares_issued` (sólido),
`free_float_shares` (nomeado pelo que é), `treasury_shares` NULL, `shares_outstanding` NULL,
`version` na chave natural (reapresentações preservadas), `source_document_id` → `cvm_documents`.

**Fallback de `shares_issued`** (achado do backfill 2010-2026): a FRE recente da Vale
(2023-2024) **não traz linha `Capital Integralizado`** no `capital_social` — só `Capital
Emitido`/`Subscrito`. Numa empresa com capital totalmente integralizado os três tipos são
iguais (confirmado em PETR4 2024). Regra V1: `Integralizado → Subscrito → Emitido`, com
`quality_flag='estimated'` quando cai para o fallback. Recupera Vale 2024 = ON 4.539.007.568
(bate com o §13.1), marcado honestamente como estimado.

## 25. `capital_social` / `distribuicao_capital` carregam só a versão final do ano

O arquivo índice (`fre_cia_aberta_AAAA.csv`) tem uma linha por reapresentação (PETR4 2024:
28 versões). Mas `capital_social` e `distribuicao_capital` trazem **apenas a última versão**
consolidada (PETR4 2024: só `Versao 28`). Então a FRE dá **uma contagem de ações por
(companhia, ano)**, com o `available_from` = `DT_RECEB` da versão final. Não são 28 snapshots
point-in-time. O join entre os 3 arquivos é por `(cnpj, Data_Referencia, Versao)`.

## 26. `Capital Integralizado` pode ter múltiplas linhas na mesma versão

Visto em 2013 (PETR4): 2 linhas `Capital Integralizado` na `Versao 9`, `ID_Capital_Social`
85010 e 85013, `Data_Autorizacao_Aprovacao` diferentes (2014-04-02 vs 2013-04-29), mesmas
quantidades. Regra: escolhe a de aprovação mais recente; `quality_flag='inconsistent'` **só
se** as quantidades divergirem entre as linhas.

## 27. Nomes de coluna e encoding por arquivo FRE

- `fre_cia_aberta_AAAA.csv` (índice): enc **utf-8**, cabeçalho padrão DFP
  (`CNPJ_CIA;DT_REFER;VERSAO;...;DT_RECEB;LINK_DOC`).
- `fre_cia_aberta_capital_social_AAAA.csv`: enc **cp1252**, cabeçalho próprio
  (`CNPJ_Companhia;Data_Referencia;Versao;...;Quantidade_Acoes_Ordinarias;
  Quantidade_Acoes_Preferenciais;Quantidade_Total_Acoes`).
- `fre_cia_aberta_distribuicao_capital_AAAA.csv`: enc **cp1252**
  (`...;Quantidade_Acoes_Ordinarias_Circulacao;Quantidade_Acoes_Preferenciais_Circulacao;
  Quantidade_Total_Acoes_Circulacao;...`).
- Headers **byte-idênticos** entre 2024 e 2013 nos 3 arquivos. `reference_date` (DT_REFER)
  mudou de convenção: FRE antiga usa `AAAA-01-01`, recente usa `AAAA-12-31` — armazenado como
  a CVM reporta.
- Pendência §14.1 **fechada**: o backfill `sync-fre --from-year 2010` baixou e ingeriu
  2010→2026 sem gap; os 3 arquivos usados têm schema estável em toda a janela.

---

# ACHADOS DA IMPLEMENTAÇÃO — BLOCO 2 (validação §22, D&A e tax, 2026-08-27)

## 28. D&A para EBITDA — contas reais confirmadas (16 anos × 3 empresas)

Query direta sobre `financial_statement_facts` (248k fatos já ingeridos). Nada assumido.

**Fonte primária: `DFC_MI` (fluxo indireto), ramo `6.01.01`** — o add-back de D&A ao lucro
líquido nas atividades operacionais. **Sinal positivo.** Descrições reais (variam por empresa
e por ano, colapsam após normalização `_norm`):

| empresa | descrição na CVM | `_norm` |
|---|---|---|
| PETR4 | "Depreciação, Depleção e Amortização" (e `deplecão`/caixa alta variam) | `DEPRECIACAO, DEPLECAO E AMORTIZACAO` |
| VALE3 | "Depreciação, amortização e exaustão" | `DEPRECIACAO, AMORTIZACAO E EXAUSTAO` |
| ITUB4 | "Depreciações e Amortizações" / "Depreciação e Amortizações" | `DEPRECIACOES E AMORTIZACOES`, `DEPRECIACAO E AMORTIZACOES` |

Código do subitem migra ao longo dos anos (`6.01.01.04` → `.05` → `.06` → `.07` → `.08`) —
casar por **descrição normalizada dentro do ramo `6.01.01`**, nunca por código fixo (mesma
disciplina de `CAPEX_DESC`). Fallback para `DFC_MD` quando `DFC_MI` ausente.

**Fonte de fallback: `DVA` (demonstração de valor adicionado), `7.04.01` (PETR4/VALE3) ou
`7.05.01` (ITUB4)** — "Depreciação, Amortização e Exaustão". Presente em **todas as 3
empresas, DFP e ITR, 2010→2026**, rótulo uniforme. **Sinal negativo** (é dedução do valor
adicionado) → usar `abs()`. É o D&A **bruto** do período (inclui parcela capitalizada em
estoque/ativo), então diverge do add-back do DFC a partir de ~2019 para PETR4 e sempre para
ITUB4 (DVA > DFC). Usar só como fallback, com `quality_flag='estimated'`.

**EBITDA V1**: `EBITDA = EBIT + D&A` (D&A do DFC, positivo). Bancos → `sector_inadequate`
(EBIT já é). D&A ausente → `missing_input`, nunca inventar (§6). Sanity: PETR4 2024
EBIT 137,2 bi + D&A ~67 bi = ~204 bi.

## 29. Alíquota efetiva de imposto — contas reais confirmadas

`DRE`, todas as 3 empresas, 16 anos, rótulo estável:

- **"Resultado Antes dos Tributos sobre o Lucro"** — código `3.05` (ITUB4) / `3.07`
  (PETR4/VALE3). Lucro antes de impostos.
- **"Imposto de Renda e Contribuição Social sobre o Lucro"** — `3.06` / `3.08`. Reportado
  **negativo** (despesa).

`effective_tax_rate = abs(income_tax) / pretax_income` quando `pretax_income > 0`; senão
`quality_flag='missing_input'` (§7: nunca alíquota inventada). Aplicável inclusive a bancos
(pagam imposto); o que não se aplica a banco é o ROIC, não a alíquota.

---

# ACHADOS DA IMPLEMENTAÇÃO — BLOCO 3 (métricas de valuation + múltiplos, 2026-08-27)

## 30. Bloco 3 implementado — o que mudou vs. o planejado

- **`analytics/valuation_metrics.py`** (§6-7): grava em `fundamental_metrics` com
  `calculation_version='valuation_metrics_v1'` (as linhas da Fase 1 ficam intactas).
  metric_names: `da`, `ebitda`, `pretax_income`, `income_tax`, `effective_tax_rate`,
  `nopat`, `invested_capital`, `roic`. period_type `annual` (DFP) + `ytd` (ITR). CLI
  `compute-metrics` roda base (Fase 1) + valuation. Verificado: PETR4 2024 EBITDA 204,2 bi,
  ROIC 12,9%; 2022 EBITDA 362,5 bi / ROIC 33,5%. ITUB4 (banco): ebitda/nopat/invested_capital/
  roic = `sector_inadequate`.
- **`analytics/valuation_multiples.py`** (§4-5): tabela `valuation_multiples`, base FY.
- **Decisão V1: base FY, não TTM.** §5 pede TTM como padrão, mas TTM exige EBITDA/FCF
  **trimestral isolado** (o DFC no ITR só vem acumulado — precisa da subtração YTD(Qn) −
  YTD(Qn−1), como o `capex` da Fase 1). É subprojeto próprio, com testes de look-ahead
  dedicados. FY point-in-time é correto e suficiente para a primeira versão. TTM = próximo
  incremento.
- **`compute_and_store_metrics` da Fase 1 estava órfão** (sem CLI, sem teste) — só tinha sido
  rodado à mão. Agora é chamado pelo `compute-metrics`. Idempotente.

## 31. Bug pré-existente: `share_class` da VALE3 corrompido por YAML 1.1

`config/companies.yaml` tinha `share_class: ON`. YAML 1.1 lê `ON`/`OFF`/`YES`/`NO`/`TRUE`/
`FALSE` como **booleano** → `ON` virou `True` → gravado como a string `"true"` em
`instruments.share_class` desde a Fase 1. PETR4/ITUB4 (`PN`) escaparam porque `PN` não é
palavra-chave. Corrigido: aspas no YAML (`"ON"`/`"PN"`) + comentário + `init` re-rodado
(VALE3 agora `"ON"`). Afetaria diretamente o §4 (o join ticker→classe do market cap).

## 32. `= any(%s)` não funciona no backend REST

`_dividends_ttm_per_share` usava `action_type = any(%s)` com lista Python — o backend REST
(`exec_sql` RPC) não materializa lista como array Postgres (`42809: op ANY/ALL (array)
requires array on right side`). Mesma ressalva já documentada em
`pipelines/fundamentals_ingest.py`. Trocado por `in (%s, %s)` com placeholders explícitos.

## 33. Preços defasados entre classes

`update-prices` (incremental) não roda para instrumento fora do universo ativo; PETR3/ITUB3
ficaram mais recentes que PETR4/VALE3/ITUB4 (spread de ~19 pregões). `compute_multiples`
agora emite aviso e degrada `quality_flag` para `estimated` quando o spread de datas entre
as classes passa de `MAX_PRICE_DATE_SPREAD_DAYS` (3). Resolvido no run com `update-prices`
antes do `compute-multiples`.

---

# ACHADOS DA IMPLEMENTAÇÃO — DCF (§10, 2026-08-27)

## 34. DCF FCFF -- decisões e achados da V1

### 34.1 Fontes externas validadas contra o real

- **Tesouro Prefixado** (`PrecoTaxaTesouroDireto.csv`, Tesouro Transparente): baixado e
  parseado. 14,4 MB, ~175k linhas, encoding latin-1, separador `;`, decimal com vírgula,
  datas `dd/mm/aaaa`. Tipos: `Tesouro Prefixado` e `Tesouro Prefixado com Juros Semestrais`
  (usados) + IPCA+/Selic/Educa+/Renda+ (ignorados). Regra §21.2 (maturidade mais próxima de
  10 anos) escolhe, para `as_of` 2026-08-27, o Prefixado c/ Juros Semestrais venc. 2037-01-01,
  base 2026-08-26, yield médio 14,5% → risk-free 12,37% (após subtrair o default spread).
- **Damodaran ERP** (`ctryprem.xlsx`): baixado e lido (via `zipfile`+`xml.etree`, sem
  dependência nova). Aba "Regional breakdown", Brazil: Adj. Default Spread **2,1275%**,
  Equity Risk Premium **7,4710%**, Country Risk Premium **3,2410%**, mature_market_erp
  derivado (= ERP total − CRP) **4,23%** (bate com o "US equity risk premium" da aba "ERPs
  by country"). **Não há download automatizado confiável** (sem `openpyxl`/`lxml` no projeto;
  planilha manual anual do autor) → virou `config/equity_risk_premium_snapshots.yaml`
  transcrito à mão, com `available_from` conservador (2026-02-16). Backfill de snapshots
  históricos exige transcrever os arquivos arquivados da Damodaran.

### 34.2 FCFF = NOPAT + D&A + capex (média de 3 anos)

O primeiro teste com `free_cash_flow` da Fase 1 (`OCF + capex`) deu fair value **absurdo**
para PETR4 (R$92-127) — `OCF` inclui variação de capital de giro favorável e não é o fluxo
pré-financiamento que um DCF descontado a WACC pede. Trocado por **FCFF ≈ NOPAT + D&A +
capex** (todos anuais, `valuation_metrics_v1`/`fundamental_metrics_v1`, `ok` nas 3 empresas,
16 anos). Média de 3 anos porque um único ano de capex pesado distorce o ponto de partida.
Resultado coerente: PETR4 FCFF 2023-25 ≈ R$83 bi (NOPAT ~107 + D&A ~84 + capex ~−109), DCF
fair R$19-35 vs preço R$41.

### 34.3 `free_cash_flow` TTM da VALE3 para em 2018 (gap pré-existente da Fase 1)

`fundamental_metrics` da VALE3: `free_cash_flow` **anual** = `ok` em todos os 16 anos, mas
`capex`/`free_cash_flow` **ytd** têm 11 buracos, e o TTM exige 4 trimestres consecutivos →
`free_cash_flow` TTM da VALE3 só vai até 2018-06-30. **Não bloqueia o DCF** (FCFF anual),
mas o `valuation_multiples --basis ttm` da VALE3 tem FCF yield stale/nulo.

**Causa exata identificada** (investigado 2026-08-27), duas coisas:
1. **2012 Q1-Q3**: a linha de capex da VALE3 é `6.02.05 "Adilções ao imobilizado"` — **typo
   da própria CVM** ("Adilções", não "Adições"). `_norm` não bate com `CAPEX_DESC`.
2. **Q3 de 2018 a 2025** (só Q3): a VALE3 reporta capex como
   `6.02.04 "Adições ao Imobilizado e investimentos"` (linha **combinada** PP&E +
   participações), diferente do `"Adições ao Imobilizado"` que usa nos outros trimestres e
   no anual.

**Fix recomendado (Fase 1, NÃO aplicado nesta rodada)**: adicionar a `CAPEX_DESC` em
`analytics/fundamentals_metrics.py`:
```python
"Adilções ao imobilizado",                  # typo da CVM, VALE3 2012
"Adições ao Imobilizado e investimentos",    # VALE3 Q3 -- linha combinada (aceita a imprecisão)
```
Depois `stock-research compute-metrics VALE3` + `compute-multiples --basis ttm VALE3`. Não
feito aqui porque muda `capex`/`free_cash_flow` já verificados e é decisão de metodologia
da Fase 1 (a segunda entrada bundla participações no capex).

### 34.4 Cost of debt = despesa financeira / dívida bruta (com piso/teto)

`DRE` conta `3.06.02` "Despesas Financeiras" (PETR4 e VALE3, 16 anos). `pretax_cost_of_debt
= abs(despesa financeira) / dívida bruta`, limitado a [4%, 30%] (`config/wacc_v1.yaml`),
porque "Despesas Financeiras" inclui variação cambial e correção monetária — pode inflar
muito num ano de real fraco. Refinamento: isolar só juros (`3.06.02.01`) — não feito na V1.

### 34.5 Anti-dupla-contagem de risco Brasil — testado

`tests/unit/test_wacc.py::test_risco_brasil_nao_conta_em_dobro_no_mesmo_lugar`: uma
implementação que usasse o yield BRUTO como risk-free **e** somasse o `country_risk_premium`
inteiro no cost of equity infla o WACC em > 2 p.p. — o teste pega. A implementação correta
subtrai o `brazil_default_spread` do risk-free (uma vez) e soma o `country_risk_premium`
(uma vez, aditivo), canais distintos (§21.6).

### 34.6 Dois bugs pegos ao aplicar (2026-08-27)

1. **JSON**: o snapshot de ERP (com campos `date`/`datetime` do YAML) não serializava
   dentro de `valuation_snapshots.assumptions`. `_j()` virou recursivo (dict/list).
2. **Cost of debt abaixo do soberano**: `pretax_cost_of_debt` (despesa financeira /
   dívida bruta) da PETR4 e VALE3 deu abaixo de 4% → limitado ao piso ABSOLUTO do config
   (4%), que fica **abaixo do risk-free** (12,4%) → `company_credit_spread` NEGATIVO e
   WACC subestimado (PETR4 saiu 13,2%). Corrigido: o piso do `pretax_cost_of_debt` passa a
   ser o **risk-free** (empresa não capta abaixo do governo); o piso do config só vale
   quando não há risk-free. Depois: PETR4 WACC 15,7%, VALE3 16,3%, `company_credit_spread`
   = +0,0% (a proxy de despesa financeira é fraca — limitação já registrada em §34.4).

---

# REFINOS DO DCF (§35, 2026-08-27) -- decisões do usuário aplicadas

## 35. CAPEX_DESC, ΔWC no FCFF e cost of debt de juros puros

### 35.1 CAPEX_DESC -- só o typo entra como capex puro

Adicionado `"Adilções ao imobilizado"` a `CAPEX_DESC` (typo da própria CVM, VALE3
2012) -- é inequivocamente aquisição de imobilizado.

A linha **combinada** `"Adições ao Imobilizado e investimentos"` (VALE3 Q3 de
2018-2025) **NÃO entra em `CAPEX_DESC`** como se fosse capex puro. Ela é
`CAPEX_COMBINED_DESC`, usada só como **fallback** quando nenhuma linha de
imobilizado separada existe no pacote, e **sempre** com:
- `capex.quality_flag = 'estimated'`, motivo "linha combinada ... inclui
  participações societárias, capex levemente superestimado";
- `free_cash_flow.quality_flag = 'estimated'` propagado (FCF/DCF não ganham
  falsa precisão).

Efeito: `free_cash_flow` TTM da VALE3, que parava em 2018-06-30, agora vai até
2026-06-30 (as linhas de 2018+ ficam `estimated`, nunca `ok`).

### 35.2 FCFF passa a incluir ΔWC operacional

`FCFF ≈ média de 3 anos de (NOPAT + D&A + capex − ΔWC)`, com:
- **capex negativo** (convenção da CVM, confirmado: `capex` 2025 = −108,7 bi
  PETR4 / −33,4 bi VALE3) -- é **somado**;
- **ΔWC = WC_y − WC_{y-1}**; aumento de capital de giro = uso de caixa → **subtrai**;
- **WC operacional** (nova métrica `working_capital` em `valuation_metrics_v1`,
  `point_in_time`): `(Ativo Circulante − Caixa − Aplicações Financeiras) −
  (Passivo Circulante − Empréstimos e Financiamentos de CP)`. Exclui os itens
  claramente financeiros, como o usuário pediu. O plano de contas padronizado da
  CVM tem esses níveis-2 idênticos entre PETR4/VALE3 e todos os 16 anos.
- Se `WC_y` ou `WC_{y-1}` faltar num ano, o ΔWC daquele ano fica 0 e o
  `quality_reason` do snapshot registra "sem ajuste de deltaWC em [anos]" --
  nunca inventa.

Efeito: PETR4 FCFF 100,7 bi → 111,5 bi (giro encolheu 2023-25), fair base
R$45,44 → R$53,14 (MoS +9% → +22%). VALE3 FCFF 20,8 bi → 24,3 bi, fair R$24,92 →
R$31,83 (MoS −215% → −146%).

### 35.3 Cost of debt -- juros puros (DRE 3.06.02.01) com fallback

`_financial_expense_over_debt` agora tenta primeiro `3.06.02.01` ("Despesas
financeiras" -- juros puros; presente em PETR4 2010-2025 e VALE3 2016-2025).
Fallback documentado para `3.06.02` (nível 2, **inclui variação cambial e
monetária**) com a fonte registrada no motivo. Se nenhum dos dois existir →
`missing_input` (nunca assume zero).

O piso econômico continua sendo o **risk-free** (empresa não capta abaixo do
soberano). Achado: para PETR4 e VALE3, `juros puros / dívida bruta` dá ~6,5%
(dívida legada barata) -- **abaixo** do risk-free de 12,37% -- então o resultado
é o mesmo do fallback anterior: `pretax_cost_of_debt` = risk-free,
`company_credit_spread` = 0. O motivo do WACC agora explicita isso:
"juros puros (DRE 3.06.02.01) / dívida bruta = 0.0647, ajustado para 0.1237
(piso = risk-free)". Refino futuro: usar spread de crédito observado (CDS/emissões)
em vez da proxy contábil.

---

# AUDITORIA DE DECOMPOSIÇÃO + PROPAGAÇÃO DE QUALIDADE (§36, 2026-08-27)

## 36. Auditoria do DCF e a regra "premissa nunca sai `ok`"

Auditoria pedida antes do merge: decompor o FCFF de PETR4/VALE3 por ano,
atribuir a mudança de fair value às suas causas e garantir que suposição não
seja gravada como observação. **Nenhuma metodologia mudou** -- os números são
idênticos antes e depois; o que mudou foi o `quality_flag`.

### 36.1 Decomposição do FCFF (média de 3 anos, valores em bi de BRL)

**PETR4** (todos os insumos `quality_flag='ok'`):

| ano | EBIT | tax_rate | NOPAT | D&A | CAPEX | WC | ΔWC | FCFF |
|---|---|---|---|---|---|---|---|---|
| 2025 | 145,63 | 0,2656 | 106,95 | 84,39 | −108,71 | −41,70 | −4,23 | 86,86 |
| 2024 | 137,20 | 0,3238 | 92,78 | 67,03 | −79,86 | −37,46 | −11,13 | 91,09 |
| 2023 | 189,34 | 0,2948 | 133,53 | 66,20 | −60,31 | −26,33 | −17,11 | 156,53 |

média = **111,49 bi**

**VALE3** (todos os insumos `quality_flag='ok'`):

| ano | EBIT | tax_rate | NOPAT | D&A | CAPEX | WC | ΔWC | FCFF |
|---|---|---|---|---|---|---|---|---|
| 2025 | 31,97 | 0,5575 | 14,15 | 17,31 | −33,39 | −24,57 | −3,21 | 1,28 |
| 2024 | 55,46 | 0,1108 | 49,31 | 16,52 | −35,10 | −21,36 | −28,16 | 58,89 |
| 2023 | 65,27 | 0,2700 | 47,65 | 15,30 | −29,45 | 6,80 | 20,75 | 12,76 |

média = **24,31 bi**

`working_capital` existe em **todos** os 16 anos (2010-2025) para as duas
empresas -- nenhum ΔWC foi assumido 0 na janela usada.

### 36.2 Atribuição da mudança de fair value (§35)

| causa | PETR4 | VALE3 |
|---|---|---|
| correção de CAPEX (typo + linha combinada) | **R$ 0,00** | **R$ 0,00** |
| inclusão de ΔWC | **+R$ 7,70** | **+R$ 6,91** |
| alteração do cost of debt | **R$ 0,00** | **R$ 0,00** |
| outros efeitos | R$ 0,00 | R$ 0,00 |
| **total** (fair base) | R$ 45,44 → **R$ 53,14** | R$ 24,92 → **R$ 31,83** |

- **CAPEX = 0** porque o `capex` anual de 2023-2025 (a janela do DCF) já vinha
  da linha pura em ambas as empresas. O refino do §35.1 alterou apenas a série
  histórica/TTM de `free_cash_flow` da VALE3 (2012 pelo typo; 2018+ pela linha
  combinada) -- fora da média de 3 anos.
- **Cost of debt = 0** porque a taxa medida fica **abaixo do risk-free** nos dois
  casos (PETR4 6,47%; VALE3 8,24%) e o piso do risk-free (12,3725%) prevalece
  igualmente antes e depois. WACC inalterado: 15,67% / 16,34%.
- **ΔWC responde por 100% da mudança.** O efeito é linear no EV: PETR4 EV
  923,08 → 1.022,35 bi; VALE3 EV 179,79 → 210,45 bi.

### 36.3 `3.06.02.01` zerada não é observação (achado da auditoria)

A VALE3 **declara** `3.06.02.01 "Despesas financeiras" com valor 0,000** e
preenche apenas o nível 2 (`3.06.02` = −8,079 bi). O código do §35 aceitava esse
zero como juros puros observados e gravava o motivo "juros puros / dívida bruta
= 0.0000" -- enganoso. Corrigido: **zero em `3.06.02.01` é tratado como ausente**
(empresa com dívida bruta positiva não paga juro zero) e cai no fallback do
nível 2, com o motivo explicitando "(3.06.02.01 declarada com valor zero)". Se
os dois níveis estiverem ausentes ou zerados → `missing_input`, nunca zero.

Nota: o desdobramento do nível 3 não é padronizado entre empresas. Na PETR4
`3.06.02.02` = "Variações monetárias e cambiais"; na VALE3 = "Resultado de
alienação/baixa de participação". Por isso o fallback do nível 2 é rotulado
"inclui não-juros", não "inclui câmbio".

### 36.4 Regra de propagação de qualidade

**Número que veio de premissa nunca sai `quality_flag='ok'`.** Implementado como
cadeia explícita, com o pior flag vencendo (`_merge_quality`,
`ok < estimated < incomplete < missing_input`):

| origem da premissa | onde degrada | flag |
|---|---|---|
| ΔWC assumido 0 porque `working_capital` falta no ano | `_fcff_avg` → DCF → snapshot | `estimated` |
| insumo anual (`nopat`/`da`/`capex`/`working_capital`) já `estimated` | `_fcff_avg` → DCF → snapshot | `estimated` |
| cost of debt do fallback nível 2 | `_financial_expense_over_debt` → WACC → snapshot | `estimated` |
| cost of debt substituído pelo piso (risk-free/config) | `_financial_expense_over_debt` → WACC → snapshot | `estimated` |
| `payout_ratio` default 0,5 no Residual Income | `_run_bank` → RIM → snapshot | `estimated` |

O `quality_reason` do snapshot passa a nomear a premissa e a origem
("deltaWC ASSUMIDO 0 em [anos] -- suposição, não dado observado";
"SUBSTITUÍDO por 0.1237 (piso = risk-free) -- premissa, não observação").
`compute_dcf`, `compute_wacc` e `compute_residual_income` ganharam
`input_quality_flag`/`input_quality_reason` -- eles nunca **melhoram** a
qualidade do que receberam.

### 36.5 Resultado após a propagação (números inalterados)

| | fair (base) | WACC/coe | flag | por quê |
|---|---|---|---|---|
| PETR4 `fcff` | R$ 53,14 | 15,67% | **`estimated`** | cost of debt no piso do risk-free |
| VALE3 `fcff` | R$ 31,83 | 16,34% | **`estimated`** | `3.06.02.01` zerada → nível 2, depois piso |
| ITUB4 `residual_income` | R$ 21,06 | 20,00% | **`ok`** | payout observado (`dividends_ttm` / lucro) |
| ITUB4 `ddm` | R$ 20,50 | 20,00% | **`ok`** | dividendo TTM observado |

O FCFF das duas empresas está `ok` na sua própria perna (WC completo, insumos
`ok`); o que degrada é o WACC. Consequência prática: **enquanto o custo de
dívida for a proxy contábil pisada no soberano, nenhum DCF de não-financeira
sai `ok`** -- o que é honesto. O caminho para `ok` é o refino já anotado no
§35.3: spread de crédito observado (CDS/emissões).

### 36.6 `cost_of_debt` V1 -- o que a proxy contábil realmente mede

Registro explícito para o merge: o `cost_of_debt` atual é uma **V1 estimada de
custo marginal**. A despesa financeira contábil da DRE (`3.06.02.01`, ou o nível
2 no fallback) dividida pela dívida bruta representa **custo histórico /
embedded** -- o juro médio dos contratos que já estão no balanço -- e **não
necessariamente o custo corrente de captar dívida nova**. Numa empresa com
dívida legada barata (PETR4, VALE3), essa proxy subestima o custo marginal; o
piso do risk-free corrige parcialmente, mas por baixo.

**Evolução futura (não implementar agora, não altera os fair values atuais)** --
hierarquia de fontes para o spread de crédito, da melhor para a pior:

1. **bond / emission spread** -- spread observado das emissões da própria
   empresa sobre o título público de prazo equivalente. É a medida direta do
   custo marginal.
2. **CDS** -- credit default swap do emissor, quando houver liquidez. Proxy de
   mercado do risco de default.
3. **synthetic credit spread** -- rating sintético derivado de cobertura de
   juros (EBIT / despesa de juros) mapeado para spread típico (tabela à la
   Damodaran). Não depende de a empresa ter dívida negociada.
4. **accounting proxy** -- o atual. Custo embedded. Último recurso.

Cada nível grava a fonte usada e degrada `quality_flag` conforme desce a
hierarquia. Enquanto o nível efetivo for o 4, todo DCF de não-financeira
permanece `estimated` (§36.5) -- comportamento correto.
