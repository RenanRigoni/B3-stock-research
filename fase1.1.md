# FASE 1.1 — EXPANSÃO HISTÓRICA E FECHAMENTO DEFINITIVO DA FASE 1

A Fase 1 principal já foi implementada e validada. NÃO quero refazer sua arquitetura, NÃO quero reescrever módulos que já funcionam e NÃO quero iniciar a Fase 2 ainda.

Sua missão agora é executar uma etapa complementar chamada:

**FASE 1.1 — Profundidade Histórica, Cobertura de Notícias e Fechamento da Base**

O objetivo é corrigir a principal limitação restante da Fase 1: a arquitetura está funcionando, mas a profundidade histórica de alguns datasets ainda é insuficiente para as análises que serão realizadas nas próximas fases.

Antes de alterar qualquer código:

1. leia `fase1.md`;
2. leia `roadmap.md`;
3. leia `docs/limitations.md`;
4. examine o estado atual do banco;
5. examine os pipelines existentes;
6. NÃO presuma que algo precisa ser refeito apenas porque pode ser melhor implementado;
7. preserve compatibilidade com tudo que já passou nos 321 testes;
8. crie testes de regressão para qualquer bug novo encontrado.

A prioridade desta etapa é:

```text
DADOS
>
COBERTURA
>
QUALIDADE
>
RASTREABILIDADE
>
PERFORMANCE
>
REFATORAÇÃO
```

---

# 1. SITUAÇÃO ATUAL

Atualmente existem aproximadamente:

```text
PETR4: 651 pregões
VALE3: 651 pregões
ITUB4: 651 pregões
IBOV:  651 pregões
```

Fundamentos possuem grande profundidade histórica através da CVM, com DFP/ITR abrangendo aproximadamente 2010–2026, respeitando `available_from` e point-in-time.

Porém, preços ainda estão limitados a aproximadamente 651 pregões.

Notícias:

```text
PETR4: 129
VALE3: 0
ITUB4: 0
```

Eventos/Event Studies possuem profundidade real principalmente em PETR4.

O pipeline de notícias sofreu com rate limit do GDELT.

Portanto, a arquitetura da Fase 1 está aprovada.

O problema atual é **profundidade e cobertura dos dados**.

---

# 2. OBJETIVO FINAL DESTA ETAPA

Ao terminar a Fase 1.1, quero possuir, sempre que a fonte permitir:

## Preços

```text
PETR4
VALE3
ITUB4
IBOV
```

Com histórico diário de aproximadamente:

```text
2010-01-01 → data atual
```

ou desde a primeira data confiável disponível de cada ativo.

Idealmente mais antigo se não houver custo adicional e os dados forem confiáveis.

---

## Fundamentos

Manter o que já está funcionando.

Confirmar:

```text
DFP
ITR
available_from
reapresentações
point-in-time
```

Não alterar metodologia sem necessidade.

---

## Notícias

Construir uma base histórica significativamente maior para:

```text
PETR4
VALE3
ITUB4
```

Idealmente:

```text
2015 → atual
```

quando a cobertura da fonte permitir.

Se determinada fonte não conseguir atingir esse período:

* documentar exatamente a limitação;
* buscar estratégia complementar gratuita;
* preservar identificação da fonte;
* nunca inventar cobertura inexistente.

---

## Eventos

Após expandir notícias:

reprocessar:

```text
deduplicação
linking
relevância
classificação
event clustering
effective_trade_date
confounding
event studies
```

---

# 3. PRIMEIRA TAREFA — INVESTIGAR OS 651 PREGÕES

Antes de simplesmente executar outro download, descubra por que existem somente 651 pregões.

Investigue:

```text
configuração do pipeline
start default
end default
yfinance period
yfinance start/end
limitação acidental
incremental sync
upsert
staging
consulta SQL
filtro
Supabase
PostgREST
schema
tipo de data
paginação
```

Não assuma que o Yahoo possui apenas esses dados.

Faça primeiro uma chamada isolada diretamente ao yfinance:

```python
import yfinance as yf

df = yf.download(
    "PETR4.SA",
    start="2010-01-01",
    end=<hoje>,
    interval="1d",
    auto_adjust=False,
    actions=True,
    repair=True,
    keepna=True,
    progress=False
)

print(df.index.min())
print(df.index.max())
print(len(df))
```

Repita para:

```text
VALE3.SA
ITUB4.SA
^BVSP
```

Compare:

```text
linhas retornadas diretamente
vs
linhas inseridas no banco
```

A partir daí determine se o problema está:

```text
fonte
pipeline
config
persistência
consulta
paginação
```

---

# 4. NÃO CORRIJA ANTES DE IDENTIFICAR A CAUSA

Crie diagnóstico objetivo:

```text
CAUSA DOS 651 PREGÕES:

Fonte:
Configuração:
Pipeline:
Banco:
Consulta:

Conclusão:
```

Somente depois corrija.

Se for bug:

* criar teste reproduzindo;
* corrigir;
* provar que o teste falha antes e passa depois.

---

# 5. BACKFILL HISTÓRICO DE PREÇOS

Após corrigir a causa:

executar backfill desde:

```text
2010-01-01
```

para:

```text
PETR4
VALE3
ITUB4
^BVSP
```

Se yfinance possuir dados anteriores confiáveis, pode armazená-los também.

Mas o objetivo mínimo é 2010 → atual.

---

# 6. PRESERVAR PREÇO BRUTO E AJUSTADO

Não alterar a regra existente.

Precisamos continuar possuindo:

```text
Open
High
Low
Close
Adj Close
Volume
```

Além das ações corporativas.

Não utilizar somente preços ajustados.

---

# 7. VALIDAR O BACKFILL

Depois da ingestão:

gerar para cada ativo:

```text
primeira data
última data
número de pregões
missing
duplicatas
anomalias OHLC
gaps
ações corporativas
```

Comparar PETR4, VALE3 e ITUB4 com o calendário do IBOV.

Não exigir que todos tenham exatamente o mesmo número de registros se houver razão legítima.

---

# 8. TESTAR RETORNOS NOVAMENTE

Recalcular:

```text
daily_returns
return_1d_price
return_1d_adjusted
log_return
volume_avg_20
volume_ratio_20
benchmark_return
excess_return
```

Verificar especialmente as fronteiras entre os dados antigos e os novos.

Não pode ocorrer:

```text
duplicação
retorno artificial
gap interpretado como pregão consecutivo
```

---

# 9. AÇÕES CORPORATIVAS

Com histórico maior, validar novamente:

```text
dividend
JCP quando identificável
split
reverse split
```

Verificar se existem mudanças bruscas de preço associadas a split/grupamento.

Evitar que data-quality reporte falsos positivos quando a ação corporativa explica o movimento.

---

# 10. VALIDAÇÃO SECUNDÁRIA DE PREÇOS

Brapi é opcional.

Se `BRAPI_TOKEN` estiver disponível:

comparar janela recente.

Se não estiver:

não bloquear a tarefa.

Também pode utilizar páginas/fontes públicas confiáveis pontualmente para validar algumas datas conhecidas, mas não criar scraping frágil como dependência principal.

---

# 11. SEGUNDA TAREFA PRINCIPAL — COBERTURA DE NOTÍCIAS

O pipeline GDELT já existe.

Não reescrever antes de testar.

O problema observado foi rate limit.

Primeiro determine:

```text
qual endpoint?
qual frequência?
qual janela?
quantas chamadas?
qual status retornado?
429?
erro silencioso?
timeout?
```

Instrumente logs se necessário.

---

# 12. NÃO CONFUNDIR RESULTADO VAZIO COM SUCESSO

Corrigir qualquer situação em que:

```text
HTTP falhou
```

possa acabar registrada como:

```text
0 notícias encontradas
```

Precisamos distinguir:

```text
SUCCESS_WITH_RESULTS
SUCCESS_EMPTY
RATE_LIMITED
TIMEOUT
HTTP_ERROR
PARSE_ERROR
PARTIAL
```

Isso é obrigatório.

---

# 13. ESTRATÉGIA DE BACKFILL DE NOTÍCIAS

Não consultar 10 anos numa chamada.

Dividir em janelas.

Começar conservadoramente:

```text
7 dias
```

ou:

```text
30 dias
```

dependendo do comportamento real da API.

O pipeline deve adaptar janela caso necessário.

Exemplo:

```text
2018-01-01 → 2018-01-31
2018-02-01 → 2018-02-28
...
```

Com:

```text
retry
backoff
jitter
cache
checkpoint
resume
```

---

# 14. CHECKPOINT

O backfill de notícias pode demorar e sofrer rate limits.

Criar controle persistente:

```text
provider
instrument
start_date
end_date
status
records
attempts
last_attempt
next_retry
```

Se o processo for interrompido:

deve continuar de onde parou.

Nunca reiniciar anos de coleta.

---

# 15. RESPEITAR RATE LIMIT

Não tentar “vencer” o GDELT por força bruta.

Implementar comportamento conservador:

```text
rate limit detectado
↓
backoff
↓
aguardar conforme header se disponível
↓
registrar janela
↓
retomar depois
```

Evitar banimento ou bloqueio.

---

# 16. PARALELISMO

Não usar paralelismo agressivo.

A prioridade é conseguir completar o histórico.

Começar:

```text
1 worker
```

Somente aumentar se a fonte tolerar claramente.

---

# 17. QUERIES DAS EMPRESAS

Revisar aliases.

Especialmente:

```text
Vale
```

que é palavra extremamente ambígua.

Não usar alias fraco isoladamente.

VALE3 deve priorizar combinações como:

```text
"Vale S.A."
"Vale SA"
"VALE3"
```

e outros termos fortes.

Petrobras:

```text
Petrobras
"Petróleo Brasileiro"
PETR4
PETR3
```

Itaú:

```text
"Itaú Unibanco"
"Itau Unibanco"
ITUB4
ITUB3
```

Não depender apenas dos tickers.

---

# 18. COBERTURA EM INGLÊS

Empresas como Vale e Petrobras possuem cobertura internacional importante.

Não limitar automaticamente notícias a português.

Permitir:

```text
Português
Inglês
```

e, se viável:

```text
Espanhol
```

Mas armazenar idioma.

A análise deve saber que uma mesma notícia pode existir em vários idiomas.

---

# 19. ALTERNATIVAS GRATUITAS PARA NOTÍCIAS

Se o GDELT continuar insuficiente, pesquise fontes gratuitas ou datasets públicos que possam complementar a cobertura histórica.

Antes de integrar qualquer fonte nova:

verifique:

```text
licença
uso pessoal permitido
cobertura histórica
data/hora
busca por empresa
rate limit
qualidade
duplicação
disponibilidade futura
```

Prioridade para fontes:

```text
gratuitas
documentadas
reproduzíveis
sem scraping frágil
```

Não integrar fonte só para aumentar números.

---

# 20. NÃO MISTURAR FONTES SEM IDENTIFICAÇÃO

Toda notícia deve continuar contendo:

```text
provider
source_name
domain
provider_id
published_at
ingested_at
raw_file
```

Deduplicação pode agrupar fontes diferentes.

Mas nunca perder provenance.

---

# 21. BACKFILL PROGRESSIVO DE NOTÍCIAS

Ordem:

## Primeiro

```text
PETR4
```

Validar pipeline.

## Depois

```text
VALE3
```

## Depois

```text
ITUB4
```

Não começar três backfills gigantes simultâneos.

---

# 22. JANELA HISTÓRICA

Objetivo preferencial:

```text
2015-01-01 → hoje
```

Se GDELT DOC não cobrir isso adequadamente:

documentar exatamente desde quando a cobertura é confiável.

Não fingir possuir 2015 se efetivamente só existem dados posteriores.

---

# 23. COBERTURA DE NOTÍCIAS — RELATÓRIO

Depois:

```text
ticker
year
raw_articles
canonical_articles
high_relevance
medium_relevance
low_relevance
events
```

Exemplo:

```text
PETR4

2018  ...
2019  ...
2020  ...
...
2026  ...
```

Isso permitirá visualizar buracos.

---

# 24. DETECTAR BURACOS SUSPEITOS

Exemplo:

```text
2020: 3.200 notícias
2021: 2.800
2022: 0
2023: 2.900
```

2022 provavelmente representa falha de coleta.

Criar detector.

Não considerar cobertura completa quando existirem gaps desse tipo.

---

# 25. REEXECUTAR DEDUP

Após aumentar a base:

recalcular/atualizar:

```text
canonical_url
url_hash
title_hash
duplicate_cluster_id
```

Verificar performance com volume maior.

---

# 26. DEDUP ENTRE FONTES

Se houver fonte adicional:

o mesmo evento/artigo pode aparecer:

```text
GDELT
+
outra fonte
```

Deduplicação deve funcionar independentemente de provider.

---

# 27. RELEVÂNCIA

Revalidar principalmente:

```text
VALE3
ITUB4
```

Porque aliases podem produzir muito ruído.

Criar amostra manual.

Selecionar aleatoriamente, por exemplo:

```text
50 high relevance
50 medium
50 low
```

por empresa.

Gerar CSV para inspeção.

Não precisa corrigir manualmente tudo.

O objetivo é estimar se a heurística faz sentido.

---

# 28. CLASSIFICAÇÃO

Após aumentar base:

gerar distribuição:

```text
event_type
count
percentage
```

Se:

```text
80% = other
```

a taxonomia provavelmente está fraca.

Se:

```text
80% = uma única categoria
```

também investigar.

---

# 29. EVENTOS

Reexecutar clustering.

Queremos reduzir:

```text
100 artigos
```

para:

```text
1 evento econômico
```

quando realmente representam o mesmo fato.

Não reduzir acontecimentos distintos publicados no mesmo dia a um único evento.

---

# 30. CONFOUNDING

Manter comportamento já implementado.

Agora, com histórico maior, calcular:

```text
eventos totais
eventos independentes
eventos confounded
percentual confounded
```

por empresa e categoria.

---

# 31. EVENT STUDY

Recalcular estudos após expansão.

Horizontes:

```text
D-60
D-20
D-5

D0

D+1
D+5
D+20
D+60
D+120
D+252
D+504
D+756
```

Se o evento for recente:

marcar censored.

---

# 32. MARKET MODEL

Com preços desde 2010:

teremos muito mais histórico para estimar:

```text
alpha
beta
abnormal return
CAR
```

Verificar se amostras anteriormente marcadas como `low_sample` melhoraram.

---

# 33. FUNDAMENTOS — NÃO REFAZER

A base CVM já passou por validação extensa.

Nesta etapa:

apenas executar auditoria e confirmar:

```text
zero look-ahead
```

após qualquer alteração.

Não refatorar parsing CVM “por limpeza”.

---

# 34. CONFIRMAÇÃO DOS MAPEAMENTOS

Atualmente `company_mapping.yaml` possui mappings ainda marcados:

```text
confirmed: false
```

Quero uma etapa de verificação.

Para:

```text
PETR4
VALE3
ITUB4
```

conferir contra dados oficiais da CVM:

```text
CNPJ
Código CVM
Razão social
```

Se houver correspondência inequívoca:

atualizar:

```text
confirmed: true
```

registrando:

```text
confirmed_at
confirmation_source
```

Não confirmar automaticamente se houver dúvida.

---

# 35. SURVIVORSHIP BIAS

Não é necessário resolver completamente nesta Fase 1.1.

Mas quero preparação séria.

Criar documento:

```text
docs/survivorship_bias_plan.md
```

Explicando:

1. por que existe hoje;
2. como afetará a Fase 3;
3. quais dados serão necessários;
4. como incorporar empresas deslistadas;
5. como reconstruir universo histórico B3;
6. o que NÃO pode ser feito em backtest antes disso.

Não gastar dias implementando agora.

---

# 36. DATABASE_URL

A ausência de `DATABASE_URL` não impede funcionamento.

Se estiver disponível:

testar Psycopg.

Se não estiver:

continuar usando PostgREST.

Não bloquear Fase 1.1 por isso.

Porém, documentar diferença de performance.

---

# 37. PAGINAÇÃO POSTGREST

Com volume histórico muito maior, verificar se alguma consulta aparenta possuir exatamente:

```text
1000
```

ou outro limite típico de paginação.

Isto é crítico.

Confirmar que:

```text
SELECT lógico
```

via backend consegue recuperar dataset completo.

Criar testes para paginação.

---

# 38. TESTES DE VOLUME

Agora que teremos potencialmente:

```text
milhares de preços
dezenas de milhares de notícias
centenas de milhares de fatos
```

testar:

```text
tempo
memória
paginação
batch upsert
dedup
event clustering
```

Não precisa otimização extrema.

Mas não pode quebrar em volume real.

---

# 39. IDEMPOTÊNCIA NOVAMENTE

Após todo backfill:

rodar novamente:

```text
sync-prices
sync-news
sync-cvm
build-events
run-event-study
```

e comparar contagens antes/depois.

Execução repetida sem novos dados deve resultar em contagens estáveis.

---

# 40. ANTI-LOOK-AHEAD

Rodar todos os testes atuais.

Além disso, escolher pelo menos:

```text
10 datas históricas aleatórias
```

por empresa e executar:

```text
get_fundamentals_as_of
```

Confirmar:

```text
available_from <= as_of
```

para todos os fatos utilizados.

---

# 41. TESTE MANUAL DE EVENTOS REAIS

Escolher alguns eventos históricos conhecidos, mas NÃO codificar a interpretação antecipadamente.

Apenas verificar:

```text
notícia encontrada?
timestamp plausível?
empresa correta?
effective_trade_date correto?
preço disponível?
IBOV disponível?
retorno calculado?
evento confounded?
fundamentos as_of?
```

---

# 42. RELATÓRIO DE COBERTURA FINAL

Criar:

```text
data/exports/phase1_1_coverage_report.html
```

ou Markdown.

Mostrar:

# PREÇOS

Por instrumento:

```text
primeira data
última data
pregões
missing
quality findings
```

# FUNDAMENTOS

```text
primeiro documento
último documento
documentos
facts
look-ahead violations
```

# NOTÍCIAS

```text
primeira notícia
última notícia
raw
canonical
alta relevância
média
baixa
gaps
rate limits
```

# EVENTOS

```text
total
independentes
confounded
studies
censored
low_sample
```

---

# 43. CRITÉRIO MÍNIMO DE ACEITE — PREÇOS

Para considerar concluído:

```text
PETR4 >= 2010 → atual
VALE3 >= 2010 → atual
ITUB4 >= 2010 → atual
IBOV  >= 2010 → atual
```

quando tecnicamente disponível na fonte.

Caso um ativo não tenha disponibilidade:

documentar motivo real.

---

# 44. CRITÉRIO MÍNIMO DE ACEITE — NOTÍCIAS

Não quero definir número arbitrário de notícias.

Quero cobertura temporal consistente.

Critério:

```text
PETR4 possui cobertura histórica utilizável
VALE3 possui cobertura histórica utilizável
ITUB4 possui cobertura histórica utilizável
```

e nenhum deles pode estar com:

```text
0 notícias
```

simplesmente por falha de pipeline/rate limit não resolvida.

Se uma fonte realmente não retornar dados:

provar através do relatório de requests.

---

# 45. CRITÉRIO MÍNIMO — EVENT STUDY

Depois do novo histórico:

todos os eventos elegíveis devem possuir estudo calculado quando houver dados suficientes.

Censorship deve ser legítimo, não falha.

---

# 46. CRITÉRIO DE ACEITE — QUALIDADE

Obrigatório:

```text
pytest → verde
ruff → verde
mypy → verde
```

e:

```text
zero look-ahead violations
```

---

# 47. NÃO QUEBRAR O QUE JÁ FUNCIONA

Se uma alteração fizer cair testes existentes:

parar e entender.

Não modificar teste apenas para aceitar comportamento novo, a menos que a regra original esteja comprovadamente errada.

Nesse caso:

documentar.

---

# 48. NÃO FAZER FASE 2 AINDA

Não implementar:

```text
DCF
preço justo
margem de segurança
score de qualidade
comprar
vender
valuation final
```

Mesmo que pareça uma continuação natural.

Primeiro fechar a base.

---

# 49. ORDEM DE EXECUÇÃO

Siga exatamente:

## 1

Diagnóstico dos 651 pregões.

## 2

Correção e backfill de preços.

## 3

Validação de preços/calendário/retornos.

## 4

Diagnóstico do GDELT.

## 5

Backfill PETR4.

## 6

Validar pipeline de notícias.

## 7

Backfill VALE3.

## 8

Backfill ITUB4.

## 9

Dedup/relevância/classificação.

## 10

Eventos.

## 11

Event Studies.

## 12

Confirmar mappings CVM.

## 13

Anti-look-ahead completo.

## 14

Idempotência.

## 15

Relatório final.

---

# 50. CONCLUSÃO QUE ESPERO RECEBER

Ao terminar, atualize `roadmap.md` com uma seção:

```text
## Conclusão Fase 1.1
```

Ela deve responder objetivamente:

### Preços

```text
Por que existiam apenas 651 pregões?
Qual era o bug/limitação?
Foi corrigido?
Qual é agora a primeira data de PETR4?
VALE3?
ITUB4?
IBOV?
Quantos pregões cada um possui?
```

### Notícias

```text
Por que VALE3/ITUB4 estavam zerados?
O rate limit foi contornado corretamente?
Qual a cobertura atual de cada empresa?
Qual a primeira e última notícia?
Quantas notícias raw/canonical/high relevance?
Quais gaps permanecem?
```

### Eventos

```text
Quantos eventos existem por empresa?
Quantos são confounded?
Quantos event studies?
Quais categorias possuem amostra útil?
```

### Fundamentos

```text
Point-in-time continua válido?
Quantos fatos?
Zero look-ahead?
Mappings foram confirmados?
```

### Qualidade

```text
Quantos testes?
ruff?
mypy?
Idempotência?
```

### Limitações

Liste SOMENTE limitações reais restantes.

### Decisão final

Responda:

```text
A Fase 1 possui profundidade histórica suficiente para iniciar a Fase 2?
SIM / NÃO
```

E justifique tecnicamente.

---

# 51. DEFINIÇÃO DE PRONTO

A Fase 1.1 estará concluída quando pudermos selecionar uma data histórica e reconstruir:

```text
preço
benchmark
retornos prévios
fundamentos conhecidos naquela data
notícias existentes naquele período
eventos relevantes
reação posterior da ação
retorno relativo ao mercado
```

com profundidade histórica suficiente para que isso não funcione apenas em alguns poucos anos recentes.

Somente depois disso avançaremos para:

```text
FASE 2
QUALIDADE
+
VALUATION
+
PREÇO JUSTO
+
MARGEM DE SEGURANÇA
```

Não antecipe essa fase.
