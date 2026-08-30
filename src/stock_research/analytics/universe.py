"""Universo point-in-time -- estrutural (M1) e investivel (M2).

Duas camadas, deliberadamente separadas (Opus, "structural vs investable"):

    structural_universe(D)  = companhia/instrumento existia e NEGOCIAVA em D
                              (so TEMPO EFETIVO do lifecycle -- Handoff §6)

    investable_universe(D)  = structural
                              + instrumento identificavel  (resolution)
                              + serie de preco ligada      (price link)
                              + liquidez suficiente        (liquidity)
                              + dados minimos              (data minimums)

Uma companhia em `company_lifecycle` NUNCA vira ativo investivel
automaticamente. Cada reprovacao tem motivo explicito e e CONTADA -- nenhum
instrumento some entre uma etapa e outra (`fase3.md` §94).

`select_structural_universe` e `apply_investable_gates` sao funcoes PURAS --
espelham `analytics.fundamentals.select_point_in_time`: selecao separada do
acesso a banco, testavel sem SQL.

CONTRATO BITEMPORAL (Handoff §6, inalterado):

* A elegibilidade ESTRUTURAL usa SO `valid_from`/`valid_to`/`listing_start`/
  `listing_end`. NENHUMA referencia a `source_available_from` /
  `source_observed_at` / `ingested_at`.
* `source_reference_year_first` entra APENAS na camada investivel, como
  criterio de identificabilidade (mesma classe de "tem preco"), sempre
  contado quando reprova. Isso NAO e o bug bitemporal da v1: a v1 dizia "o
  pipeline baixou em 2026, logo nada e elegivel em 2013" (falso -- a
  existencia era publica); aqui e "nenhuma fonte diz qual era o ticker em
  2013, logo nao ha como ligar serie de preco" (verdadeiro, conservador,
  medido).
* O retorno NAO expoe `valid_to` nem `listing_end` (Handoff §5.3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from stock_research.db import fetch_all
from stock_research.transforms.company_lifecycle import company_eligible_at
from stock_research.transforms.instrument_lifecycle import (
    BACK_PROJECTED,
    IDENTIFIABLE,
    RESOLVED,
    SEEDED,
    UNRESOLVED_INVALID_CODE,
    UNRESOLVED_NO_CODE,
    instrument_eligible_at,
    resolution_status,
)

# Motivos de inelegibilidade (vocabulario fechado -- Opus regra 8).
NOT_ELIGIBLE_DATA = "NOT_ELIGIBLE_DATA"
UNRESOLVED_INSTRUMENT = "unresolved_instrument"
BACK_PROJECTED_INSTRUMENT = "back_projected_instrument"
NO_PRICE_LINK = "no_price_link"
ILLIQUID = "illiquid"
INSUFFICIENT_TRADING_HISTORY = "insufficient_trading_history"
INSUFFICIENT_DATA = "insufficient_data"

REJECTION_REASONS = (
    NOT_ELIGIBLE_DATA,
    UNRESOLVED_INSTRUMENT,
    BACK_PROJECTED_INSTRUMENT,
    NO_PRICE_LINK,
    ILLIQUID,
    INSUFFICIENT_TRADING_HISTORY,
    INSUFFICIENT_DATA,
)

# Ordem de preferencia quando a mesma (companhia, classe) tem varias linhas de
# nomenclatura -- SSBR3 -> ALSO3 -> ALOS3 sao a MESMA acao ordinaria, nao tres.
_STATUS_RANK = {
    RESOLVED: 0,
    SEEDED: 1,
    BACK_PROJECTED: 2,
    UNRESOLVED_NO_CODE: 3,
    UNRESOLVED_INVALID_CODE: 3,
}


@dataclass(frozen=True)
class UniverseInstrument:
    """O que a camada de estrategia ve. Sem `valid_to`/`listing_end`."""

    company_id: int
    cnpj: str
    instrument_id: int | None
    ticker: str | None
    share_class: str
    market: str | None
    listing_venue: str | None
    segment: str | None
    quality_flag: str
    resolution: str = RESOLVED


@dataclass(frozen=True)
class Rejection:
    instrument: UniverseInstrument
    reason: str
    detail: str | None = None


@dataclass(frozen=True)
class StructuralResult:
    as_of: date
    companies: tuple[tuple[int, str], ...]
    instruments: tuple[UniverseInstrument, ...]
    not_eligible_data: tuple[UniverseInstrument, ...]
    naming_variants_collapsed: int = 0

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(i.ticker for i in self.instruments if i.ticker)


@dataclass(frozen=True)
class InvestableResult:
    as_of: date
    structural: StructuralResult
    instruments: tuple[UniverseInstrument, ...]
    rejections: tuple[Rejection, ...] = field(default_factory=tuple)

    @property
    def tickers(self) -> tuple[str, ...]:
        return tuple(i.ticker for i in self.instruments if i.ticker)

    @property
    def rejection_counts(self) -> dict[str, int]:
        out = dict.fromkeys(REJECTION_REASONS, 0)
        out[NOT_ELIGIBLE_DATA] = len(self.structural.not_eligible_data)
        for r in self.rejections:
            out[r.reason] = out.get(r.reason, 0) + 1
        return out

    def rejected_for(self, reason: str) -> tuple[Rejection, ...]:
        return tuple(r for r in self.rejections if r.reason == reason)


# ---------------------------------------------------------------------------
# Camada 1 -- estrutural (M1, so tempo efetivo)
# ---------------------------------------------------------------------------


def _entry(row: dict[str, Any], company_id: int, cnpj: str, as_of: date) -> UniverseInstrument:
    return UniverseInstrument(
        company_id=company_id,
        cnpj=cnpj,
        instrument_id=row.get("instrument_id"),
        ticker=row.get("ticker"),
        share_class=row["share_class"],
        market=row.get("market"),
        listing_venue=row.get("listing_venue"),
        segment=row.get("segment"),
        quality_flag=row.get("quality_flag", "ok"),
        resolution=resolution_status(row, as_of),
    )


def select_structural_universe(
    company_rows: list[dict[str, Any]],
    instrument_rows: list[dict[str, Any]],
    as_of: date,
    *,
    include_suspended: bool = False,
    allowed_share_classes: frozenset[str] | None = None,
) -> StructuralResult:
    """Camada estrutural: existia e negociava em `as_of`. Funcao pura.

    Granularidade: **uma entrada por (company_id, share_class)**. Linhas de
    nomenclatura sucessiva (SSBR3 -> ALSO3 -> ALOS3) sao a MESMA acao ordinaria
    -- conta-las como tres inflaria `structural_instruments`. A variante que
    representa a classe em `as_of` e escolhida por `resolution_status`
    (`resolved` primeiro, depois o ticker observado MAIS RECENTE que ja existia
    em `as_of`), nunca pelo ticker atual retroagido. As demais sao contadas em
    `naming_variants_collapsed` -- nao somem em silencio.
    """
    eligible_company: dict[int, str] = {}
    for row in company_rows:
        cid = row["company_id"]
        if not company_eligible_at(row, as_of):
            continue
        if not include_suspended and row.get("registration_status") == "suspended":
            continue
        eligible_company[cid] = row["cnpj"]

    best: dict[tuple[int, str], dict[str, Any]] = {}
    not_eligible_data: list[UniverseInstrument] = []
    collapsed = 0
    for row in instrument_rows:
        cid = row["company_id"]
        if cid not in eligible_company:
            continue
        if allowed_share_classes is not None and row["share_class"] not in allowed_share_classes:
            continue
        cnpj = eligible_company[cid]
        if row.get("valid_from") is None or row.get("listing_start") is None:
            # Handoff §5.1: NULL em data efetiva nunca cai no filtro em
            # silencio -- vai para o balde contado.
            not_eligible_data.append(_entry(row, cid, cnpj, as_of))
            continue
        if not instrument_eligible_at(row, as_of):
            continue
        key = (cid, row["share_class"])
        cur = best.get(key)
        if cur is None:
            best[key] = row
        else:
            collapsed += 1
            if _prefer(row, cur, as_of):
                best[key] = row

    instruments = [
        _entry(r, r["company_id"], eligible_company[r["company_id"]], as_of) for r in best.values()
    ]
    instruments.sort(key=lambda i: (i.company_id, i.share_class))

    return StructuralResult(
        as_of=as_of,
        companies=tuple(sorted(eligible_company.items())),
        instruments=tuple(instruments),
        not_eligible_data=tuple(not_eligible_data),
        naming_variants_collapsed=collapsed,
    )


def _prefer(candidate: dict[str, Any], incumbent: dict[str, Any], as_of: date) -> bool:
    """Qual linha representa a (companhia, classe) em `as_of`.

    1. status: `resolved` > `seeded` > `back_projected` > `unresolved_*`.
    2. entre `resolved`: o ticker OBSERVADO MAIS RECENTE que ja existia em
       `as_of` (maior `source_reference_year_first <= as_of.year`). Em 2020 a
       ALLOS e ALSO3 (observado 2019), nao ALOS3 (so existe desde 2023).
    3. determinismo: ordem do ticker.
    """
    ck = _STATUS_RANK.get(resolution_status(candidate, as_of), 9)
    ik = _STATUS_RANK.get(resolution_status(incumbent, as_of), 9)
    if ck != ik:
        return ck < ik
    cy = candidate.get("source_reference_year_first") or 0
    iy = incumbent.get("source_reference_year_first") or 0
    if cy != iy:
        return cy > iy
    return (candidate.get("ticker") or "") > (incumbent.get("ticker") or "")


# Alias historico -- o M1 chamava a camada estrutural de "investable".
select_investable_universe = select_structural_universe


# ---------------------------------------------------------------------------
# Camada 2 -- investivel (M2)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InvestabilityInputs:
    """Tudo que a camada investivel precisa alem do lifecycle.

    `price_dates`   : instrument_id -> (primeira, ultima) data com preco <= D.
                      Ausente = sem serie ligada.
    `liquidity`     : instrument_id -> linha de `liquidity_metrics` em D.
    `data_ready`    : instrument_id -> True quando os dados minimos exigidos
                      estao disponiveis (fundamentos etc.). Ausente = nao.
    """

    price_dates: dict[int, tuple[date, date]] = field(default_factory=dict)
    liquidity: dict[int, dict[str, Any]] = field(default_factory=dict)
    data_ready: dict[int, bool] = field(default_factory=dict)


@dataclass(frozen=True)
class InvestabilityThresholds:
    """Limiares da config. `None` = gate NAO aplicado (limiar ainda nao
    aprovado) -- a distribuicao e medida e reportada, mas nada e reprovado por
    ele. Nunca inventar numero aqui (`fase3.md` §15)."""

    min_avg_financial_volume_60: float | None = None
    min_median_financial_volume_60: float | None = None
    min_trading_days_60: int | None = None
    min_price_history_days: int | None = None
    require_fundamentals: bool | None = None

    @property
    def liquidity_active(self) -> bool:
        return (
            self.min_avg_financial_volume_60 is not None
            or self.min_median_financial_volume_60 is not None
        )

    @property
    def history_active(self) -> bool:
        return self.min_trading_days_60 is not None or self.min_price_history_days is not None


def apply_investable_gates(
    structural: StructuralResult,
    inputs: InvestabilityInputs,
    thresholds: InvestabilityThresholds,
) -> InvestableResult:
    """structural -> resolution -> price link -> liquidity -> data -> ELIGIBLE.

    Funcao pura. Cada reprovacao vira uma `Rejection` com motivo explicito; a
    soma de elegiveis + reprovados e sempre igual ao total estrutural.
    """
    as_of = structural.as_of
    eligible: list[UniverseInstrument] = []
    rejections: list[Rejection] = []

    for inst in structural.instruments:
        # --- 1. resolucao do instrumento -----------------------------------
        if inst.resolution not in IDENTIFIABLE:
            reason = (
                BACK_PROJECTED_INSTRUMENT
                if inst.resolution == BACK_PROJECTED
                else UNRESOLVED_INSTRUMENT
            )
            rejections.append(Rejection(inst, reason, inst.resolution))
            continue

        # --- 2. ligacao com serie de preco ---------------------------------
        # "existe preco ate D" -- NAO significa "negociava perto de D". A
        # recencia e problema dos gates de liquidez/historico abaixo, para um
        # preco antigo isolado nunca transformar um zumbi em investivel.
        span = inputs.price_dates.get(inst.instrument_id) if inst.instrument_id else None
        if span is None:
            rejections.append(
                Rejection(inst, NO_PRICE_LINK, "sem serie de preco ligada ate a data")
            )
            continue

        liq = inputs.liquidity.get(inst.instrument_id) if inst.instrument_id else None

        # --- 3. historico minimo de negociacao -----------------------------
        if thresholds.history_active:
            failed = _history_failure(liq, span, as_of, thresholds)
            if failed is not None:
                rejections.append(Rejection(inst, INSUFFICIENT_TRADING_HISTORY, failed))
                continue

        # --- 4. liquidez ----------------------------------------------------
        if thresholds.liquidity_active:
            failed = _liquidity_failure(liq, thresholds)
            if failed is not None:
                rejections.append(Rejection(inst, ILLIQUID, failed))
                continue

        # --- 5. dados minimos ----------------------------------------------
        if thresholds.require_fundamentals and not inputs.data_ready.get(
            inst.instrument_id or -1, False
        ):
            rejections.append(
                Rejection(inst, INSUFFICIENT_DATA, "fundamentos exigidos e ausentes")
            )
            continue

        eligible.append(inst)

    return InvestableResult(
        as_of=as_of,
        structural=structural,
        instruments=tuple(eligible),
        rejections=tuple(rejections),
    )


def _history_failure(
    liq: dict[str, Any] | None,
    span: tuple[date, date],
    as_of: date,
    th: InvestabilityThresholds,
) -> str | None:
    if th.min_price_history_days is not None:
        first = span[0]
        if (as_of - first).days < th.min_price_history_days:
            return f"historico de {(as_of - first).days}d < {th.min_price_history_days}d"
    if th.min_trading_days_60 is not None:
        observed = (liq or {}).get("trading_days_60")
        if observed is None:
            return "sem liquidity_metrics na data"
        if observed < th.min_trading_days_60:
            return f"trading_days_60={observed} < {th.min_trading_days_60}"
    return None


def _liquidity_failure(liq: dict[str, Any] | None, th: InvestabilityThresholds) -> str | None:
    if liq is None:
        return "sem liquidity_metrics na data"
    if th.min_avg_financial_volume_60 is not None:
        v = liq.get("avg_financial_volume_60")
        if v is None or float(v) < th.min_avg_financial_volume_60:
            return f"avg_financial_volume_60={v} < {th.min_avg_financial_volume_60}"
    if th.min_median_financial_volume_60 is not None:
        v = liq.get("median_financial_volume_60")
        if v is None or float(v) < th.min_median_financial_volume_60:
            return f"median_financial_volume_60={v} < {th.min_median_financial_volume_60}"
    return None


# ---------------------------------------------------------------------------
# Acesso a banco (fino -- a logica esta nas funcoes puras acima)
# ---------------------------------------------------------------------------

_COMPANY_QUERY = """
    select l.company_id, c.cnpj, l.valid_from, l.valid_to, l.registration_status,
           l.event_type, l.source
    from public.company_lifecycle l
    join public.companies c on c.company_id = l.company_id
"""

_INSTRUMENT_QUERY = """
    select l.company_id, c.cnpj, l.instrument_id, l.ticker, l.share_class,
           l.valid_from, l.valid_to, l.listing_start, l.listing_end,
           l.market, l.listing_venue, l.segment, l.quality_flag, l.source,
           l.source_reference_year, l.source_reference_year_first
    from public.instrument_lifecycle l
    join public.companies c on c.company_id = l.company_id
"""


def load_lifecycles() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    company_rows = fetch_all(_COMPANY_QUERY)
    instrument_rows = fetch_all(_INSTRUMENT_QUERY)
    for r in company_rows:
        r["valid_from"] = _as_date(r["valid_from"])
        r["valid_to"] = _as_date(r["valid_to"])
    for r in instrument_rows:
        for k in ("valid_from", "valid_to", "listing_start", "listing_end"):
            r[k] = _as_date(r[k])
    return company_rows, instrument_rows


def get_structural_universe_as_of(
    as_of: date,
    *,
    include_suspended: bool = False,
    allowed_share_classes: frozenset[str] | None = None,
) -> StructuralResult:
    """Universo ESTRUTURAL em `as_of` (existia e negociava). Sem gate de
    identificabilidade, preco ou liquidez."""
    company_rows, instrument_rows = load_lifecycles()
    return select_structural_universe(
        company_rows,
        instrument_rows,
        as_of,
        include_suspended=include_suspended,
        allowed_share_classes=allowed_share_classes,
    )


def load_price_spans(as_of: date) -> dict[int, tuple[date, date]]:
    """`instrument_id -> (primeira, ultima)` data com preco `<= as_of`.

    Point-in-time por construcao: `trade_date <= as_of`. Ausencia da chave =
    nenhuma serie ligada.
    """
    rows = fetch_all(
        "select instrument_id, min(trade_date) as first_date, max(trade_date) as last_date "
        "from public.daily_prices where trade_date <= %s group by instrument_id",
        [as_of],
    )
    out: dict[int, tuple[date, date]] = {}
    for r in rows:
        first, last = _as_date(r["first_date"]), _as_date(r["last_date"])
        if first is not None and last is not None:
            out[int(r["instrument_id"])] = (first, last)
    return out


def load_liquidity(as_of: date) -> dict[int, dict[str, Any]]:
    """Ultima linha de `liquidity_metrics` com `as_of_date <= as_of`."""
    rows = fetch_all(
        """
        select distinct on (instrument_id)
               instrument_id, as_of_date, avg_volume_20, avg_volume_60,
               avg_financial_volume_20, avg_financial_volume_60,
               median_financial_volume_60, trading_days_20, trading_days_60,
               expected_trading_days_20, expected_trading_days_60, quality_flag
        from public.liquidity_metrics
        where as_of_date <= %s
        order by instrument_id, as_of_date desc
        """,
        [as_of],
    )
    return {int(r["instrument_id"]): r for r in rows}


def get_investable_universe_as_of(
    as_of: date,
    *,
    include_suspended: bool = False,
    allowed_share_classes: frozenset[str] | None = None,
    thresholds: InvestabilityThresholds | None = None,
) -> InvestableResult:
    """Universo INVESTIVEL em `as_of` -- as 5 etapas, com contagem por motivo."""
    structural = get_structural_universe_as_of(
        as_of, include_suspended=include_suspended, allowed_share_classes=allowed_share_classes
    )
    inputs = InvestabilityInputs(
        price_dates=load_price_spans(as_of),
        liquidity=load_liquidity(as_of),
    )
    return apply_investable_gates(
        structural, inputs, thresholds or InvestabilityThresholds()
    )


def _as_date(value: Any) -> date | None:
    if value is None or isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])
