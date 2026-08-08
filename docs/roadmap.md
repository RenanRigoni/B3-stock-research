# Roadmap da Fase 1

Ordem derivada de `fase1.md` §106. Cada milestone termina com `pytest` + `ruff check`
verdes e o critério de aceite da seção correspondente satisfeito. Um milestone não começa
antes do anterior fechar.

| # | Milestone | Entrega | Aceite (`fase1.md`) | Status |
|---|---|---|---|---|
| 0 | **Base** | Repo, schema, config, CLI, backends de banco | — | ✅ |
| 1 | Preços | Adapter yfinance, `sync-prices`, `update-prices`, bruto preservado | §108 | ⬜ |
| 2 | Retornos + calendário | `trading_calendar` do `^BVSP`, `daily_returns`, D+N por pregão | §14, §16 | ⬜ |
| 3 | Qualidade + brapi | Checks de preço, `validate-prices`, brapi opcional | §21, §22 | ⬜ |
| 4 | CVM bruto | Cadastro, DFP/ITR, checksum, staging | §42–46 | ⬜ |
| 5 | Fundamentos point-in-time | `available_from`, `get_fundamentals_as_of`, testes anti-look-ahead | §47–52, §110 | ⬜ |
| 6 | Notícias | Adapter GDELT, bruto preservado, normalização | §24–28 | ⬜ |
| 7 | Dedup + linking | Clusters, `news_company_links`, relevância | §29–31, §36 | ⬜ |
| 8 | Classificação | Heurística + taxonomia, sem API paga obrigatória | §33–37 | ⬜ |
| 9 | Eventos | Clustering, `effective_trade_date`, confounding | §38–41, §93 | ⬜ |
| 10 | Event study | Retornos, excesso, market model, CAR | §53–60, §111 | ⬜ |
| 11 | Relatórios | `audit`, `report`, event browser, `backup` | §71–74 | ⬜ |
| 12 | Ponta a ponta | `pipeline`, validação em PETR4/VALE3/ITUB4, docs finais | §112–125 | ⬜ |

## O que já está pronto (Milestone 0)

- Repositório git + GitHub privado
- Supabase `B3 FOCUS` (`sa-east-1`, PG 17): 25 tabelas, 10 views, RLS em tudo
- `config/` versionado: settings, companies, taxonomia, mapping CVM
- `stock_research.db` com dois backends (psycopg e PostgREST), escolha automática
- `stock-research doctor` / `init` / `status` funcionando
- Universo carregado e **idempotência verificada** (execuções repetidas, contagens estáveis)
- 27 testes offline passando, ruff limpo

## Bloqueios conhecidos

**`DATABASE_URL` não configurada.** Só temos as API keys do Supabase; a senha do Postgres
não foi capturada. O backend PostgREST cobre todos os pipelines, então nada está parado —
mas ele é mais lento e não tem transação multi-tabela.

Para desbloquear o caminho rápido:
Supabase Dashboard → Project Settings → Database → Connection string → URI (Session
pooler, porta 5432) → colar em `DATABASE_URL` no `.env`.

Isso importa principalmente no **Milestone 4** (a CVM gera milhões de linhas, onde a
diferença entre `COPY` e JSON-sobre-HTTP é grande).

## Definição de pronto da Fase 1

Não é "tem muitos dados". É:

> Conseguimos reconstruir de forma confiável o contexto histórico de uma ação em uma data
> usando **somente** a informação disponível naquele momento, relacionar eventos à evolução
> posterior do preço, e medir a reação de forma absoluta e relativa ao mercado.

Se a suíte anti-look-ahead falhar, a Fase 1 **não** está pronta — independentemente do que
os outros números digam.
