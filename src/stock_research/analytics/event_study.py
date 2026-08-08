"""Matematica do event study (fase1.md 53-61). Funcoes puras: recebem series
ja carregadas do banco, nunca fazem I/O -- e a parte do projeto onde um erro
silencioso e mais caro, entao cada peca e testada isoladamente.

Principio central (fase1.md 53): o sistema nunca afirma causalidade.
"A noticia foi seguida por queda de 8%", nunca "a noticia causou queda de
8%". O que estas funcoes calculam e retorno associado, nao efeito causal.

Pecas:

    compute_return                 -- retorno simples P_t/P_0 - 1
    returns_at_horizons             -- retorno em cada D+/-N (pregoes, nao dias corridos)
    estimate_market_model          -- alpha/beta via OLS na janela de estimacao
    abnormal_returns_at_horizons   -- retorno anormal e CAR por horizonte
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol


class TradingIndexLookup(Protocol):
    """Ponte pro trading_calendar real: dado um pregao, seu indice
    sequencial (fase1.md 54: D+N conta pregoes, nao dias corridos)."""

    def index_of(self, d: date) -> int | None: ...
    def date_at(self, index: int) -> date | None: ...


def compute_return(base_price: Decimal | float | None, target_price: Decimal | float | None) -> float | None:
    """Retorno simples ``P_target / P_base - 1``. ``None`` se faltar
    qualquer um dos dois lados ou se ``base_price`` for zero (nunca divide
    por zero silenciosamente transformado em outro numero)."""
    if base_price is None or target_price is None:
        return None
    base = float(base_price)
    if base == 0.0:
        return None
    return float(target_price) / base - 1.0


@dataclass(frozen=True)
class HorizonReturn:
    horizon_days: int
    end_trade_date: date | None
    return_actual: float | None
    is_censored: bool


def returns_at_horizons(
    *,
    reference_date: date,
    horizons: list[int],
    prices: Mapping[date, Decimal | float],
    calendar: TradingIndexLookup,
) -> list[HorizonReturn]:
    """``prices``: fechamento (ajustado ou nao -- quem chama decide qual
    serie) indexado por data de pregao. Um horizonte sem pregao
    correspondente na janela conhecida (evento recente demais pra ter
    D+252, por exemplo) volta ``is_censored=True`` e ``return_actual=None``
    -- nunca um numero extrapolado (fase1.md 54).

    Direcao do retorno para horizonte NEGATIVO (janela pre-evento, fase1.md
    59): ``return_pre_N`` mede o movimento de D-N ATE D0, no sentido do
    tempo -- nao o inverso. O exemplo literal do fase1.md 59 ("acao caiu 18%
    nos 20 pregoes anteriores") so faz sentido como ``P(D0)/P(D-20) - 1``;
    calcular ``P(D-20)/P(D0) - 1`` inverteria o sinal e trocaria "caiu" por
    "subiu". Por isso o par (base, target) passado a ``compute_return`` se
    inverte quando ``h < 0``: a data mais ANTIGA das duas sempre entra como
    base.
    """
    base_index = calendar.index_of(reference_date)
    base_price = prices.get(reference_date)
    if base_index is None:
        return [
            HorizonReturn(horizon_days=h, end_trade_date=None, return_actual=None, is_censored=True)
            for h in horizons
        ]

    results = []
    for h in horizons:
        target_date = calendar.date_at(base_index + h)
        if target_date is None:
            results.append(HorizonReturn(horizon_days=h, end_trade_date=None, return_actual=None, is_censored=True))
            continue
        target_price = prices.get(target_date)
        if h < 0:
            ret = compute_return(target_price, base_price)  # de D+h (mais antigo) ate D0
        else:
            ret = compute_return(base_price, target_price)  # de D0 ate D+h
        results.append(
            HorizonReturn(
                horizon_days=h, end_trade_date=target_date, return_actual=ret,
                is_censored=ret is None,
            )
        )
    return results


@dataclass(frozen=True)
class MarketModel:
    alpha: float | None
    beta: float | None
    r_squared: float | None
    residual_std: float | None
    observations: int
    low_sample: bool


def estimate_market_model(
    stock_returns: list[float], market_returns: list[float], *, min_observations: int = 60
) -> MarketModel:
    """OLS de um fator: ``R_stock = alpha + beta * R_market + epsilon``
    (fase1.md 57). Pares com qualquer lado ``NaN``/ausente ja devem ter sido
    filtrados por quem chama -- esta funcao assume as duas listas paralelas
    e do mesmo tamanho.

    ``low_sample=True`` quando ``observations < min_observations``: alpha/beta
    ainda sao calculados (podem ser uteis), mas marcados como estatisticamente
    fracos -- nunca escondidos, so sinalizados (fase1.md 57: "flag de baixa
    amostra").
    """
    n = len(stock_returns)
    if n < 2 or len(market_returns) != n:
        return MarketModel(alpha=None, beta=None, r_squared=None, residual_std=None, observations=n, low_sample=True)

    mean_market = sum(market_returns) / n
    mean_stock = sum(stock_returns) / n

    cov = sum((market_returns[i] - mean_market) * (stock_returns[i] - mean_stock) for i in range(n))
    var_market = sum((m - mean_market) ** 2 for m in market_returns)

    if var_market == 0.0:
        # Mercado sem variancia na janela (praticamente impossivel na pratica,
        # mas guarda contra divisao por zero de forma explicita).
        return MarketModel(alpha=None, beta=None, r_squared=None, residual_std=None, observations=n, low_sample=True)

    beta = cov / var_market
    alpha = mean_stock - beta * mean_market

    predicted = [alpha + beta * m for m in market_returns]
    residuals = [stock_returns[i] - predicted[i] for i in range(n)]
    ss_res = sum(r**2 for r in residuals)
    ss_tot = sum((s - mean_stock) ** 2 for s in stock_returns)
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else None
    residual_std = (ss_res / (n - 2)) ** 0.5 if n > 2 else None

    return MarketModel(
        alpha=alpha, beta=beta, r_squared=r_squared, residual_std=residual_std,
        observations=n, low_sample=n < min_observations,
    )


@dataclass(frozen=True)
class AbnormalHorizonReturn:
    horizon_days: int
    return_actual: float | None
    benchmark_return: float | None
    excess_return: float | None
    expected_return: float | None
    abnormal_return: float | None
    car: float | None
    is_censored: bool


def abnormal_returns_at_horizons(
    stock_horizons: list[HorizonReturn],
    benchmark_horizons: list[HorizonReturn],
    model: MarketModel,
) -> list[AbnormalHorizonReturn]:
    """Combina retorno absoluto, excesso simples (fase1.md 56) e retorno
    anormal via market model (fase1.md 57) por horizonte. CAR e a soma
    cumulativa dos abnormal returns dos horizontes POSITIVOS, na ordem
    (fase1.md 57) -- horizontes pre-evento (negativos) tem CAR None: CAR
    descreve acumulo APOS o evento, acumular "antes" nao tem o mesmo
    significado economico.
    """
    by_horizon_bench = {b.horizon_days: b for b in benchmark_horizons}
    out = []
    running_car: float | None = 0.0

    for stock_h in sorted(stock_horizons, key=lambda h: h.horizon_days):
        bench_h = by_horizon_bench.get(stock_h.horizon_days)
        benchmark_return = bench_h.return_actual if bench_h else None
        excess = (
            stock_h.return_actual - benchmark_return
            if stock_h.return_actual is not None and benchmark_return is not None
            else None
        )

        expected = None
        abnormal = None
        if model.alpha is not None and model.beta is not None and benchmark_return is not None:
            expected = model.alpha + model.beta * benchmark_return
            if stock_h.return_actual is not None:
                abnormal = stock_h.return_actual - expected

        car = None
        if stock_h.horizon_days > 0:
            if abnormal is not None and running_car is not None:
                running_car += abnormal
                car = running_car
            else:
                # Um horizonte censurado quebra a cadeia acumulada -- CAR dos
                # horizontes seguintes tambem fica None, nunca soma parcial
                # apresentada como se fosse completa.
                running_car = None

        out.append(
            AbnormalHorizonReturn(
                horizon_days=stock_h.horizon_days,
                return_actual=stock_h.return_actual,
                benchmark_return=benchmark_return,
                excess_return=excess,
                expected_return=expected,
                abnormal_return=abnormal,
                car=car,
                is_censored=stock_h.is_censored,
            )
        )
    return out
