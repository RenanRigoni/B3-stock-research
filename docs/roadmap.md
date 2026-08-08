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
| 12 | Ponta a ponta | `pipeline`, validação em PETR4/VALE3/ITUB4, docs finais | §112–125 | ⬜ |

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
