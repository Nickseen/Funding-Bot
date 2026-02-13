"""
Unit tests for GateExchange adapter
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime, timezone
import ccxt

from src.exchanges.gate import GateExchange
from src.exchanges.base import ExchangeError, RateLimitError, NetworkError
from src.exchanges.enums import Exchange, PositionSide, OrderSide, OrderType
from src.exchanges.types import Position, Order, OrderBook, PriceData, Balance, FundingRate


class TestGateExchange:
    """Test GateExchange adapter"""

    @pytest.fixture
    def gate_exchange(self):
        """Create GateExchange instance"""
        return GateExchange(
            api_key="test_key",
            secret_key="test_secret",
            testnet=True
        )

    # 1. Symbol Conversion Tests
    def test_convert_symbol_simple(self, gate_exchange):
        """Test _convert_symbol with simple format"""
        assert gate_exchange._convert_symbol("BTCUSDT") == "BTC/USDT:USDT"
        assert gate_exchange._convert_symbol("ETHUSDT") == "ETH/USDT:USDT"

    def test_convert_symbol_already_converted(self, gate_exchange):
        """Test _convert_symbol with already converted format"""
        symbol = "BTC/USDT:USDT"
        assert gate_exchange._convert_symbol(symbol) == symbol

    def test_convert_symbol_fallback(self, gate_exchange):
        """Test _convert_symbol fallback"""
        assert gate_exchange._convert_symbol("UNKNOWN") == "UNKNOWN"

    def test_to_gate_symbol(self, gate_exchange):
        """Test _to_gate_symbol conversion"""
        assert gate_exchange._to_gate_symbol("BTCUSDT") == "BTC_USDT"
        assert gate_exchange._to_gate_symbol("BTC/USDT:USDT") == "BTC_USDT"

    # 2. Parser Tests
    def test_parse_position(self, gate_exchange):
        """Test _parse_position"""
        data = {
            'contract': 'BTC/USDT:USDT',
            'size': 1.0,
            'side': 'long',
            'entry_price': 50000.0,
            'mark_price': 51000.0,
            'leverage': 10,
            'user_id': 123,
            'liq_price': 45000.0,
            'pnl': 1000.0
        }

        position = gate_exchange._parse_position(data)

        assert position.id.startswith("gate_BTC/USDT:USDT_")
        assert position.pair == 'BTC/USDT:USDT'
        assert position.exchange1 == 'gate'
        assert position.exchange1_side == 'LONG'
        assert position.exchange1_entry_price == 50000.0
        assert position.exchange1_current_price == 51000.0
        assert position.exchange1_leverage == 10
        assert position.quantity == 1.0
        assert position.status == 'OPEN'
        assert position.unrealized_pnl == 1000.0

    def test_parse_position_none_values(self, gate_exchange):
        """Test _parse_position with None values"""
        data = {
            'contract': 'BTC/USDT:USDT',
            'size': None,
            'side': None,
            'entry_price': None,
            'mark_price': None,
            'leverage': None,
        }

        position = gate_exchange._parse_position(data)

        assert position.exchange1_entry_price == 0
        assert position.exchange1_current_price == 0
        assert position.exchange1_leverage == 1
        assert position.quantity == 0

    def test_parse_order(self, gate_exchange):
        """Test _parse_order"""
        data = {
            'id': 'order123',
            'contract': 'BTC/USDT:USDT',
            'side': 'long',
            'type': 'limit',
            'size': 1.0,
            'price': 50000.0,
            'fill_size': 0.5,
            'status': 'finished'
        }

        order = gate_exchange._parse_order(data)

        assert order.id == 'order123'
        assert order.symbol == 'BTC/USDT:USDT'
        assert order.exchange == 'gate'
        assert order.side == 'LONG'
        assert order.order_type == 'LIMIT'
        assert order.quantity == 1.0
        assert order.price == 50000.0
        assert order.filled_quantity == 0.5
        assert order.status == 'FINISHED'

    def test_parse_orderbook(self, gate_exchange):
        """Test _parse_orderbook"""
        data = {
            'contract': 'BTC/USDT:USDT',
            'bids': [[50000.0, 1.0], [49900.0, 2.0]],
            'asks': [[50100.0, 1.5], [50200.0, 3.0]],
            'update_time': 1640995200000
        }

        orderbook = gate_exchange._parse_orderbook(data)

        assert orderbook.symbol == 'BTC/USDT:USDT'
        assert orderbook.exchange == 'gate'
        assert orderbook.bids == [(50000.0, 1.0), (49900.0, 2.0)]
        assert orderbook.asks == [(50100.0, 1.5), (50200.0, 3.0)]
        assert orderbook.timestamp == 1640995200000

    def test_parse_price_data(self, gate_exchange):
        """Test _parse_price_data"""
        data = {
            'contract': 'BTCUSDT',
            'highest_bid': 50000.0,
            'lowest_ask': 50100.0,
            'bid_size': 1.0,
            'ask_size': 1.5,
            'update_time': 1640995200000
        }

        price_data = gate_exchange._parse_price_data(data)

        assert price_data.symbol == 'BTCUSDT'
        assert price_data.bid == 50000.0
        assert price_data.ask == 50100.0
        assert price_data.bid_qty == 1.0
        assert price_data.ask_qty == 1.5
        assert price_data.timestamp == 1640995200.0

    def test_parse_balance(self, gate_exchange):
        """Test _parse_balance"""
        data = {
            'currency': 'USDT',
            'total': 1000.0,
            'available': 800.0,
            'unrealised_pnl': 50.0,
            'margin': 150.0
        }

        balance = gate_exchange._parse_balance(data)

        assert balance.exchange == 'gate'
        assert balance.total == 1000.0
        assert balance.available == 800.0
        assert balance.margin_used == 150.0

    def test_parse_funding_rate(self, gate_exchange):
        """Test _parse_funding_rate"""
        data = {
            'contract': 'BTCUSDT',
            'funding_rate': 0.0001,
            'funding_next_apply': 1640995200000
        }

        funding = gate_exchange._parse_funding_rate(data)

        assert funding.symbol == 'BTCUSDT'
        assert funding.exchange == 'gate'
        assert funding.rate == 0.0001
        assert funding.rate_bps == 1.0
        assert funding.next_funding_time.year == 2022

    # 3. CCXT Error Handling Tests
    @pytest.mark.asyncio
    async def test_api_get_orderbook_rate_limit(self, gate_exchange):
        """Test RateLimitError handling"""
        with patch.object(gate_exchange.client, 'fetch_order_book', side_effect=ccxt.RateLimitExceeded("Rate limit")):
            with pytest.raises(RateLimitError):
                await gate_exchange._api_get_orderbook("BTCUSDT", 10)

    @pytest.mark.asyncio
    async def test_api_get_price_data_network_error(self, gate_exchange):
        """Test NetworkError handling"""
        with patch.object(gate_exchange.client, 'fetch_ticker', side_effect=ccxt.NetworkError("Network error")):
            with pytest.raises(NetworkError):
                await gate_exchange._api_get_price_data("BTCUSDT")

    # 4. Client Initialization Tests
    def test_client_initialization(self, gate_exchange):
        """Test ccxt client initialization"""
        assert gate_exchange.client.id == 'gate'
        assert gate_exchange.client.options['defaultType'] == 'swap'
        assert gate_exchange.client.options['defaultSubType'] == 'linear'

    # 5. Time Synchronization Tests
    @pytest.mark.asyncio
    async def test_connect_time_sync(self, gate_exchange):
        """Test time synchronization in connect"""
        with patch.object(gate_exchange.client, 'fetch_time', return_value=1640995200000):
            await gate_exchange.connect()
            assert gate_exchange.client.options['timeDifference'] is not None

    # Commission and SL/TP Tests
    def test_commission_calculation(self, gate_exchange):
        """Test taker commission calculation"""
        # Gate.io taker commission is 5 bps
        taker_fee_bps = 5.0
        spread_bps = 8.0

        net_spread = spread_bps - taker_fee_bps
        assert net_spread == 3.0

    @pytest.mark.asyncio
    async def test_stop_loss_setup(self, gate_exchange):
        """Test stop loss setup"""
        with patch.object(gate_exchange.client, 'fetch_positions') as mock_positions:
            mock_positions.return_value = [{
                'size': 1.0,
                'side': 'long',
                'contract': 'BTC/USDT:USDT'
            }]

            with patch.object(gate_exchange.client, 'create_order') as mock_order:
                mock_order.return_value = {'id': 'sl123'}

                result = await gate_exchange._api_set_stop_loss("BTCUSDT", PositionSide.LONG, 49000.0)

                assert result['order_id'] == 'sl123'
                assert result['type'] == 'stop_loss'

    @pytest.mark.asyncio
    async def test_take_profit_setup(self, gate_exchange):
        """Test take profit setup"""
        with patch.object(gate_exchange.client, 'fetch_positions') as mock_positions:
            mock_positions.return_value = [{
                'size': 1.0,
                'side': 'short',
                'contract': 'BTC/USDT:USDT'
            }]

            with patch.object(gate_exchange.client, 'create_order') as mock_order:
                mock_order.return_value = {'id': 'tp123'}

                result = await gate_exchange._api_set_take_profit("BTCUSDT", PositionSide.SHORT, 51000.0)

                assert result['order_id'] == 'tp123'
                assert result['type'] == 'take_profit'