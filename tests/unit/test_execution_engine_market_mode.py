"""Regression tests for ExecutionEngine market mode opening spread calculation."""

from __future__ import annotations

import pytest

from src.core.execution_engine import ExecutionEngine
from src.exchanges.enums import PositionSide
from src.exchanges.types import OrderBook


class DummyExchange:
    """Minimal stub sufficient for market_open pre-confirmation path."""

    def __init__(self, name: str, bid: float, ask: float):
        self._name = name
        self._bid = bid
        self._ask = ask

    def get_name(self) -> str:
        return self._name

    async def get_symbol_info(self, symbol: str) -> dict:
        _ = symbol
        return {
            "min_quantity": 0.001,
            "quantity_step": 0.001,
            "contract_size": 1,
        }

    async def get_orderbook(self, symbol: str) -> OrderBook:
        return OrderBook(
            symbol=symbol,
            exchange=self._name,
            bids=[(self._bid, 1000)],
            asks=[(self._ask, 1000)],
            timestamp=0.0,
        )


@pytest.mark.asyncio
async def test_market_open_no_type_error_and_can_cancel(monkeypatch):
    """Market mode should not crash on spread calc and should respect cancel."""
    ex1 = DummyExchange("okx", bid=100.0, ask=100.1)
    ex2 = DummyExchange("bitget", bid=100.2, ask=100.3)
    engine = ExecutionEngine(ex1, ex2)

    monkeypatch.setattr("builtins.input", lambda _prompt="": "n")

    result = await engine.market_open(
        symbol="BTCUSDT",
        side1=PositionSide.LONG,
        quantity=1.0,
        leverage=10,
        funding_rate_bps=1.0,
    )

    assert result is None
