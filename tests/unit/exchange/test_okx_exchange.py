"""
Unit tests for OKXExchange adapter
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime, timezone
import ccxt

from src.exchanges.okx import OKXExchange
from src.exchanges.base import ExchangeError, RateLimitError, NetworkError
from src.exchanges.enums import Exchange, PositionSide, OrderSide, OrderType
from src.exchanges.types import Position, Order, OrderBook, PriceData, Balance, FundingRate


class TestOKXExchange:
    """Test OKXExchange adapter"""

    @pytest.fixture
    def okx_exchange(self):
        """Create OKXExchange instance"""
        return OKXExchange(
            api_key="test_key",
            secret_key="test_secret",
            passphrase="test_passphrase",
            testnet=True
        )

    # 1. Symbol Conversion Tests
    def test_convert_symbol_simple(self, okx_exchange):
        """Test _convert_symbol with simple format"""
        assert okx_exchange._convert_symbol("BTCUSDT") == "BTC-USDT-SWAP"
        assert okx_exchange._convert_symbol("ETHUSDT") == "ETH-USDT-SWAP"

    def test_convert_symbol_already_converted(self, okx_exchange):
        """Test _convert_symbol with already converted format"""
        symbol = "BTC-USDT-SWAP"
        assert okx_exchange._convert_symbol(symbol) == symbol

    def test_convert_symbol_fallback(self, okx_exchange):
        """Test _convert_symbol fallback"""
        assert okx_exchange._convert_symbol("UNKNOWN") == "UNKNOWN"

    # 2. Parser Tests
    def test_parse_position(self, okx_exchange):
        """Test _parse_position"""
        data = {
            'instId': 'BTC-USDT-SWAP',
            'pos': 1.0,
            'posSide': 'long',
            'avgPx': 50000.0,
            'markPx': 51000.0,
            'lever': 10,
            'posId': 'pos123',
            'liqPx': 45000.0,
            'upl': 1000.0
        }

        position = okx_exchange._parse_position(data)

        assert position.id.startswith("okx_BTC-USDT-SWAP_")
        assert position.pair == 'BTC-USDT-SWAP'
        assert position.exchange1 == 'okx'
        assert position.exchange1_side == 'LONG'
        assert position.exchange1_entry_price == 50000.0
        assert position.exchange1_current_price == 51000.0
        assert position.exchange1_leverage == 10
        assert position.quantity == 1.0
        assert position.status == 'OPEN'
        assert position.unrealized_pnl == 1000.0

    def test_parse_position_none_values(self, okx_exchange):
        """Test _parse_position with None values"""
        data = {
            'instId': 'BTC-USDT-SWAP',
            'pos': None,
            'posSide': None,
            'avgPx': None,
            'markPx': None,
            'lever': None,
        }

        position = okx_exchange._parse_position(data)

        assert position.exchange1_entry_price == 0
        assert position.exchange1_current_price == 0
        assert position.exchange1_leverage == 1
        assert position.quantity == 0

    def test_parse_order(self, okx_exchange):
        """Test _parse_order"""
        data = {
            'ordId': 'order123',
            'instId': 'BTC-USDT-SWAP',
            'side': 'buy',
            'ordType': 'limit',
            'sz': 1.0,
            'px': 50000.0,
            'fillSz': 0.5,
            'state': 'filled'
        }

        order = okx_exchange._parse_order(data)

        assert order.id == 'order123'
        assert order.symbol == 'BTC-USDT-SWAP'
        assert order.exchange == 'okx'
        assert order.side == 'BUY'
        assert order.order_type == 'LIMIT'
        assert order.quantity == 1.0
        assert order.price == 50000.0
        assert order.filled_quantity == 0.5
        assert order.status == 'FILLED'

    def test_parse_orderbook(self, okx_exchange):
        """Test _parse_orderbook"""
        data = {
            'instId': 'BTC-USDT-SWAP',
            'bids': [[50000.0, 1.0], [49900.0, 2.0]],
            'asks': [[50100.0, 1.5], [50200.0, 3.0]],
            'ts': 1640995200000
        }

        orderbook = okx_exchange._parse_orderbook(data)

        assert orderbook.symbol == 'BTC-USDT-SWAP'
        assert orderbook.exchange == 'okx'
        assert orderbook.bids == [(50000.0, 1.0), (49900.0, 2.0)]
        assert orderbook.asks == [(50100.0, 1.5), (50200.0, 3.0)]
        assert orderbook.timestamp == 1640995200000

    def test_parse_price_data(self, okx_exchange):
        """Test _parse_price_data"""
        data = {
            'instId': 'BTC-USDT-SWAP',
            'bidPx': 50000.0,
            'askPx': 50100.0,
            'bidSz': 1.0,
            'askSz': 1.5,
            'ts': 1640995200000
        }

        price_data = okx_exchange._parse_price_data(data)

        assert price_data.symbol == 'BTC-USDT-SWAP'
        assert price_data.bid == 50000.0
        assert price_data.ask == 50100.0
        assert price_data.bid_qty == 1.0
        assert price_data.ask_qty == 1.5
        assert price_data.timestamp == 1640995200.0

    def test_parse_balance(self, okx_exchange):
        """Test _parse_balance"""
        data = {
            'ccy': 'USDT',
            'eq': 1000.0,
            'availEq': 800.0,
            'mgnRatio': '0.15',
            'upl': 50.0
        }

        balance = okx_exchange._parse_balance(data)

        assert balance.exchange == 'okx'
        assert balance.total == 1000.0
        assert balance.available == 800.0
        assert balance.margin_used == 150.0  # 15% of total

    def test_parse_funding_rate(self, okx_exchange):
        """Test _parse_funding_rate"""
        data = {
            'instId': 'BTC-USDT-SWAP',
            'fundingRate': 0.0001,
            'nextFundingTime': 1640995200000
        }

        funding = okx_exchange._parse_funding_rate(data)

        assert funding.symbol == 'BTC-USDT-SWAP'
        assert funding.exchange == 'okx'
        assert funding.rate == 0.0001
        assert funding.rate_bps == 1.0
        assert funding.next_funding_time.year == 2022

    # 3. CCXT Error Handling Tests
    @pytest.mark.asyncio
    async def test_api_get_orderbook_rate_limit(self, okx_exchange):
        """Test RateLimitError handling"""
        with patch.object(okx_exchange.client, 'fetch_order_book', side_effect=ccxt.RateLimitExceeded("Rate limit")):
            with pytest.raises(RateLimitError):
                await okx_exchange._api_get_orderbook("BTCUSDT", 10)

    @pytest.mark.asyncio
    async def test_api_get_price_data_network_error(self, okx_exchange):
        """Test NetworkError handling"""
        with patch.object(okx_exchange.client, 'fetch_ticker', side_effect=ccxt.NetworkError("Network error")):
            with pytest.raises(NetworkError):
                await okx_exchange._api_get_price_data("BTCUSDT")

    # 4. Client Initialization Tests
    def test_client_initialization(self, okx_exchange):
        """Test ccxt client initialization"""
        assert okx_exchange.client.id == 'okx'
        assert okx_exchange.client.password == 'test_passphrase'
        assert okx_exchange.client.options['defaultType'] == 'swap'

    # 5. Time Synchronization Tests
    @pytest.mark.asyncio
    async def test_connect_time_sync(self, okx_exchange):
        """Test time synchronization in connect"""
        with patch.object(okx_exchange.client, 'fetch_time', return_value=1640995200000):
            await okx_exchange.connect()
            assert okx_exchange.client.options['timeDifference'] is not None

    # Commission and SL/TP Tests
    def test_commission_calculation(self, okx_exchange):
        """Test taker commission calculation"""
        # OKX taker commission is 10 bps
        taker_fee_bps = 10.0
        spread_bps = 15.0

        net_spread = spread_bps - taker_fee_bps
        assert net_spread == 5.0

    @pytest.mark.asyncio
    async def test_stop_loss_setup(self, okx_exchange):
        """Test stop loss setup"""
        with patch.object(okx_exchange.client, 'fetch_positions') as mock_positions:
            mock_positions.return_value = [{
                'pos': 1.0,
                'posSide': 'long',
                'instId': 'BTC-USDT-SWAP'
            }]

            with patch.object(okx_exchange.client, 'create_order') as mock_order:
                mock_order.return_value = {'ordId': 'sl123'}

                result = await okx_exchange._api_set_stop_loss("BTCUSDT", PositionSide.LONG, 49000.0)

                assert result['order_id'] == 'sl123'
                assert result['type'] == 'stop_loss'

    @pytest.mark.asyncio
    async def test_take_profit_setup(self, okx_exchange):
        """Test take profit setup"""
        with patch.object(okx_exchange.client, 'fetch_positions') as mock_positions:
            mock_positions.return_value = [{
                'pos': 1.0,
                'posSide': 'short',
                'instId': 'BTC-USDT-SWAP'
            }]

            with patch.object(okx_exchange.client, 'create_order') as mock_order:
                mock_order.return_value = {'ordId': 'tp123'}

                result = await okx_exchange._api_set_take_profit("BTCUSDT", PositionSide.SHORT, 51000.0)

                assert result['order_id'] == 'tp123'
                assert result['type'] == 'take_profit'

    @pytest.mark.asyncio
    async def test_api_get_symbol_info_uses_symbol_specific_max_leverage(self, okx_exchange):
        ccxt_symbol = "DOGE/USDT:USDT"
        okx_exchange.client.markets = {
            ccxt_symbol: {
                "limits": {
                    "amount": {"min": 1, "max": 1000000},
                    "price": {"min": 0.0001},
                    "leverage": {"max": 50},
                },
                "precision": {"amount": 1, "price": 0.0001},
                "contractSize": 1,
                "info": {"maxLeverage": 125},
            }
        }

        symbol_info = await okx_exchange._api_get_symbol_info("DOGEUSDT")
        assert symbol_info["max_leverage"] == 50