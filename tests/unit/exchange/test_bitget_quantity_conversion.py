"""Regression tests for Bitget quantity/contract conversions."""

import pytest
from unittest.mock import Mock

from src.exchanges.bitget import BitgetExchange


@pytest.fixture
def exchange():
    ex = BitgetExchange("k", "s", "p", testnet=False)
    ex.client = Mock()
    ex.client.markets = {
        "PEPE/USDT:USDT": {
            "contractSize": 10_000_000,
            "limits": {"amount": {"min": 0.1}},
        }
    }
    ex.client.amount_to_precision = lambda _symbol, amount: f"{amount:.1f}"
    return ex


def test_to_contract_amount_from_base(exchange):
    # 2_000_000 PEPE with contractSize=10_000_000 -> 0.2 contracts
    contracts = exchange._to_contract_amount("PEPE/USDT:USDT", 2_000_000)
    assert contracts == pytest.approx(0.2)


def test_parse_position_converts_contracts_back_to_base(exchange):
    position = exchange._parse_position(
        {
            "symbol": "PEPE/USDT:USDT",
            "contracts": 0.2,
            "entryPrice": 0.00000347,
            "markPrice": 0.00000350,
            "side": "long",
            "leverage": 10,
        }
    )

    assert position.quantity == pytest.approx(2_000_000)
