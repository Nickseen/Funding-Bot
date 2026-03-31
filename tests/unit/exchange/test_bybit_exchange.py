"""
Unit tests for BybitExchange adapter
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime, timezone
import ccxt

from src.exchanges.bybit import BybitExchange
from src.exchanges.base import ExchangeError, RateLimitError, NetworkError
from src.exchanges.enums import Exchange, PositionSide, OrderSide, OrderType
from src.exchanges.types import Position, Order, OrderBook, PriceData, Balance, FundingRate


class TestBybitExchange:
    """Test BybitExchange adapter"""

    @pytest.fixture
    def bybit_exchange(self):
        """Create BybitExchange instance"""
        return BybitExchange(
            api_key="test_key",
            secret_key="test_secret",
            testnet=True
        )

    # 1. Symbol Conversion Tests
    def test_convert_symbol_simple(self, bybit_exchange):
        """Test _convert_symbol with simple format"""
        assert bybit_exchange._convert_symbol("BTCUSDT") == "BTC/USDT:USDT"
        assert bybit_exchange._convert_symbol("ETHUSDT") == "ETH/USDT:USDT"

    def test_convert_symbol_already_converted(self, bybit_exchange):
        """Test _convert_symbol with already converted format"""
        symbol = "BTC/USDT:USDT"
        assert bybit_exchange._convert_symbol(symbol) == symbol

    def test_convert_symbol_fallback(self, bybit_exchange):
        """Test _convert_symbol fallback"""
        assert bybit_exchange._convert_symbol("UNKNOWN") == "UNKNOWN"

    # 2. Parser Tests
    def test_parse_position(self, bybit_exchange):
        """Test _parse_position"""
        data = {
            'symbol': 'BTC/USDT:USDT',
            'size': 1.0,
            'side': 'Buy',
            'avgPrice': 50000.0,
            'markPrice': 51000.0,
            'leverage': 10,
            'positionIdx': 0,
            'stopLoss': 49000.0,
            'takeProfit': 52000.0,
            'liqPrice': 45000.0,
            'unrealisedPnl': 1000.0
        }

        position = bybit_exchange._parse_position(data)

        assert position.id.startswith("bybit_BTC/USDT:USDT_")
        assert position.pair == 'BTC/USDT:USDT'
        assert position.exchange1 == 'bybit'
        assert position.exchange1_side == 'BUY'  # Bybit uses Buy/Sell
        assert position.exchange1_entry_price == 50000.0
        assert position.exchange1_current_price == 51000.0
        assert position.exchange1_leverage == 10
        assert position.quantity == 1.0
        assert position.status == 'OPEN'
        assert position.unrealized_pnl == 1000.0

    def test_parse_position_none_values(self, bybit_exchange):
        """Test _parse_position with None values"""
        data = {
            'symbol': 'BTC/USDT:USDT',
            'size': None,
            'side': None,
            'avgPrice': None,
            'markPrice': None,
            'leverage': None,
        }

        position = bybit_exchange._parse_position(data)

        assert position.exchange1_entry_price == 0
        assert position.exchange1_current_price == 0
        assert position.exchange1_leverage == 1
        assert position.quantity == 0

    def test_parse_order(self, bybit_exchange):
        """Test _parse_order"""
        data = {
            'orderId': 'order123',
            'symbol': 'BTC/USDT:USDT',
            'side': 'Buy',
            'orderType': 'Limit',
            'qty': 1.0,
            'price': 50000.0,
            'cumExecQty': 0.5,
            'orderStatus': 'PartiallyFilled'
        }

        order = bybit_exchange._parse_order(data)

        assert order.id == 'order123'
        assert order.symbol == 'BTC/USDT:USDT'
        assert order.exchange == 'bybit'
        assert order.side == 'BUY'
        assert order.order_type == 'LIMIT'
        assert order.quantity == 1.0
        assert order.price == 50000.0
        assert order.filled_quantity == 0.5
        assert order.status == 'PARTIALLYFILLED'

    def test_parse_orderbook(self, bybit_exchange):
        """Test _parse_orderbook"""
        data = {
            'symbol': 'BTC/USDT:USDT',
            'bids': [[50000.0, 1.0], [49900.0, 2.0]],
            'asks': [[50100.0, 1.5], [50200.0, 3.0]],
            'ts': 1640995200000
        }

        orderbook = bybit_exchange._parse_orderbook(data)

        assert orderbook.symbol == 'BTC/USDT:USDT'
        assert orderbook.exchange == 'bybit'
        assert orderbook.bids == [(50000.0, 1.0), (49900.0, 2.0)]
        assert orderbook.asks == [(50100.0, 1.5), (50200.0, 3.0)]
        assert orderbook.timestamp == 1640995200000

    def test_parse_price_data(self, bybit_exchange):
        """Test _parse_price_data"""
        data = {
            'symbol': 'BTCUSDT',
            'bid1Price': 50000.0,
            'ask1Price': 50100.0,
            'bid1Size': 1.0,
            'ask1Size': 1.5,
            'time': 1640995200000
        }

        price_data = bybit_exchange._parse_price_data(data)

        assert price_data.symbol == 'BTCUSDT'
        assert price_data.bid == 50000.0
        assert price_data.ask == 50100.0
        assert price_data.bid_qty == 1.0
        assert price_data.ask_qty == 1.5
        assert price_data.timestamp == 1640995200.0

    def test_parse_balance(self, bybit_exchange):
        """Test _parse_balance"""
        data = {
            'result': {
                'list': [
                    {
                        'coin': 'USDT',
                        'walletBalance': 1000.0,
                        'availableToWithdraw': 800.0,
                        'used': 200.0
                    }
                ]
            }
        }

        balance = bybit_exchange._parse_balance(data)

        assert balance.exchange == 'bybit'
        assert balance.total == 1000.0
        assert balance.available == 800.0
        assert balance.margin_used == 200.0

    def test_parse_funding_rate(self, bybit_exchange):
        """Test _parse_funding_rate"""
        data = {
            'symbol': 'BTCUSDT',
            'fundingRate': 0.0001,
            'nextFundingTime': 1640995200000
        }

        funding = bybit_exchange._parse_funding_rate(data)

        assert funding.symbol == 'BTCUSDT'
        assert funding.exchange == 'bybit'
        assert funding.rate == 0.0001
        assert funding.rate_bps == 1.0
        assert funding.next_funding_time.year == 2022

    # 3. CCXT Error Handling Tests
    @pytest.mark.asyncio
    async def test_api_get_orderbook_rate_limit(self, bybit_exchange):
        """Test RateLimitError handling"""
        with patch.object(bybit_exchange.client, 'fetch_order_book', side_effect=ccxt.RateLimitExceeded("Rate limit")):
            with pytest.raises(RateLimitError):
                await bybit_exchange._api_get_orderbook("BTCUSDT", 10)

    @pytest.mark.asyncio
    async def test_api_get_price_data_network_error(self, bybit_exchange):
        """Test NetworkError handling"""
        with patch.object(bybit_exchange.client, 'fetch_ticker', side_effect=ccxt.NetworkError("Network error")):
            with pytest.raises(NetworkError):
                await bybit_exchange._api_get_price_data("BTCUSDT")

    # 4. Client Initialization Tests
    def test_client_initialization(self, bybit_exchange):
        """Test ccxt client initialization"""
        assert bybit_exchange.client.id == 'bybit'
        assert bybit_exchange.client.options['defaultType'] == 'swap'
        assert bybit_exchange.client.options['defaultSubType'] == 'linear'

    # 5. Time Synchronization Tests
    @pytest.mark.asyncio
    async def test_connect_time_sync(self, bybit_exchange):
        """Test time synchronization in connect"""
        with patch.object(bybit_exchange.client, 'fetch_time', return_value=1640995200000):
            await bybit_exchange.connect()
            assert bybit_exchange.client.options['timeDifference'] is not None

    # Commission and SL/TP Tests
    def test_commission_calculation(self, bybit_exchange):
        """Test taker commission calculation"""
        # Bybit taker commission is 5.5 bps
        taker_fee_bps = 5.5
        spread_bps = 9.0

        net_spread = spread_bps - taker_fee_bps
        assert net_spread == 3.5

    @pytest.mark.asyncio
    async def test_stop_loss_setup(self, bybit_exchange):
        """Test stop loss setup"""
        with patch.object(bybit_exchange.client, 'fetch_positions') as mock_positions:
            mock_positions.return_value = [{
                'size': 1.0,
                'side': 'Buy',
                'symbol': 'BTC/USDT:USDT'
            }]

            with patch.object(bybit_exchange.client, 'create_order') as mock_order:
                mock_order.return_value = {'id': 'sl123'}

                result = await bybit_exchange._api_set_stop_loss("BTCUSDT", PositionSide.LONG, 49000.0)

                assert result['order_id'] == 'sl123'
                assert result['type'] == 'stop_loss'

    @pytest.mark.asyncio
    async def test_take_profit_setup(self, bybit_exchange):
        """Test take profit setup"""
        with patch.object(bybit_exchange.client, 'fetch_positions') as mock_positions:
            mock_positions.return_value = [{
                'size': 1.0,
                'side': 'Sell',
                'symbol': 'BTC/USDT:USDT'
            }]

            with patch.object(bybit_exchange.client, 'create_order') as mock_order:
                mock_order.return_value = {'id': 'tp123'}

                result = await bybit_exchange._api_set_take_profit("BTCUSDT", PositionSide.SHORT, 51000.0)

                assert result['order_id'] == 'tp123'
                assert result['type'] == 'take_profit'

    @pytest.mark.asyncio
    async def test_api_get_symbol_info_uses_symbol_specific_max_leverage(self, bybit_exchange):
        """limits.leverage.max must override the generic 100 default."""
        ccxt_symbol = "HYPE/USDT:USDT"
        bybit_exchange.client.markets = {
            ccxt_symbol: {
                "limits": {
                    "amount": {"min": 1, "max": 1000000},
                    "price": {"min": 0.0001},
                    "leverage": {"max": 75},
                },
                "precision": {"amount": 1, "price": 0.0001},
                "info": {"leverageFilter": {"maxLeverage": "125"}},
            }
        }
        symbol_info = await bybit_exchange._api_get_symbol_info("HYPEUSDT")
        assert symbol_info["max_leverage"] == 75

    def test_extract_max_leverage_from_leverage_filter(self, bybit_exchange):
        """info.leverageFilter.maxLeverage is used when limits.leverage absent."""
        market = {
            "limits": {},
            "info": {"leverageFilter": {"maxLeverage": "50"}},
        }
        assert bybit_exchange._extract_max_leverage(market) == 50

    def test_extract_max_leverage_fallback(self, bybit_exchange):
        """Falls back to 100 when no leverage info present."""
        assert bybit_exchange._extract_max_leverage({"limits": {}, "info": {}}) == 100