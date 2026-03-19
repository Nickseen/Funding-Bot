"""Regression tests for strict leverage handling on Bitget."""

import pytest
from unittest.mock import AsyncMock, Mock

from src.exchanges.bitget import BitgetExchange
from src.exchanges.base import ExchangeError


@pytest.fixture
def exchange():
    ex = BitgetExchange("k", "s", "p", testnet=False)
    ex.client = Mock()
    ex.client.set_leverage = AsyncMock()
    return ex


@pytest.mark.asyncio
async def test_api_set_leverage_allows_idempotent_error(exchange):
    exchange.client.set_leverage.side_effect = Exception("already same leverage")

    result = await exchange._api_set_leverage("DOGEUSDT", 1)
    assert result is True


@pytest.mark.asyncio
async def test_api_set_leverage_raises_non_idempotent_error(exchange):
    exchange.client.set_leverage.side_effect = Exception("insufficient permission")

    with pytest.raises(ExchangeError):
        await exchange._api_set_leverage("DOGEUSDT", 1)
