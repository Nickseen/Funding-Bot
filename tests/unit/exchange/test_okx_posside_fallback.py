"""Regression tests for OKX posSide fallback in open/close position."""

import pytest
from unittest.mock import AsyncMock, Mock

from src.exchanges.okx import OKXExchange
from src.exchanges.enums import PositionSide, OrderType


@pytest.fixture
def exchange():
    ex = OKXExchange("k", "s", "p", testnet=False)
    ex.client = Mock()
    ex.client.markets = {
        "DOGE-USDT-SWAP": {
            "contractSize": 1,
            "limits": {"amount": {"min": 1}},
        }
    }
    ex.client.amount_to_precision = lambda _symbol, amount: str(int(amount))
    ex.client.set_leverage = AsyncMock()
    ex.client.create_order = AsyncMock()
    ex.client.fetch_positions = AsyncMock(
        return_value=[
            {
                "symbol": "DOGE-USDT-SWAP",
                "contracts": 19,
                "side": "long",
                "entryPrice": 0.1,
                "markPrice": 0.1,
                "leverage": 1,
            }
        ]
    )
    return ex


@pytest.mark.asyncio
async def test_open_position_retries_with_posside_long_on_51000(exchange):
    exchange.client.set_leverage.side_effect = [
        Exception('okx {"code":"51000","msg":"Parameter posSide error"}'),
        None,
    ]
    exchange.client.create_order.side_effect = [
        Exception('okx {"code":"51000","msg":"Parameter posSide error"}'),
        {"id": "ord_1"},
    ]

    await exchange._api_open_position(
        symbol="DOGEUSDT",
        side=PositionSide.LONG,
        quantity=19,
        leverage=1,
        order_type=OrderType.LIMIT,
        price=0.1,
    )

    # set_leverage: first without posSide, second with posSide=long
    second_set_lev_params = exchange.client.set_leverage.call_args_list[1].kwargs["params"]
    assert second_set_lev_params.get("posSide") == "long"

    # create_order: first without posSide, second with posSide=long
    second_order_params = exchange.client.create_order.call_args_list[1].kwargs["params"]
    assert second_order_params.get("posSide") == "long"


@pytest.mark.asyncio
async def test_open_position_falls_back_to_posside_net(exchange):
    exchange.client.set_leverage.side_effect = [
        Exception('okx {"code":"51000","msg":"Parameter posSide error"}'),
        Exception('okx {"code":"51000","msg":"Parameter posSide error"}'),
        None,
    ]
    exchange.client.create_order.side_effect = [
        Exception('okx {"code":"51000","msg":"Parameter posSide error"}'),
        Exception('okx {"code":"51000","msg":"Parameter posSide error"}'),
        {"id": "ord_2"},
    ]

    await exchange._api_open_position(
        symbol="DOGEUSDT",
        side=PositionSide.LONG,
        quantity=19,
        leverage=1,
        order_type=OrderType.MARKET,
        price=None,
    )

    # Third create_order attempt should use posSide=net
    third_order_params = exchange.client.create_order.call_args_list[2].kwargs["params"]
    assert third_order_params.get("posSide") == "net"


@pytest.mark.asyncio
async def test_close_position_retries_with_posside_long_on_51000(exchange):
    exchange.client.fetch_positions = AsyncMock(
        side_effect=[
            [
                {
                    "symbol": "DOGE/USDT:USDT",
                    "contracts": 38,
                    "side": "long",
                    "info": {},
                }
            ],
            [],
        ]
    )
    exchange.client.create_order.side_effect = [
        Exception('okx {"code":"51000","msg":"Parameter posSide error"}'),
        {"id": "cls_1"},
    ]

    await exchange._api_close_position(
        symbol="DOGEUSDT",
        order_type=OrderType.MARKET,
        price=None,
    )

    second_order_params = exchange.client.create_order.call_args_list[1].kwargs["params"]
    assert second_order_params.get("posSide") == "long"


@pytest.mark.asyncio
async def test_close_position_falls_back_to_posside_net(exchange):
    exchange.client.fetch_positions = AsyncMock(
        side_effect=[
            [
                {
                    "symbol": "DOGE/USDT:USDT",
                    "contracts": 38,
                    "side": "long",
                    "info": {},
                }
            ],
            [],
        ]
    )
    exchange.client.create_order.side_effect = [
        Exception('okx {"code":"51000","msg":"Parameter posSide error"}'),
        Exception('okx {"code":"51000","msg":"Parameter posSide error"}'),
        {"id": "cls_2"},
    ]

    await exchange._api_close_position(
        symbol="DOGEUSDT",
        order_type=OrderType.MARKET,
        price=None,
    )

    third_order_params = exchange.client.create_order.call_args_list[2].kwargs["params"]
    assert third_order_params.get("posSide") == "net"
