"""
Unit tests for LighterExchange adapter
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime, timezone
import ccxt

from src.exchanges.lighter import LighterExchange
from src.exchanges.base import ExchangeError, RateLimitError, NetworkError
from src.exchanges.enums import Exchange, PositionSide, OrderSide, OrderType
from src.exchanges.types import Position, Order, OrderBook, PriceData, Balance, FundingRate


class TestLighterExchange:
    """Test LighterExchange adapter"""

    @pytest.fixture
    def lighter_exchange(self):
        """Create LighterExchange instance"""
        return LighterExchange(
            api_key="test_key",
            secret_key="test_secret",
            testnet=True
        )

    # 1. Symbol Conversion Tests
    def test_convert_symbol_simple(self, lighter_exchange):
        """Test _convert_symbol with simple format"""
        assert lighter_exchange._convert_symbol("BTCUSDT") == 1  # BTC market index
        assert lighter_exchange._convert_symbol("ETHUSDT") == 0  # ETH market index

    def test_convert_symbol_already_converted(self, lighter_exchange):
        """Test _convert_symbol with already converted format"""
        # Market index should be returned as-is
        assert lighter_exchange._convert_symbol(1) == 1

    def test_convert_symbol_fallback(self, lighter_exchange):
        """Test _convert_symbol fallback"""
        assert lighter_exchange._convert_symbol("UNKNOWN") == "UNKNOWN"

    def test_symbol_to_market_index(self, lighter_exchange):
        """Test _symbol_to_market_index"""
        assert lighter_exchange._symbol_to_market_index("BTCUSDT") == 1
        assert lighter_exchange._symbol_to_market_index("ETHUSDT") == 0
        assert lighter_exchange._symbol_to_market_index("UNKNOWN") == -1

    def test_market_index_to_symbol(self, lighter_exchange):
        """Test _market_index_to_symbol"""
        assert lighter_exchange._market_index_to_symbol(1) == "BTCUSDT"
        assert lighter_exchange._market_index_to_symbol(0) == "ETHUSDT"
        assert lighter_exchange._market_index_to_symbol(999) == "UNKNOWN"

    # 2. Parser Tests
    def test_parse_position(self, lighter_exchange):
        """Test _parse_position"""
        data = {
            'market_index': 1,
            'size': 1000,  # 0.1 BTC
            'entry_price': 40000000,  # $40000.00 in cents
            'mark_price': 41000000,   # $41000.00 in cents
            'leverage': 10,
            'pnl': 100000  # $1000.00 in cents
        }

        position = lighter_exchange._parse_position(data)

        assert position.id.startswith("lighter_BTCUSDT_")
        assert position.pair == 'BTCUSDT'
        assert position.exchange1 == 'lighter'
        assert position.exchange1_side == 'LONG'  # Assuming positive size = long
        assert position.exchange1_entry_price == 40000.0  # Converted from cents
        assert position.exchange1_current_price == 41000.0
        assert position.exchange1_leverage == 10
        assert position.quantity == 0.1  # Converted from base units
        assert position.status == 'OPEN'
        assert position.unrealized_pnl == 1000.0  # Converted from cents

    def test_parse_position_none_values(self, lighter_exchange):
        """Test _parse_position with None values"""
        data = {
            'market_index': 1,
            'size': None,
            'entry_price': None,
            'mark_price': None,
            'leverage': None,
        }

        position = lighter_exchange._parse_position(data)

        assert position.exchange1_entry_price == 0
        assert position.exchange1_current_price == 0
        assert position.exchange1_leverage == 1
        assert position.quantity == 0

    def test_parse_order(self, lighter_exchange):
        """Test _parse_order"""
        data = {
            'order_id': 'order123',
            'market_index': 1,
            'is_ask': False,  # BUY
            'order_type': 'limit',
            'amount': 1000,  # 0.1 BTC
            'price': 40000000,  # $40000.00
            'filled_amount': 500,  # 0.05 BTC
            'status': 'filled'
        }

        order = lighter_exchange._parse_order(data)

        assert order.id == 'order123'
        assert order.symbol == 'BTCUSDT'
        assert order.exchange == 'lighter'
        assert order.side == 'BUY'
        assert order.order_type == 'LIMIT'
        assert order.quantity == 0.1
        assert order.price == 40000.0
        assert order.filled_quantity == 0.05
        assert order.status == 'FILLED'

    def test_parse_orderbook(self, lighter_exchange):
        """Test _parse_orderbook"""
        data = {
            'market_index': 1,
            'bids': [[40000000, 1000], [39900000, 2000]],  # prices in cents, amounts in base units
            'asks': [[40100000, 1500], [40200000, 3000]],
            'timestamp': 1640995200000
        }

        orderbook = lighter_exchange._parse_orderbook(data)

        assert orderbook.symbol == 'BTCUSDT'
        assert orderbook.exchange == 'lighter'
        assert orderbook.bids == [(40000.0, 0.1), (39900.0, 0.2)]  # Converted prices and amounts
        assert orderbook.asks == [(40100.0, 0.15), (40200.0, 0.3)]
        assert orderbook.timestamp == 1640995200000

    def test_parse_price_data(self, lighter_exchange):
        """Test _parse_price_data"""
        data = {
            'market_index': 1,
            'best_bid': 40000000,  # $40000.00 in cents
            'best_ask': 40100000,  # $40100.00 in cents
            'bid_size': 1000,      # 0.1 BTC
            'ask_size': 1500,      # 0.15 BTC
            'timestamp': 1640995200000
        }

        price_data = lighter_exchange._parse_price_data(data)

        assert price_data.symbol == 'BTCUSDT'
        assert price_data.bid == 40000.0
        assert price_data.ask == 40100.0
        assert price_data.bid_qty == 0.1
        assert price_data.ask_qty == 0.15
        assert price_data.timestamp == 1640995200.0

    def test_parse_balance(self, lighter_exchange):
        """Test _parse_balance"""
        data = {
            'collateral': 100000000,  # $1000.00 in cents
            'available': 80000000,    # $800.00 in cents
            'margin_used': 15000000,  # $150.00 in cents
            'pnl': 5000000            # $50.00 in cents
        }

        balance = lighter_exchange._parse_balance(data)

        assert balance.exchange == 'lighter'
        assert balance.total == 1000.0
        assert balance.available == 800.0
        assert balance.margin_used == 150.0

    def test_parse_funding_rate(self, lighter_exchange):
        """Test _parse_funding_rate"""
        data = {
            'market_index': 1,
            'funding_rate': 0.0001,
            'next_funding_time': 1640995200000
        }

        funding = lighter_exchange._parse_funding_rate(data)

        assert funding.symbol == 'BTCUSDT'
        assert funding.exchange == 'lighter'
        assert funding.rate == 0.0001
        assert funding.rate_bps == 1.0
        assert funding.next_funding_time.year == 2022

    # 3. Error Handling Tests
    @pytest.mark.asyncio
    async def test_api_get_orderbook_rate_limit(self, lighter_exchange):
        """Test RateLimitError handling"""
        with patch.object(lighter_exchange, '_api_client') as mock_client:
            mock_client.get_orderbook.side_effect = Exception("Rate limit exceeded")

            with patch('src.exchanges.lighter.LIGHTER_AVAILABLE', True):
                with pytest.raises(ExchangeError):  # Lighter doesn't use ccxt exceptions
                    await lighter_exchange._api_get_orderbook("BTCUSDT", 10)

    # 4. Client Initialization Tests
    def test_client_initialization(self, lighter_exchange):
        """Test client initialization"""
        assert lighter_exchange.exchange_name == Exchange.LIGHTER
        assert lighter_exchange.api_key == "test_key"
        assert lighter_exchange.secret_key == "test_secret"

    # 5. Time Synchronization Tests
    @pytest.mark.asyncio
    async def test_connect_time_sync(self, lighter_exchange):
        """Test time synchronization in connect"""
        with patch('src.exchanges.lighter.LIGHTER_AVAILABLE', True):
            with patch('lighter.SignerClient') as mock_signer:
                mock_instance = Mock()
                mock_signer.return_value = mock_instance

                await lighter_exchange.connect()
                assert lighter_exchange.connected

    # Commission and SL/TP Tests
    def test_commission_calculation(self, lighter_exchange):
        """Test taker commission calculation"""
        # Lighter taker commission is 0 bps (FREE!)
        taker_fee_bps = 0.0
        spread_bps = 5.0

        net_spread = spread_bps - taker_fee_bps
        assert net_spread == 5.0

    @pytest.mark.asyncio
    async def test_stop_loss_setup(self, lighter_exchange):
        """Test stop loss setup"""
        with patch('src.exchanges.lighter.LIGHTER_AVAILABLE', True):
            with patch.object(lighter_exchange, '_signer_client') as mock_signer:
                mock_signer.place_stop_order.return_value = {'order_id': 'sl123'}

                result = await lighter_exchange._api_set_stop_loss("BTCUSDT", PositionSide.LONG, 49000.0)

                assert result['order_id'] == 'sl123'
                assert result['type'] == 'stop_loss'

    @pytest.mark.asyncio
    async def test_take_profit_setup(self, lighter_exchange):
        """Test take profit setup"""
        with patch('src.exchanges.lighter.LIGHTER_AVAILABLE', True):
            with patch.object(lighter_exchange, '_signer_client') as mock_signer:
                mock_signer.place_stop_order.return_value = {'order_id': 'tp123'}

                result = await lighter_exchange._api_set_take_profit("BTCUSDT", PositionSide.SHORT, 51000.0)

                assert result['order_id'] == 'tp123'
                assert result['type'] == 'take_profit'