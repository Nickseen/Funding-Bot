"""Regression tests for OKX-like quantity step parsing in ExecutionEngine."""

import pytest

from src.core.execution_engine import ExecutionEngine


class DummyExchange:
    def __init__(self, name: str, symbol_info: dict):
        self._name = name
        self._symbol_info = symbol_info

    def get_name(self) -> str:
        return self._name

    async def get_symbol_info(self, symbol: str) -> dict:
        return self._symbol_info


@pytest.mark.asyncio
async def test_okx_precision_one_with_fractional_min_is_treated_as_tenth_step():
    # Simulates adapter returning precision-like quantity_step=1 with min_quantity=0.1 contract
    # and large contract_size in base units (e.g. PEPE contracts).
    okx_like = DummyExchange(
        "okx",
        {
            "min_quantity": 0.1,
            "quantity_step": 1,
            "contract_size": 10_000_000,
        },
    )
    fine = DummyExchange(
        "bitget",
        {
            "min_quantity": 0.001,
            "quantity_step": 0.001,
            "contract_size": 1,
        },
    )

    engine = ExecutionEngine(okx_like, fine)

    # 0.1 contract * 10_000_000 = 1_000_000 base units step
    step = await engine._get_exchange_base_qty_step(okx_like, "PEPEUSDT")
    assert step == pytest.approx(1_000_000)
