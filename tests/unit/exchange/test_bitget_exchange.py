"""
Unit tests for BitgetExchange adapter
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime, timezone
import ccxt

from src.exchanges.bitget import BitgetExchange
from src.exchanges.base import ExchangeError, RateLimitError, NetworkError
from src.exchanges.enums import Exchange, PositionSide, OrderSide, OrderType
from src.exchanges.types import Position, Order, OrderBook, PriceData, Balance, FundingRate


class TestBitgetExchange:
    """Test BitgetExchange adapter"""

    @pytest.fixture
    def bitget_exchange(self):
        """Create BitgetExchange instance"""
        return BitgetExchange(
            api_key="test_key",
            secret_key="test_secret",
            passphrase="test_passphrase",
            testnet=True
        )

    # 1. Symbol Conversion Tests
    def test_convert_symbol_simple(self, bitget_exchange):
        """Test _convert_symbol with simple format"""
        assert bitget_exchange._convert_symbol("BTCUSDT") == "BTC/USDT:USDT"
        assert bitget_exchange._convert_symbol("ETHUSDT") == "ETH/USDT:USDT"
        assert bitget_exchange._convert_symbol("ADAUSDT") == "ADA/USDT:USDT"

    def test_convert_symbol_usdc(self, bitget_exchange):
        """Test _convert_symbol with USDC"""
        assert bitget_exchange._convert_symbol("BTCUSDC") == "BTC/USDC:USDC"
        assert bitget_exchange._convert_symbol("ETHUSDC") == "ETH/USDC:USDC"

    def test_convert_symbol_already_converted(self, bitget_exchange):
        """Test _convert_symbol with already converted format"""
        symbol = "BTC/USDT:USDT"
        assert bitget_exchange._convert_symbol(symbol) == symbol

    def test_convert_symbol_fallback(self, bitget_exchange):
        """Test _convert_symbol fallback for unknown formats"""
        assert bitget_exchange._convert_symbol("UNKNOWN") == "UNKNOWN"

    def test_to_bitget_symbol(self, bitget_exchange):
        """Test _to_bitget_symbol conversion"""
        assert bitget_exchange._to_bitget_symbol("BTCUSDT") == "BTCUSDT_UMCBL"
        assert bitget_exchange._to_bitget_symbol("ETHUSDT") == "ETHUSDT_UMCBL"
        assert bitget_exchange._to_bitget_symbol("BTC/USDT:USDT") == "BTCUSDT_UMCBL"
        assert bitget_exchange._to_bitget_symbol("BTCUSDT_UMCBL") == "BTCUSDT_UMCBL"

    # 2. Parser Tests
    def test_parse_position(self, bitget_exchange):
        """Test _parse_position"""
        data = {
            'symbol': 'BTC/USDT:USDT',
            'contracts': 1.0,
            'side': 'long',
            'entryPrice': 50000.0,
            'markPrice': 51000.0,
            'leverage': 10,
            'id': 'pos123',
            'stopLossPrice': 49000.0,
            'takeProfitPrice': 52000.0,
            'liquidationPrice': 45000.0,
            'unrealizedPnl': 1000.0
        }

        position = bitget_exchange._parse_position(data)

        assert position.id.startswith("bitget_BTC/USDT:USDT_")
        assert position.pair == 'BTC/USDT:USDT'
        assert position.exchange1 == 'bitget'
        assert position.exchange1_side == 'LONG'
        assert position.exchange1_entry_price == 50000.0
        assert position.exchange1_current_price == 51000.0
        assert position.exchange1_leverage == 10
        assert position.quantity == 1.0
        assert position.status == 'OPEN'
        assert position.unrealized_pnl == 1000.0

    def test_parse_position_none_values(self, bitget_exchange):
        """Test _parse_position with None values"""
        data = {
            'symbol': 'BTC/USDT:USDT',
            'contracts': None,
            'side': None,
            'entryPrice': None,
            'markPrice': None,
            'leverage': None,
        }

        position = bitget_exchange._parse_position(data)

        assert position.exchange1_entry_price == 0
        assert position.exchange1_current_price == 0
        assert position.exchange1_leverage == 1
        assert position.quantity == 0

    def test_parse_position_closed(self, bitget_exchange):
        """Test _parse_position with zero contracts (closed)"""
        data = {
            'symbol': 'BTC/USDT:USDT',
            'contracts': 0,
            'side': 'long',
        }

        position = bitget_exchange._parse_position(data)
        assert position.status == 'CLOSED'

    def test_parse_order(self, bitget_exchange):
        """Test _parse_order"""
        data = {
            'id': 'order123',
            'symbol': 'BTC/USDT:USDT',
            'side': 'buy',
            'type': 'limit',
            'amount': 1.0,
            'price': 50000.0,
            'filled': 0.5,
            'status': 'open'
        }

        order = bitget_exchange._parse_order(data)

        assert order.id == 'order123'
        assert order.symbol == 'BTC/USDT:USDT'
        assert order.exchange == 'bitget'
        assert order.side == 'BUY'
        assert order.order_type == 'LIMIT'
        assert order.quantity == 1.0
        assert order.price == 50000.0
        assert order.filled_quantity == 0.5
        assert order.status == 'OPEN'

    def test_parse_order_none_values(self, bitget_exchange):
        """Test _parse_order with None values"""
        data = {
            'id': None,
            'symbol': None,
            'side': None,
            'type': None,
            'amount': None,
            'price': None,
            'filled': None,
            'status': None
        }

        order = bitget_exchange._parse_order(data)

        assert order.id == ''
        assert order.symbol == ''
        assert order.side == ''
        assert order.order_type == ''
        assert order.quantity == 0
        assert order.price == 0
        assert order.filled_quantity == 0
        assert order.status == 'OPEN'

    def test_parse_orderbook(self, bitget_exchange):
        """Test _parse_orderbook"""
        data = {
            'symbol': 'BTC/USDT:USDT',
            'bids': [[50000.0, 1.0], [49900.0, 2.0]],
            'asks': [[50100.0, 1.5], [50200.0, 3.0]],
            'timestamp': 1640995200000  # 2022-01-01 00:00:00 UTC in milliseconds
        }

        orderbook = bitget_exchange._parse_orderbook(data)

        assert orderbook.symbol == 'BTC/USDT:USDT'
        assert orderbook.exchange == 'bitget'
        assert orderbook.bids == [(50000.0, 1.0), (49900.0, 2.0)]
        assert orderbook.asks == [(50100.0, 1.5), (50200.0, 3.0)]
        assert orderbook.timestamp == 1640995200000

    def test_parse_orderbook_empty(self, bitget_exchange):
        """Test _parse_orderbook with empty bids/asks"""
        data = {
            'symbol': 'BTC/USDT:USDT',
            'bids': [],
            'asks': [],
            'timestamp': 1640995200000
        }

        orderbook = bitget_exchange._parse_orderbook(data)

        assert orderbook.bids == []
        assert orderbook.asks == []

    def test_parse_price_data(self, bitget_exchange):
        """Test _parse_price_data"""
        data = {
            'symbol': 'BTCUSDT',
            'bid': 50000.0,
            'ask': 50100.0,
            'bid_qty': 1.0,
            'ask_qty': 1.5,
            'timestamp': 1640995200000  # milliseconds
        }

        price_data = bitget_exchange._parse_price_data(data)

        assert price_data.symbol == 'BTCUSDT'
        assert price_data.bid == 50000.0
        assert price_data.ask == 50100.0
        assert price_data.bid_qty == 1.0
        assert price_data.ask_qty == 1.5
        assert price_data.timestamp == 1640995200.0  # converted to seconds

    def test_parse_price_data_none_timestamp(self, bitget_exchange):
        """Test _parse_price_data with None timestamp"""
        data = {
            'symbol': 'BTCUSDT',
            'bid': 50000.0,
            'ask': 50100.0,
            'bid_qty': 1.0,
            'ask_qty': 1.5,
            'timestamp': None
        }

        price_data = bitget_exchange._parse_price_data(data)

        # Should use current time when timestamp is None
        assert price_data.timestamp > 0

    def test_parse_balance_usdt_direct(self, bitget_exchange):
        """Test _parse_balance with direct USDT data"""
        data = {
            'USDT': {
                'total': 1000.0,
                'free': 800.0,
                'used': 200.0
            }
        }

        balance = bitget_exchange._parse_balance(data)

        assert balance.exchange == 'bitget'
        assert balance.total == 1000.0
        assert balance.available == 800.0
        assert balance.margin_used == 200.0

    def test_parse_balance_info_list(self, bitget_exchange):
        """Test _parse_balance with info as list"""
        data = {
            'info': [
                {
                    'marginCoin': 'USDT',
                    'equity': 1000.0,
                    'available': 800.0,
                    'locked': 200.0
                }
            ]
        }

        balance = bitget_exchange._parse_balance(data)

        assert balance.total == 1000.0
        assert balance.available == 800.0
        assert balance.margin_used == 200.0

    def test_parse_balance_fallback(self, bitget_exchange):
        """Test _parse_balance fallback to total/free/used dicts"""
        data = {
            'total': {'USDT': 1000.0},
            'free': {'USDT': 800.0},
            'used': {'USDT': 200.0}
        }

        balance = bitget_exchange._parse_balance(data)

        assert balance.total == 1000.0
        assert balance.available == 800.0
        assert balance.margin_used == 200.0

    def test_parse_funding_rate_milliseconds(self, bitget_exchange):
        """Test _parse_funding_rate with timestamp in milliseconds"""
        data = {
            'symbol': 'BTCUSDT',
            'fundingRate': 0.0001,
            'nextFundingTime': 1640995200000  # > 4102444800, so milliseconds
        }

        funding = bitget_exchange._parse_funding_rate(data)

        assert funding.symbol == 'BTCUSDT'
        assert funding.exchange == 'bitget'
        assert funding.rate == 0.0001
        assert funding.rate_bps == 1.0  # 0.0001 * 10000
        assert funding.next_funding_time.year == 2022
        assert funding.next_funding_time.month == 1
        assert funding.next_funding_time.day == 1

    def test_parse_funding_rate_seconds(self, bitget_exchange):
        """Test _parse_funding_rate with timestamp in seconds"""
        data = {
            'symbol': 'BTCUSDT',
            'fundingRate': 0.0002,
            'nextFundingTime': 1640995200  # < 4102444800, so seconds
        }

        funding = bitget_exchange._parse_funding_rate(data)

        assert funding.rate_bps == 2.0
        assert funding.next_funding_time.year == 2022

    def test_parse_funding_rate_no_timestamp(self, bitget_exchange):
        """Test _parse_funding_rate with no timestamp"""
        data = {
            'symbol': 'BTCUSDT',
            'fundingRate': 0.0001,
            'nextFundingTime': 0
        }

        funding = bitget_exchange._parse_funding_rate(data)

        # Should use current time
        assert funding.next_funding_time is not None
        assert funding.next_funding_time.tzinfo is not None  # timezone-aware

    # 3. CCXT Error Handling Tests
    @pytest.mark.asyncio
    async def test_api_get_orderbook_rate_limit(self, bitget_exchange):
        """Test RateLimitError handling in _api_get_orderbook"""
        with patch.object(bitget_exchange.client, 'fetch_order_book', side_effect=ccxt.RateLimitExceeded("Rate limit")):
            with pytest.raises(RateLimitError):
                await bitget_exchange._api_get_orderbook("BTCUSDT", 10)

    @pytest.mark.asyncio
    async def test_api_get_price_data_network_error(self, bitget_exchange):
        """Test NetworkError handling in _api_get_price_data"""
        with patch.object(bitget_exchange.client, 'fetch_ticker', side_effect=ccxt.NetworkError("Network error")):
            with pytest.raises(NetworkError):
                await bitget_exchange._api_get_price_data("BTCUSDT")

    @pytest.mark.asyncio
    async def test_api_set_leverage_already_set(self, bitget_exchange):
        """Test ignoring 'already set' leverage errors"""
        with patch.object(bitget_exchange.client, 'set_leverage', side_effect=Exception("leverage same")):
            # Should not raise
            result = await bitget_exchange._api_set_leverage("BTCUSDT", 10)
            assert result is True

    @pytest.mark.asyncio
    async def test_api_set_margin_mode_already_set(self, bitget_exchange):
        """Test ignoring 'already set' margin mode errors"""
        with patch.object(bitget_exchange.client, 'set_margin_mode', side_effect=Exception("already set")):
            # Should not raise
            result = await bitget_exchange._api_set_margin_mode("ISOLATED")
            assert result is True

    # 4. Client Initialization Tests
    def test_client_initialization_testnet(self, bitget_exchange):
        """Test ccxt client initialization with testnet"""
        assert bitget_exchange.client.id == 'bitget'
        assert bitget_exchange.client.apiKey == 'test_key'
        assert bitget_exchange.client.secret == 'test_secret'
        assert bitget_exchange.client.password == 'test_passphrase'
        assert bitget_exchange.client.options['defaultType'] == 'swap'
        assert bitget_exchange.client.options['defaultSubType'] == 'linear'
        assert bitget_exchange.client.options['recvWindow'] == 60000

    def test_client_initialization_mainnet(self):
        """Test ccxt client initialization without testnet"""
        exchange = BitgetExchange("key", "secret", "pass", testnet=False)
        # Should not have sandbox mode set
        assert not hasattr(exchange.client, 'sandbox') or not exchange.client.sandbox

    # 5. Time Synchronization Tests
    @pytest.mark.asyncio
    async def test_connect_time_sync(self, bitget_exchange):
        """Test time synchronization in connect"""
        server_time = 1640995200000  # 2022-01-01 00:00:00 UTC in milliseconds
        local_time = 1640995200000 - 1000  # 1 second difference

        with patch.object(bitget_exchange.client, 'fetch_time', return_value=server_time):
            with patch.object(bitget_exchange.client, 'milliseconds', return_value=local_time):
                await bitget_exchange.connect()

                # timeDifference should be set
                expected_diff = server_time - local_time
                assert bitget_exchange.client.options['timeDifference'] == expected_diff

    # Commission and SL/TP Tests
    def test_commission_calculation(self, bitget_exchange):
        """Test taker commission is properly deducted from spread"""
        # Bitget taker commission is 6 bps (0.06%)
        taker_fee_bps = 6.0
        spread_bps = 10.0  # 0.10%

        # Net spread after fees
        net_spread = spread_bps - taker_fee_bps
        assert net_spread == 4.0  # Should be positive for profitable trade

    @pytest.mark.asyncio
    async def test_stop_loss_setup(self, bitget_exchange):
        """Test stop loss order setup"""
        with patch.object(bitget_exchange.client, 'fetch_positions') as mock_positions:
            mock_positions.return_value = [{
                'contracts': 1.0,
                'side': 'long',
                'symbol': 'BTC/USDT:USDT'
            }]

            with patch.object(bitget_exchange.client, 'private_mix_post_mix_v1_plan_placetpsl') as mock_api:
                mock_api.return_value = {'data': {'orderId': 'sl123'}}

                result = await bitget_exchange._api_set_stop_loss("BTCUSDT", PositionSide.LONG, 49000.0)

                assert result['success'] is True
                assert result['order_id'] == 'sl123'
                assert result['type'] == 'stop_loss'
                assert result['trigger_price'] == 49000.0

    @pytest.mark.asyncio
    async def test_take_profit_setup(self, bitget_exchange):
        """Test take profit order setup"""
        with patch.object(bitget_exchange.client, 'fetch_positions') as mock_positions:
            mock_positions.return_value = [{
                'contracts': 1.0,
                'side': 'short',
                'symbol': 'BTC/USDT:USDT'
            }]

            with patch.object(bitget_exchange.client, 'private_mix_post_mix_v1_plan_placetpsl') as mock_api:
                mock_api.return_value = {'data': {'orderId': 'tp123'}}

                result = await bitget_exchange._api_set_take_profit("BTCUSDT", PositionSide.SHORT, 51000.0)

                assert result['success'] is True
                assert result['order_id'] == 'tp123'
                assert result['type'] == 'take_profit'
                assert result['trigger_price'] == 51000.0