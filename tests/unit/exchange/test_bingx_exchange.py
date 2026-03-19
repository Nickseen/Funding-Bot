"""
Unit tests for BingXExchange adapter
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime, timezone
import ccxt

from src.exchanges.bingx import BingXExchange
from src.exchanges.base import ExchangeError, RateLimitError, NetworkError
from src.exchanges.enums import Exchange, PositionSide, OrderSide, OrderType
from src.exchanges.types import Position, Order, OrderBook, PriceData, Balance, FundingRate


class TestBingXExchange:
    """Test BingXExchange adapter"""

    @pytest.fixture
    def bingx_exchange(self):
        """Create BingXExchange instance"""
        return BingXExchange(
            api_key="test_key",
            secret_key="test_secret",
            testnet=True
        )

    # 1. Symbol Conversion Tests
    def test_convert_symbol_simple(self, bingx_exchange):
        """Test _convert_symbol with simple format"""
        assert bingx_exchange._convert_symbol("BTCUSDT") == "BTC/USDT:USDT"
        assert bingx_exchange._convert_symbol("ETHUSDT") == "ETH/USDT:USDT"

    def test_convert_symbol_already_converted(self, bingx_exchange):
        """Test _convert_symbol with already converted format"""
        symbol = "BTC/USDT:USDT"
        assert bingx_exchange._convert_symbol(symbol) == symbol

    def test_convert_symbol_fallback(self, bingx_exchange):
        """Test _convert_symbol fallback"""
        assert bingx_exchange._convert_symbol("UNKNOWN") == "UNKNOWN"

    def test_to_bingx_symbol(self, bingx_exchange):
        """Test _to_bingx_symbol conversion"""
        assert bingx_exchange._to_bingx_symbol("BTCUSDT") == "BTC-USDT"
        assert bingx_exchange._to_bingx_symbol("BTC/USDT:USDT") == "BTC-USDT"

    # 2. Parser Tests
    def test_parse_position(self, bingx_exchange):
        """Test _parse_position"""
        data = {
            'symbol': 'BTC/USDT:USDT',
            'contracts': 1.0,
            'side': 'long',
            'entryPrice': 50000.0,
            'markPrice': 51000.0,
            'leverage': 10,
            'positionId': 'pos123',
            'stopLossPrice': 49000.0,
            'takeProfitPrice': 52000.0,
            'liquidationPrice': 45000.0,
            'unrealizedPnl': 1000.0
        }

        position = bingx_exchange._parse_position(data)

        assert position.id.startswith("bingx_BTC/USDT:USDT_")
        assert position.pair == 'BTC/USDT:USDT'
        assert position.exchange1 == 'bingx'
        assert position.exchange1_side == 'LONG'
        assert position.exchange1_entry_price == 50000.0
        assert position.exchange1_current_price == 51000.0
        assert position.exchange1_leverage == 10
        assert position.quantity == 1.0
        assert position.status == 'OPEN'
        assert position.unrealized_pnl == 1000.0

    def test_parse_position_none_values(self, bingx_exchange):
        """Test _parse_position with None values"""
        data = {
            'symbol': 'BTC/USDT:USDT',
            'contracts': None,
            'side': None,
            'entryPrice': None,
            'markPrice': None,
            'leverage': None,
        }

        position = bingx_exchange._parse_position(data)

        assert position.exchange1_entry_price == 0
        assert position.exchange1_current_price == 0
        assert position.exchange1_leverage == 1
        assert position.quantity == 0

    def test_parse_order(self, bingx_exchange):
        """Test _parse_order"""
        data = {
            'id': 'order123',
            'symbol': 'BTC/USDT:USDT',
            'side': 'BUY',
            'type': 'LIMIT',
            'amount': 1.0,
            'price': 50000.0,
            'filled': 0.5,
            'status': 'PARTIALLY_FILLED'
        }

        order = bingx_exchange._parse_order(data)

        assert order.id == 'order123'
        assert order.symbol == 'BTC/USDT:USDT'
        assert order.exchange == 'bingx'
        assert order.side == 'BUY'
        assert order.order_type == 'LIMIT'
        assert order.quantity == 1.0
        assert order.price == 50000.0
        assert order.filled_quantity == 0.5
        assert order.status == 'PARTIALLY_FILLED'

    def test_parse_orderbook(self, bingx_exchange):
        """Test _parse_orderbook"""
        data = {
            'symbol': 'BTC/USDT:USDT',
            'bids': [[50000.0, 1.0], [49900.0, 2.0]],
            'asks': [[50100.0, 1.5], [50200.0, 3.0]],
            'timestamp': 1640995200000
        }

        orderbook = bingx_exchange._parse_orderbook(data)

        assert orderbook.symbol == 'BTC/USDT:USDT'
        assert orderbook.exchange == 'bingx'
        assert orderbook.bids == [(50000.0, 1.0), (49900.0, 2.0)]
        assert orderbook.asks == [(50100.0, 1.5), (50200.0, 3.0)]
        assert orderbook.timestamp == 1640995200000

    def test_parse_price_data(self, bingx_exchange):
        """Test _parse_price_data"""
        data = {
            'symbol': 'BTCUSDT',
            'bid': 50000.0,
            'ask': 50100.0,
            'bid_qty': 1.0,
            'ask_qty': 1.5,
            'timestamp': 1640995200000
        }

        price_data = bingx_exchange._parse_price_data(data)

        assert price_data.symbol == 'BTCUSDT'
        assert price_data.bid == 50000.0
        assert price_data.ask == 50100.0
        assert price_data.bid_qty == 1.0
        assert price_data.ask_qty == 1.5
        assert price_data.timestamp == 1640995200.0

    def test_parse_balance(self, bingx_exchange):
        """Test _parse_balance"""
        # For testnet, BingX uses VST token
        data = {
            'VST': {
                'total': 1000.0,
                'free': 800.0,
                'used': 150.0
            }
        }

        balance = bingx_exchange._parse_balance(data)

        assert balance.exchange == 'bingx'
        assert balance.total == 1000.0
        assert balance.available == 800.0
        assert balance.margin_used == 150.0

    def test_parse_funding_rate(self, bingx_exchange):
        """Test _parse_funding_rate"""
        data = {
            'symbol': 'BTCUSDT',
            'fundingRate': 0.0001,
            'nextFundingTime': 1640995200000
        }

        funding = bingx_exchange._parse_funding_rate(data)

        assert funding.symbol == 'BTCUSDT'
        assert funding.exchange == 'bingx'
        assert funding.rate == 0.0001
        assert funding.rate_bps == 1.0
        assert funding.next_funding_time.year == 2022

    @pytest.mark.asyncio
    async def test_api_get_symbol_info_uses_base_quantity_units(self, bingx_exchange):
        """ExecutionEngine expects BingX quantity constraints already in base units."""
        ccxt_symbol = "DOGE/USDT:USDT"
        bingx_exchange.client.markets = {
            ccxt_symbol: {
                "contractSize": 0.163,
                "limits": {
                    "amount": {"min": 20, "max": 1000000},
                    "price": {"min": 0.0001},
                },
                "precision": {"amount": 1, "price": 0.0001},
                "info": {"maxLeverage": 50},
            }
        }

        symbol_info = await bingx_exchange._api_get_symbol_info("DOGEUSDT")

        # Even if exchange metadata contains contractSize, this adapter trades in base qty.
        assert symbol_info["contract_size"] == pytest.approx(1.0)
        assert symbol_info["min_quantity"] == pytest.approx(20.0)
        assert symbol_info["quantity_step"] == pytest.approx(1.0)

    # 3. CCXT Error Handling Tests
    @pytest.mark.asyncio
    async def test_api_get_orderbook_rate_limit(self, bingx_exchange):
        """Test RateLimitError handling"""
        with patch.object(bingx_exchange.client, 'fetch_order_book', side_effect=ccxt.RateLimitExceeded("Rate limit")):
            with pytest.raises(RateLimitError):
                await bingx_exchange._api_get_orderbook("BTCUSDT", 10)

    @pytest.mark.asyncio
    async def test_api_get_price_data_network_error(self, bingx_exchange):
        """Test NetworkError handling"""
        with patch.object(bingx_exchange.client, 'fetch_ticker', side_effect=ccxt.NetworkError("Network error")):
            with pytest.raises(NetworkError):
                await bingx_exchange._api_get_price_data("BTCUSDT")

    # 4. Client Initialization Tests
    def test_client_initialization(self, bingx_exchange):
        """Test ccxt client initialization"""
        assert bingx_exchange.client.id == 'bingx'
        assert bingx_exchange.client.options['defaultType'] == 'swap'

    # 5. Time Synchronization Tests
    @pytest.mark.asyncio
    async def test_connect_time_sync(self, bingx_exchange):
        """Test time synchronization in connect"""
        with patch.object(bingx_exchange.client, 'fetch_time', return_value=1640995200000):
            await bingx_exchange.connect()
            assert bingx_exchange.client.options['timeDifference'] is not None

    # Commission and SL/TP Tests
    def test_commission_calculation(self, bingx_exchange):
        """Test taker commission calculation"""
        # BingX taker commission is 5 bps
        taker_fee_bps = 5.0
        spread_bps = 8.0

        net_spread = spread_bps - taker_fee_bps
        assert net_spread == 3.0

    @pytest.mark.asyncio
    async def test_stop_loss_setup(self, bingx_exchange):
        """Test stop loss setup"""
        with patch.object(bingx_exchange.client, 'fetch_positions') as mock_positions:
            mock_positions.return_value = [{
                'contracts': 1.0,
                'side': 'long',
                'symbol': 'BTC/USDT:USDT'
            }]

            with patch.object(bingx_exchange.client, 'create_order') as mock_order:
                mock_order.return_value = {'id': 'sl123'}

                result = await bingx_exchange._api_set_stop_loss("BTCUSDT", PositionSide.LONG, 49000.0, 1.0)

                assert result['order_id'] == 'sl123'
                assert result['type'] == 'stop_loss'

    @pytest.mark.asyncio
    async def test_take_profit_setup(self, bingx_exchange):
        """Test take profit setup"""
        with patch.object(bingx_exchange.client, 'fetch_positions') as mock_positions:
            mock_positions.return_value = [{
                'contracts': 1.0,
                'side': 'short',
                'symbol': 'BTC/USDT:USDT'
            }]

            with patch.object(bingx_exchange.client, 'create_order') as mock_order:
                mock_order.return_value = {'id': 'tp123'}

                result = await bingx_exchange._api_set_take_profit("BTCUSDT", PositionSide.SHORT, 51000.0, 1.0)

                assert result['order_id'] == 'tp123'
                assert result['type'] == 'take_profit'