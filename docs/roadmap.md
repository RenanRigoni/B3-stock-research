# Roadmap da Fase 1

Ordem derivada de `fase1.md` §106. Cada milestone termina com `pytest` + `ruff check`
verdes e o critério de aceite da seção correspondente satisfeito. Um milestone não começa
antes do anterior fechar.

| # | Milestone | Entrega | Aceite (`fase1.md`) | Status |
|---|---|---|---|---|
| 0 | **Base** | Repo, schema, config, CLI, backends de banco | — | ✅ |
| 1 | Preços | Adapter yfinance, `sync-prices`, `update-prices`, bruto preservado | §108 | ✅ |
| 2 | Retornos + calendário | `trading_calendar` do `^BVSP`, `daily_returns`, D+N por pregão | §14, §16 | ✅ |
| 3 | Qualidade + brapi | Checks de preço, `validate-prices`, brapi opcional | §21, §22 | ✅ |
| 4 | CVM bruto | Cadastro, DFP/ITR, checksum, staging | §42–46 | ✅ |
| 5 | Fundamentos point-in-time | `available_from`, `get_fundamentals_as_of`, testes anti-look-ahead | §47–52, §110 | ✅ |
| 6 | Notícias | Adapter GDELT, bruto preservado, normalização | §24–28 | ✅ |
| 7 | Dedup + linking | Clusters, `news_company_links`, relevância | §29–31, §36 | ✅ |
| 8 | Classificação | Heurística + taxonomia, sem API paga obrigatória | §33–37 | ✅ |
| 9 | Eventos | Clustering, `effective_trade_date`, confounding | §38–41, §93 | ✅ |
| 10 | Event study | Retornos, excesso, market model, CAR | §53–60, §111 | ✅ |
| 11 | Relatórios | `audit`, `report`, `backup` | §71–74, §100 | ✅ |
| 12 | Ponta a ponta | `pipeline`, validação em PETR4/VALE3/ITUB4, docs finais | §112–125 | ✅ |

## O que já está pronto (Milestones 0-5)

- Repositório git + GitHub privado
- Supabase `B3 FOCUS` (`sa-east-1`, PG 17): 25 tabelas, 10 views, RLS em tudo
- `config/` versionado: settings, companies, taxonomia, mapping CVM (resolvido com CNPJ/código CVM reais)
- `stock_research.db` com dois backends (psycopg e PostgREST), escolha automática, tipos temporais coerentes entre os dois
- `stock-research doctor` / `init` / `status` / `sync-prices` / `update-prices` / `validate-prices` / `sync-cvm` funcionando
- Universo carregado e **idempotência verificada** em preços e fundamentos (execuções repetidas, contagens estáveis via SQL direto)
- Preços reais de PETR4/VALE3/ITUB4/IBOV sincronizados, `daily_returns` calculado, `trading_calendar` construído a partir do IBOV
- DFP+ITR 2024 reais ingeridos (16.210 fatos, 14 documentos) para as 3 empresas
- `fundamental_metrics` calculado e validado contra dados reais: ITUB4 (banco) corretamente bloqueia capex/ebit/FCF como `sector_inadequate`; PETR4 com receita/margem/ROE plausíveis
- **Suíte anti-look-ahead** (`tests/unit/test_lookahead.py`, 12 testes) validando `select_point_in_time` diretamente, incluindo o cenário literal do fase1.md §63; validado também contra dados reais (zero violações em 3 janelas `as_of` distintas)
- 173 testes offline passando, ruff limpo, mypy limpo nos módulos novos (7 erros pré-existentes em `db/connection.py`, não bloqueantes)

## Bugs encontrados e corrigidos durante a validação real

Nenhum destes apareceu nos testes unitários com fixtures pequenas — só bateram ao rodar
contra o Supabase e os ZIPs reais da CVM. Registrados aqui porque o padrão importa para
os próximos milestones:

1. **`normalize_fiscal_year_order`**: `"ÚLTIMO".encode("ascii", errors="ignore")` descarta a
   letra inteira (vira `"LTIMO"`, 5 letras) em vez de transliterar o acento — faltava
   `unicodedata.normalize("NFKD", ...)` antes do encode. `company_registry.py` já fazia
   certo; `fundamentals_facts.py` não.
2. **Upsert com payload parcial em coluna NOT NULL**: Postgres valida NOT NULL na tupla do
   INSERT tentado *antes* de decidir que vai cair em `ON CONFLICT DO UPDATE` — mesmo quando
   a linha já existe. `_sync_instrument_identifiers` upava `instruments` só com
   `ticker/exchange/cnpj/cvm_code`, sem `company_name` (NOT NULL): quebrava sempre. Corrigido
   para UPDATE puro (o instrumento sempre já existe, criado por `init`).
3. **DMPL tem uma dimensão extra (`COLUNA_DF`)** que `compute_source_row_hash` não
   contemplava: o mesmo `account_code` se repete uma vez por componente do patrimônio,
   colidindo no mesmo lote (`ON CONFLICT DO UPDATE command cannot affect row a second time`).
   Excluído do escopo, com a mesma justificativa de `composicao_capital`/`parecer`.
4. **`_scaled(_match_one(...))` sem guarda de `None`**: quebraria com `AttributeError` sempre
   que o período comparativo (PENÚLTIMO) não existisse (ex. primeiro ano de dados de uma
   empresa). `_scaled` passou a ser `None`-safe.
5. **Backend REST devolve data/timestamp como string**, backend `psycopg` devolve objetos
   nativos — quebrava qualquer comparação `available_from <= boundary` (o coração do
   point-in-time) quando rodando sem `DATABASE_URL`. `rest.py` agora coage os dois formatos
   na leitura, para os dois backends se comportarem igual (contrato que `db/__init__.py`
   já prometia, mas não cumpria).
6. **`company_name` promovido a alias forte mesmo quando o próprio YAML classifica esse termo
   como fraco.** VALE3 tem `company_name: Vale` e `aliases.weak: [Vale]`; a promoção
   automática ignorava a classificação explícita, reintroduzindo o ruído que ela existe para
   evitar (contamina exatamente a query de notícias do Milestone 6). Corrigido em
   `pipelines/universe.py`, com teste de regressão e reload confirmado no banco.
7. **`ON CONFLICT DO UPDATE` rejeita colisão dentro do mesmo lote** (achado 2×: primeiro em
   DMPL, depois em `news_articles`). O backend REST manda um `INSERT` com várias `VALUES` numa
   chamada só; se duas linhas do lote tiverem a mesma chave de conflito — caso real:
   `http://` e `https://` do mesmo artigo colapsando pro mesmo `url_hash` após a
   canonicalização — o Postgres rejeita com *"cannot affect row a second time"*. `psycopg`
   nunca sofria disso (processa linha a linha). Corrigido na camada certa: `db/rest.py`
   deduplica por `conflict_columns` antes de montar o request, em vez de cada pipeline ter
   que lembrar disso sozinho.

## Bloqueios conhecidos

**`DATABASE_URL` não configurada.** Só temos as API keys do Supabase; a senha do Postgres
não foi capturada. O backend PostgREST cobre todos os pipelines (M1-M5 validados end-to-end
nele), então nada está parado — mas ele é mais lento e não tem transação multi-tabela.

Para desbloquear o caminho rápido:
Supabase Dashboard → Project Settings → Database → Connection string → URI (Session
pooler, porta 5432) → colar em `DATABASE_URL` no `.env`.

## Definição de pronto da Fase 1

Não é "tem muitos dados". É:

> Conseguimos reconstruir de forma confiável o contexto histórico de uma ação em uma data
> usando **somente** a informação disponível naquele momento, relacionar eventos à evolução
> posterior do preço, e medir a reação de forma absoluta e relativa ao mercado.

Se a suíte anti-look-ahead falhar, a Fase 1 **não** está pronta — independentemente do que
os outros números digam.

---

## Conclusão final (fase1.md §125-126)

Os 12 milestones estão implementados, testados e validados contra dados reais — não só
contra fixtures. 321 testes unitários passando, `ruff` e `mypy` limpos em todo o código
(`src/`: 58 arquivos), zero segredos versionados. Repositório:
[github.com/RenanRigoni/B3-stock-research](https://github.com/RenanRigoni/B3-stock-research).

### O que está funcionando

Todo o pipeline `stock-research pipeline --ticker X` roda ponta a ponta: preços → cadastro
CVM → fundamentos → notícias → dedup → relevância → classificação → eventos → event study →
audit → relatório. Estado final do banco (Supabase `B3 FOCUS`, `sa-east-1`):

| | PETR4 | VALE3 | ITUB4 | IBOV |
|---|---|---|---|---|
| Pregões | 651 | 651 | 651 | 651 |
| Documentos CVM | 74 | 86 | 82 | — |
| Notícias | 129 | 0 | 0 | — |
| Eventos / studies | 38 / 31 | 0 / 0 | 0 / 0 | — |

Total de fatos contábeis (`financial_statement_facts`) nas três empresas: **230.990**.

### O que foi validado (não só implementado)

- **Point-in-time de fundamentos**: zero violações em dados reais, em 3 janelas `as_of`
  distintas contra os fatos ingeridos da CVM (Milestone 5).
- **Point-in-time de eventos** (`effective_trade_date`): bug real de direção nos horizontes
  pré-evento pego pelo teste antes de tocar o banco (Milestone 10) — corrigido antes de
  qualquer dado real ser gravado com o sinal errado.
- **Dedup por similaridade**: validado com um par real de republicação do GDELT que a métrica
  inicial (`token_sort_ratio`) deixava passar; recalibrado para `token_set_ratio` com o motivo
  documentado no código (Milestone 7).
- **Relevância e classificação**: os 7 artigos sobre "Lava Jato" que o GDELT trouxe numa busca
  por Petrobras (por full-text, não título) foram corretamente marcados como baixa relevância
  — confirma que as camadas de coleta, relevância e classificação se encaixam como projetado
  antes de qualquer decisão automática usar esses dados (Milestones 6-8).
- **Confounding**: 26 dos 38 eventos de PETR4 caem no mesmo pregão real (dia do resultado do
  2º trimestre), corretamente marcados como confundidos entre si — não é ruído, é o cenário
  literal do fase1.md §93 acontecendo com dado real.
- **Idempotência**: verificada por contagem direta no banco (não só pela saída do CLI) em
  preços, fundamentos, notícias, clusters, eventos e event studies — todos estáveis em
  execuções repetidas.
- **Bugs de infraestrutura**: 9 bugs reais documentados no changelog acima, todos encontrados
  rodando contra o Supabase/CVM/GDELT reais (nenhum apareceu nos testes com fixture pequena),
  todos corrigidos com teste de regressão.

### Fontes utilizadas

yfinance (preços), CVM Dados Abertos (fundamentos, DFP/ITR 2010-2026), GDELT DOC 2.0 API
(notícias). brapi não foi exercitada nesta validação (sem `BRAPI_TOKEN`) — o pipeline
principal funciona sem ela por design (Milestone 3).

### Limitações que permanecem

Ver [docs/limitations.md](limitations.md) para a lista completa. As que mais pesam para uso
imediato:

- **Rate limit do GDELT neste ambiente é mais rígido que o documentado** — provavelmente por
  IP compartilhado. VALE3 e ITUB4 ficaram sem notícias nesta rodada por isso, não por bug: o
  mecanismo (retry, backoff, `quality_findings` registrando o esgotamento) funcionou
  exatamente como projetado nos dois casos observados (sucesso vazio silencioso no VALE3,
  esgotamento com warning registrado no ITUB4). Rodar de uma rede pessoal deve resolver.
- **CVM não publica ITR de 2010** no endpoint de dados abertos (404 real, confirmado) — gap da
  fonte, não do pipeline. Histórico de fundamentos é sólido a partir de 2011.
- **Survivorship bias** não resolvido — universo só tem empresas vivas (ver limitations.md).
- **`DATABASE_URL` não configurada** — tudo roda no backend PostgREST (mais lento, sem
  transação multi-tabela), que é por isso mesmo mais testado nesta validação que o `psycopg`.

### Dados com maior risco de inconsistência

- Métricas fundamentalistas com `quality_flag != 'ok'` (`missing_input`, `estimated`) — sempre
  checar o `quality_reason` antes de usar.
- `event_studies.low_sample = true` — alpha/beta estimados com poucas observações.
- Eventos com `is_confounded = true` — reação de preço não pode ser atribuída a um só evento.
- Categorização de notícia sem `category` (heurística não reconheceu nenhum termo da
  taxonomia) — não é erro, é honestidade sobre o limite da heurística.

### O que exige revisão manual

`stock-research audit` expõe isso diretamente: `v_manual_review_queue` (movimento de preço
sem evento associado, relevância ambígua, timestamp incerto) e
`config/company_mapping.yaml` (CNPJ/código CVM resolvidos automaticamente, mas
`confirmed: false` até um humano conferir contra a fonte oficial).

### Risco de look-ahead

**Nenhum encontrado.** `tests/unit/test_lookahead.py` (12 testes, incluindo o cenário literal
do fase1.md §63) e a validação contra os 230.990 fatos reais confirmam: nenhum fato usado numa
consulta `as_of` tem `available_from` posterior ao boundary consultado. Esta é a garantia mais
importante do projeto e ela se sustenta.

### A Fase 1 está pronta para servir de base à Fase 2?

**Sim, com uma ressalva operacional clara.** A infraestrutura (schema, point-in-time,
idempotência, rastreabilidade, qualidade) está comprovadamente correta e testada — inclusive
sob dados reais e mensagens de erro reais de três fontes externas independentes. O que a Fase
2 (valuation + qualidade) vai precisar já está disponível: preços point-in-time, fundamentos
com `available_from`, ações corporativas, contexto histórico de eventos, benchmark. A ressalva
é de **volume**, não de **arquitetura**: notícias e eventos hoje só têm profundidade real em
PETR4; VALE3 e ITUB4 precisam de uma nova rodada de `sync-news` fora do horário de pico do
rate limit do GDELT antes de qualquer análise cross-company de notícias fazer sentido. Preços
e fundamentos, que são o essencial para a Fase 2, já estão completos nas três.
