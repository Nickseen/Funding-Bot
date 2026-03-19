"""Unit tests for quantity alignment across exchanges in ExecutionEngine."""

import pytest

from src.core.execution_engine import ExecutionEngine
from src.exchanges.base import ExchangeError


class DummyExchange:
    """Minimal async exchange stub for symbol constraints tests."""

    def __init__(self, name: str, symbol_info: dict):
        self._name = name
        self._symbol_info = symbol_info

    def get_name(self) -> str:
        return self._name

    async def get_symbol_info(self, symbol: str) -> dict:
        return self._symbol_info


@pytest.mark.asyncio
async def test_align_quantity_uses_coarsest_exchange_step():
    ex1 = DummyExchange(
        "okx",
        {
            "min_quantity": 0.1,
            "quantity_step": 0.1,
            "contract_size": 1,
        },
    )
    ex2 = DummyExchange(
        "bitget",
        {
            "min_quantity": 0.001,
            "quantity_step": 0.001,
            "contract_size": 1,
        },
    )

    engine = ExecutionEngine(ex1, ex2)
    aligned = await engine._align_quantity_for_delta_neutrality("PEPEUSDT", 0.26)

    assert aligned == pytest.approx(0.2)


@pytest.mark.asyncio
async def test_align_quantity_respects_contract_size_in_base_units():
    ex1 = DummyExchange(
        "coarse",
        {
            "min_quantity": 0.1,
            "quantity_step": 0.1,
            "contract_size": 1000,
        },
    )
    ex2 = DummyExchange(
        "fine",
        {
            "min_quantity": 1,
            "quantity_step": 1,
            "contract_size": 1,
        },
    )

    engine = ExecutionEngine(ex1, ex2)
    aligned = await engine._align_quantity_for_delta_neutrality("TESTUSDT", 250)

    # coarse step in base units = 0.1 * 1000 = 100
    assert aligned == pytest.approx(200)


@pytest.mark.asyncio
async def test_align_quantity_does_not_use_min_as_step():
    ex1 = DummyExchange(
        "bingx",
        {
            "min_quantity": 20,
            "quantity_step": 1,
            "contract_size": 1,
        },
    )
    ex2 = DummyExchange(
        "okx",
        {
            "min_quantity": 1,
            "quantity_step": 0.1,
            "contract_size": 1,
        },
    )

    engine = ExecutionEngine(ex1, ex2)
    aligned = await engine._align_quantity_for_delta_neutrality("DOGEUSDT", 42.58)

    # Coarsest step should be 1.0, not min_quantity=20.
    assert aligned == pytest.approx(42.0)


@pytest.mark.asyncio
async def test_align_quantity_raises_if_requested_below_min_step():
    ex1 = DummyExchange(
        "okx",
        {
            "min_quantity": 0.1,
            "quantity_step": 0.1,
            "contract_size": 1,
        },
    )
    ex2 = DummyExchange(
        "bitget",
        {
            "min_quantity": 0.001,
            "quantity_step": 0.001,
            "contract_size": 1,
        },
    )

    engine = ExecutionEngine(ex1, ex2)

    with pytest.raises(ExchangeError):
        await engine._align_quantity_for_delta_neutrality("PEPEUSDT", 0.05)


@pytest.mark.asyncio
async def test_align_quantity_raises_if_below_exchange_minimum():
    ex1 = DummyExchange(
        "bingx",
        {
            "min_quantity": 20,
            "quantity_step": 1,
            "contract_size": 1,
        },
    )
    ex2 = DummyExchange(
        "okx",
        {
            "min_quantity": 1,
            "quantity_step": 0.1,
            "contract_size": 1,
        },
    )

    engine = ExecutionEngine(ex1, ex2)

    with pytest.raises(ExchangeError):
        await engine._align_quantity_for_delta_neutrality("DOGEUSDT", 19.0)


def test_normalize_step_supports_decimal_places_precision():
    ex = DummyExchange("x", {"min_quantity": 1})
    engine = ExecutionEngine(ex, ex)

    assert engine._normalize_step_value(3) == pytest.approx(0.001)
    assert engine._normalize_step_value(0.1) == pytest.approx(0.1)
