"""Testes de ``analytics/valuation_multiples`` (fase2_plan.md 4, 5).

Cobre a montagem do market cap agregado e o rigor point-in-time (as queries
filtram ``trade_date``/``available_from`` <= as_of).
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from stock_research.analytics import valuation_multiples as vm


class _Fakes:
    """Substitui os helpers de I/O por dados em memória."""

    def __init__(
        self,
        *,
        financial=False,
        prices=None,
        shares=None,
        metrics=None,
        divs=None,
        instruments=None,
    ):
        self.financial = financial
        self.prices = prices or {}
        self.shares = shares or {}
        self.metrics = metrics or {}
        self.divs = divs or {}
        self.instruments = instruments or [
            {"instrument_id": 3, "ticker": "PETR3", "share_class": "ON"},
            {"instrument_id": 2, "ticker": "PETR4", "share_class": "PN"},
        ]

    def install(self, monkeypatch):
        monkeypatch.setattr(vm, "_company_instruments", lambda cid: self.instruments)
        monkeypatch.setattr(vm, "_price_as_of", lambda iid, as_of: self.prices.get(iid))
        monkeypatch.setattr(
            vm, "_shares_issued_as_of", lambda cid, cls, as_of: self.shares.get(cls)
        )
        monkeypatch.setattr(
            vm, "_metric_as_of", lambda iid, name, pt, cv, as_of: self.metrics.get(name)
        )
        monkeypatch.setattr(
            vm, "_dividends_ttm_per_share", lambda iid, as_of: self.divs.get(iid, Decimal(0))
        )

        def fake_fetch_one(query, params=None):
            if "from public.companies" in query and "financial_company" in query:
                return {"company_id": 1, "cnpj": "x", "financial_company": self.financial}
            if "active = true order by instrument_id" in query:
                return {"instrument_id": 2}
            return None

        monkeypatch.setattr(vm, "fetch_one", fake_fetch_one)


def _metric(value, ref="2024-12-31"):
    return {
        "metric_value": Decimal(str(value)),
        "reference_date": date.fromisoformat(ref),
        "available_from": None,
    }


def test_market_cap_soma_as_classes(monkeypatch):
    _Fakes(
        prices={
            3: {"close": 40.0, "trade_date": date(2026, 8, 20)},
            2: {"close": 38.0, "trade_date": date(2026, 8, 20)},
        },
        shares={"ON": Decimal(7_000_000_000), "PN": Decimal(5_000_000_000)},
        metrics={
            "net_income": _metric(30_000_000_000),
            "ebitda": _metric(200_000_000_000),
            "free_cash_flow": _metric(60_000_000_000),
            "equity": _metric(380_000_000_000),
            "net_debt": _metric(300_000_000_000),
        },
        divs={3: Decimal("2.0"), 2: Decimal("2.5")},
    ).install(monkeypatch)

    r = vm.compute_multiples(1, date(2026, 8, 27))
    # 40*7bi + 38*5bi = 280bi + 190bi = 470bi
    assert r["market_cap"] == Decimal(470_000_000_000)
    assert r["enterprise_value"] == Decimal(470_000_000_000 + 300_000_000_000)
    assert r["price_earnings"] == Decimal(470_000_000_000) / Decimal(30_000_000_000)
    assert r["ev_ebitda"] == Decimal(770_000_000_000) / Decimal(200_000_000_000)
    assert r["price_book"] == Decimal(470_000_000_000) / Decimal(380_000_000_000)
    # dividends_ttm = 2.0*7bi + 2.5*5bi = 14bi + 12.5bi = 26.5bi
    assert r["dividends_ttm"] == Decimal("26500000000.0")
    assert r["quality_flag"] == "ok"


def test_lucro_negativo_zera_pe_mas_mantem_earnings_yield(monkeypatch):
    _Fakes(
        prices={
            3: {"close": 10.0, "trade_date": date(2026, 8, 20)},
            2: {"close": 10.0, "trade_date": date(2026, 8, 20)},
        },
        shares={"ON": Decimal(1_000_000_000), "PN": Decimal(1_000_000_000)},
        metrics={"net_income": _metric(-5_000_000_000), "equity": _metric(50_000_000_000)},
        divs={},
    ).install(monkeypatch)
    r = vm.compute_multiples(1, date(2026, 8, 27))
    assert r["price_earnings"] is None
    assert r["earnings_yield"] == Decimal(-5_000_000_000) / Decimal(20_000_000_000)


def test_classe_sem_preco_fica_incompleta(monkeypatch):
    _Fakes(
        prices={2: {"close": 38.0, "trade_date": date(2026, 8, 20)}},  # PETR3 sem preço
        shares={"ON": Decimal(7_000_000_000), "PN": Decimal(5_000_000_000)},
        metrics={"net_income": _metric(30_000_000_000), "equity": _metric(380_000_000_000)},
    ).install(monkeypatch)
    r = vm.compute_multiples(1, date(2026, 8, 27))
    assert r["market_cap"] == Decimal(38 * 5_000_000_000)  # só a perna PN
    assert r["quality_flag"] == "incomplete"
    assert "PETR3" in r["quality_reason"]


def test_banco_sem_fcf_yield_nem_ev_ebitda(monkeypatch):
    _Fakes(
        financial=True,
        instruments=[
            {"instrument_id": 5, "ticker": "ITUB3", "share_class": "ON"},
            {"instrument_id": 4, "ticker": "ITUB4", "share_class": "PN"},
        ],
        prices={
            5: {"close": 40.0, "trade_date": date(2026, 8, 20)},
            4: {"close": 40.0, "trade_date": date(2026, 8, 20)},
        },
        shares={"ON": Decimal(5_000_000_000), "PN": Decimal(5_000_000_000)},
        metrics={"net_income": _metric(40_000_000_000), "equity": _metric(220_000_000_000)},
    ).install(monkeypatch)
    r = vm.compute_multiples(1, date(2026, 8, 27))
    assert r["fcf_yield"] is None
    assert r["ev_ebitda"] is None
    assert r["price_earnings"] is not None  # P/L faz sentido p/ banco
    assert r["price_book"] is not None


class TestPointInTimeQueries:
    """As queries têm que filtrar por data -- a garantia de não-vazamento
    de futuro do §5 mora no SQL."""

    @staticmethod
    def _capture(monkeypatch):
        seen: dict[str, object] = {}

        def fake(query, params=None):
            seen["query"] = query
            seen["params"] = params
            return None

        monkeypatch.setattr(vm, "fetch_one", fake)
        return seen

    def test_price_query_filtra_trade_date(self, monkeypatch):
        seen = self._capture(monkeypatch)
        vm._price_as_of(1, date(2026, 1, 1))
        assert "trade_date <= %s" in seen["query"]

    def test_shares_query_filtra_available_from(self, monkeypatch):
        seen = self._capture(monkeypatch)
        vm._shares_issued_as_of(1, "ON", date(2026, 1, 1))
        assert "available_from <= %s" in seen["query"]

    def test_metric_query_filtra_available_from(self, monkeypatch):
        seen = self._capture(monkeypatch)
        vm._metric_as_of(1, "net_income", "annual", "fundamental_metrics_v1", date(2026, 1, 1))
        assert "available_from <= %s" in seen["query"]

    def test_dividends_query_janela_de_365_dias(self, monkeypatch):
        seen = self._capture(monkeypatch)
        vm._dividends_ttm_per_share(1, date(2026, 8, 27))
        assert "action_date > %s" in seen["query"] and "action_date <= %s" in seen["query"]
        assert date(2026, 8, 27) - timedelta(days=365) in seen["params"]
