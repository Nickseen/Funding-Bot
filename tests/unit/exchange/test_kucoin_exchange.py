"""Unit tests for KuCoinExchange adapter."""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import ccxt
import pytest

from src.exchanges.base import NetworkError, RateLimitError
from src.exchanges.enums import OrderType, PositionSide
from src.exchanges.kucoin import KuCoinExchange


class TestKuCoinExchange:
    """Test KuCoinExchange adapter behavior."""

    @pytest.fixture
    def kucoin_exchange(self):
        """Create KuCoinExchange with mocked ccxt client."""
        with patch("src.exchanges.kucoin.ccxt.kucoinfutures") as mock_ctor:
            mock_client = MagicMock()
            mock_client.id = "kucoinfutures"
            mock_client.password = "test_passphrase"
            mock_client.options = {"timeDifference": 0}
            mock_client.milliseconds.return_value = 1000
            mock_client.set_sandbox_mode = MagicMock()
            mock_client.fetch_time = AsyncMock(return_value=2000)
            mock_client.load_markets = AsyncMock(return_value={})
            mock_client.fetch_order_book = AsyncMock()
            mock_client.fetch_ticker = AsyncMock()
            mock_client.create_order = AsyncMock()
            mock_client.fetch_positions = AsyncMock(return_value=[])
            mock_client.cancel_order = AsyncMock(return_value={})
            mock_client.fetch_balance = AsyncMock(return_value={"USDT": {"total": 0, "free": 0, "used": 0}})
            mock_client.fetch_funding_rate = AsyncMock(return_value={"fundingRate": 0.0, "nextFundingTimestamp": 0})
            mock_client.set_leverage = AsyncMock(return_value={})
            mock_client.close = AsyncMock(return_value=None)
            mock_client.markets = {}
            mock_ctor.return_value = mock_client

            exchange = KuCoinExchange(
                api_key="test_key",
                secret_key="test_secret",
                passphrase="test_passphrase",
                testnet=True,
            )
            yield exchange

    def test_convert_symbol_simple(self, kucoin_exchange):
        assert kucoin_exchange._convert_symbol("BTCUSDT") == "BTC/USDT:USDT"
        assert kucoin_exchange._convert_symbol("ETHUSDT") == "ETH/USDT:USDT"

    def test_convert_symbol_native_kucoin(self, kucoin_exchange):
        assert kucoin_exchange._convert_symbol("XBTUSDTM") == "BTC/USDT:USDT"

    def test_convert_symbol_already_ccxt(self, kucoin_exchange):
        symbol = "BTC/USDT:USDT"
        assert kucoin_exchange._convert_symbol(symbol) == symbol

    def test_parse_position(self, kucoin_exchange):
        data = {
            "symbol": "BTC/USDT:USDT",
            "contracts": 1.0,
            "side": "long",
            "entryPrice": 50000.0,
            "markPrice": 51000.0,
            "leverage": 10,
            "id": "pos123",
            "liquidationPrice": 45000.0,
            "unrealizedPnl": 1000.0,
            "initialMargin": 120.0,
        }

        position = kucoin_exchange._parse_position(data)

        assert position.id.startswith("kucoin_BTC/USDT:USDT_")
        assert position.pair == "BTC/USDT:USDT"
        assert position.exchange1 == "kucoin"
        assert position.exchange1_side == "LONG"
        assert position.exchange1_entry_price == 50000.0
        assert position.exchange1_current_price == 51000.0
        assert position.exchange1_leverage == 10
        assert position.quantity == 1.0
        assert position.status == "OPEN"
        assert position.unrealized_pnl == 1000.0

    def test_parse_order(self, kucoin_exchange):
        data = {
            "id": "order123",
            "symbol": "BTC/USDT:USDT",
            "side": "buy",
            "type": "limit",
            "amount": 1.0,
            "price": 50000.0,
            "filled": 0.5,
            "status": "open",
        }

        order = kucoin_exchange._parse_order(data)

        assert order.id == "order123"
        assert order.symbol == "BTC/USDT:USDT"
        assert order.exchange == "kucoin"
        assert order.side == "BUY"
        assert order.order_type == "LIMIT"
        assert order.quantity == 1.0
        assert order.price == 50000.0
        assert order.filled_quantity == 0.5
        assert order.status == "OPEN"

    def test_parse_orderbook(self, kucoin_exchange):
        data = {
            "symbol": "BTC/USDT:USDT",
            "bids": [[50000.0, 1.0], [49900.0, 2.0]],
            "asks": [[50100.0, 1.5], [50200.0, 3.0]],
            "timestamp": 1640995200000,
        }

        orderbook = kucoin_exchange._parse_orderbook(data)

        assert orderbook.symbol == "BTC/USDT:USDT"
        assert orderbook.exchange == "kucoin"
        assert orderbook.bids == [(50000.0, 1.0), (49900.0, 2.0)]
        assert orderbook.asks == [(50100.0, 1.5), (50200.0, 3.0)]
        assert orderbook.timestamp == 1640995200000

    def test_parse_price_data(self, kucoin_exchange):
        data = {
            "symbol": "BTCUSDT",
            "bid": 50000.0,
            "ask": 50100.0,
            "bid_qty": 1.0,
            "ask_qty": 1.5,
            "timestamp": 1640995200000,
        }

        price_data = kucoin_exchange._parse_price_data(data)

        assert price_data.symbol == "BTCUSDT"
        assert price_data.bid == 50000.0
        assert price_data.ask == 50100.0
        assert price_data.bid_qty == 1.0
        assert price_data.ask_qty == 1.5
        assert price_data.timestamp == 1640995200.0

    def test_parse_balance(self, kucoin_exchange):
        data = {
            "USDT": {
                "total": 1000.0,
                "free": 800.0,
                "used": 150.0,
            }
        }

        balance = kucoin_exchange._parse_balance(data)

        assert balance.exchange == "kucoin"
        assert balance.total == 1000.0
        assert balance.available == 800.0
        assert balance.margin_used == 150.0

    def test_parse_funding_rate(self, kucoin_exchange):
        data = {
            "symbol": "BTCUSDT",
            "fundingRate": 0.0001,
            "nextFundingTime": 1640995200000,
        }

        funding = kucoin_exchange._parse_funding_rate(data)

        assert funding.symbol == "BTCUSDT"
        assert funding.exchange == "kucoin"
        assert funding.rate == 0.0001
        assert funding.rate_bps == 1.0
        assert funding.next_funding_time.year == 2022
        assert funding.next_funding_time.tzinfo == timezone.utc

    @pytest.mark.asyncio
    async def test_api_get_orderbook_rate_limit(self, kucoin_exchange):
        kucoin_exchange.client.fetch_order_book.side_effect = ccxt.RateLimitExceeded("Rate limit")
        with pytest.raises(RateLimitError):
            await kucoin_exchange._api_get_orderbook("BTCUSDT", 10)

    @pytest.mark.asyncio
    async def test_api_get_price_data_network_error(self, kucoin_exchange):
        kucoin_exchange.client.markets = {
            "BTC/USDT:USDT": {"contractSize": 0.001}
        }
        kucoin_exchange.client.fetch_ticker.side_effect = ccxt.NetworkError("Network error")
        with pytest.raises(NetworkError):
            await kucoin_exchange._api_get_price_data("BTCUSDT")

    def test_client_initialization(self, kucoin_exchange):
        assert kucoin_exchange.client.id == "kucoinfutures"
        assert kucoin_exchange.client.password == "test_passphrase"

    @pytest.mark.asyncio
    async def test_connect_time_sync(self, kucoin_exchange):
        kucoin_exchange.client.fetch_time.return_value = 1640995200000
        kucoin_exchange.client.milliseconds.return_value = 1640995199000

        await kucoin_exchange.connect()

        assert kucoin_exchange.client.options["timeDifference"] == 1000
        assert kucoin_exchange.connected is True

    @pytest.mark.asyncio
    async def test_stop_loss_setup(self, kucoin_exchange):
        kucoin_exchange.client.markets = {
            "BTC/USDT:USDT": {"contractSize": 0.001}
        }
        kucoin_exchange.client.create_order.return_value = {"id": "sl123", "type": "market", "side": "sell", "amount": 1}

        result = await kucoin_exchange._api_set_stop_loss("BTCUSDT", PositionSide.LONG, 49000.0, None)

        assert result["id"] == "sl123"
        call_kwargs = kucoin_exchange.client.create_order.call_args.kwargs
        assert call_kwargs["params"]["stopLossPrice"] == 49000.0
        assert call_kwargs["params"]["reduceOnly"] is True

    @pytest.mark.asyncio
    async def test_take_profit_setup(self, kucoin_exchange):
        kucoin_exchange.client.markets = {
            "BTC/USDT:USDT": {"contractSize": 0.001}
        }
        kucoin_exchange.client.create_order.return_value = {"id": "tp123", "type": "market", "side": "buy", "amount": 1}

        result = await kucoin_exchange._api_set_take_profit("BTCUSDT", PositionSide.SHORT, 51000.0, None)

        assert result["id"] == "tp123"
        call_kwargs = kucoin_exchange.client.create_order.call_args.kwargs
        assert call_kwargs["params"]["takeProfitPrice"] == 51000.0
        assert call_kwargs["params"]["reduceOnly"] is True

    def test_test_order_mode_fallback_when_sandbox_unavailable(self):
        with patch("src.exchanges.kucoin.ccxt.kucoinfutures") as mock_ctor:
            mock_client = MagicMock()
            mock_client.options = {"timeDifference": 0}
            mock_client.set_sandbox_mode.side_effect = Exception("sandbox unsupported")
            mock_ctor.return_value = mock_client

            exchange = KuCoinExchange(
                api_key="k",
                secret_key="s",
                passphrase="p",
                testnet=True,
            )

            assert exchange.sandbox_enabled is False
            assert exchange._is_test_order_mode() is True

    def test_api_test_mode_on_mainnet(self):
        with patch("src.exchanges.kucoin.ccxt.kucoinfutures") as mock_ctor:
            mock_client = MagicMock()
            mock_client.options = {"timeDifference": 0}
            mock_ctor.return_value = mock_client

            exchange = KuCoinExchange(
                api_key="k",
                secret_key="s",
                passphrase="p",
                testnet=False,
                api_test_mode=True,
            )

            assert exchange._is_test_order_mode() is True
            params = exchange._maybe_test_flag({"reduceOnly": True})
            assert params["test"] is True
