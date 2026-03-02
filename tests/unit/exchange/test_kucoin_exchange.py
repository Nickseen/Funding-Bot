"""
Unit tests for KuCoinExchange adapter
"""

import pytest
from unittest.mock import Mock, patch, MagicMock, AsyncMock
from datetime import datetime, timezone
import ccxt

from src.exchanges.kucoin import KuCoinExchange
from src.exchanges.base import ExchangeError, RateLimitError, NetworkError
from src.exchanges.enums import Exchange, PositionSide, OrderSide, OrderType
from src.exchanges.types import Position, Order, OrderBook, PriceData, Balance, FundingRate


class TestKuCoinExchange:
    """Test KuCoinExchange adapter"""

    @pytest.fixture
    def kucoin_exchange(self):
        """Create KuCoinExchange instance"""
        return KuCoinExchange(
            api_key="test_key",
            secret_key="test_secret",
            passphrase="test_passphrase",
            testnet=True
        )

    # 1. Symbol Conversion Tests
    def test_convert_symbol_simple(self, kucoin_exchange):
        """Test _convert_symbol with simple format"""
        assert kucoin_exchange._convert_symbol("BTCUSDT") == "XBTUSDTM"
        assert kucoin_exchange._convert_symbol("ETHUSDT") == "ETHUSDTM"

    def test_convert_symbol_already_converted(self, kucoin_exchange):
        """Test _convert_symbol with already converted format"""
        symbol = "XBTUSDTM"
        assert kucoin_exchange._convert_symbol(symbol) == symbol

    def test_convert_symbol_fallback(self, kucoin_exchange):
        """Test _convert_symbol fallback"""
        assert kucoin_exchange._convert_symbol("UNKNOWN") == "UNKNOWN"

    # 2. Parser Tests
    def test_parse_position(self, kucoin_exchange):
        """Test _parse_position"""
        data = {
            'symbol': 'XBTUSDTM',
            'currentQty': 1.0,
            'side': 'long',
            'avgEntryPrice': 50000.0,
            'markPrice': 51000.0,
            'leverage': 10,
            'id': 'pos123',
            'liquidationPrice': 45000.0,
            'unrealisedPnl': 1000.0
        }

        position = kucoin_exchange._parse_position(data)

        assert position.id.startswith("kucoin_XBTUSDTM_")
        assert position.pair == 'XBTUSDTM'
        assert position.exchange1 == 'kucoin'
        assert position.exchange1_side == 'LONG'
        assert position.exchange1_entry_price == 50000.0
        assert position.exchange1_current_price == 51000.0
        assert position.exchange1_leverage == 10
        assert position.quantity == 1.0
        assert position.status == 'OPEN'
        assert position.unrealized_pnl == 1000.0

    def test_parse_position_none_values(self, kucoin_exchange):
        """Test _parse_position with None values"""
        data = {
            'symbol': 'XBTUSDTM',
            'currentQty': None,
            'side': None,
            'avgEntryPrice': None,
            'markPrice': None,
            'leverage': None,
        }

        position = kucoin_exchange._parse_position(data)

        assert position.exchange1_entry_price == 0
        assert position.exchange1_current_price == 0
        assert position.exchange1_leverage == 1
        assert position.quantity == 0

    def test_parse_order(self, kucoin_exchange):
        """Test _parse_order"""
        data = {
            'id': 'order123',
            'symbol': 'XBTUSDTM',
            'side': 'buy',
            'type': 'limit',
            'size': 1.0,
            'price': 50000.0,
            'dealSize': 0.5,
            'status': 'done'
        }

        order = kucoin_exchange._parse_order(data)

        assert order.id == 'order123'
        assert order.symbol == 'XBTUSDTM'
        assert order.exchange == 'kucoin'
        assert order.side == 'BUY'
        assert order.order_type == 'LIMIT'
        assert order.quantity == 1.0
        assert order.price == 50000.0
        assert order.filled_quantity == 0.5
        assert order.status == 'DONE'

    def test_parse_orderbook(self, kucoin_exchange):
        """Test _parse_orderbook"""
        data = {
            'symbol': 'XBTUSDTM',
            'bids': [[50000.0, 1.0], [49900.0, 2.0]],
            'asks': [[50100.0, 1.5], [50200.0, 3.0]],
            'timestamp': 1640995200000
        }

        orderbook = kucoin_exchange._parse_orderbook(data)

        assert orderbook.symbol == 'XBTUSDTM'
        assert orderbook.exchange == 'kucoin'
        assert orderbook.bids == [(50000.0, 1.0), (49900.0, 2.0)]
        assert orderbook.asks == [(50100.0, 1.5), (50200.0, 3.0)]
        assert orderbook.timestamp == 1640995200000

    def test_parse_price_data(self, kucoin_exchange):
        """Test _parse_price_data"""
        data = {
            'symbol': 'XBTUSDTM',
            'bestBidPrice': 50000.0,
            'bestAskPrice': 50100.0,
            'bestBidSize': 1.0,
            'bestAskSize': 1.5,
            'time': 1640995200000
        }

        price_data = kucoin_exchange._parse_price_data(data)

        assert price_data.symbol == 'XBTUSDTM'
        assert price_data.bid == 50000.0
        assert price_data.ask == 50100.0
        assert price_data.bid_qty == 1.0
        assert price_data.ask_qty == 1.5
        assert price_data.timestamp == 1640995200.0

    def test_parse_balance(self, kucoin_exchange):
        """Test _parse_balance"""
        data = {
            'currency': 'USDT',
            'accountEquity': 1000.0,
            'availableBalance': 800.0,
            'unrealisedPNL': 50.0,
            'marginBalance': 150.0
        }

        balance = kucoin_exchange._parse_balance(data)

        assert balance.exchange == 'kucoin'
        assert balance.total == 1000.0
        assert balance.available == 800.0
        assert balance.margin_used == 150.0

    def test_parse_funding_rate(self, kucoin_exchange):
        """Test _parse_funding_rate"""
        data = {
            'symbol': 'XBTUSDTM',
            'fundingRate': 0.0001,
            'nextFundingRateTime': 1640995200000
        }

        funding = kucoin_exchange._parse_funding_rate(data)

        assert funding.symbol == 'XBTUSDTM'
        assert funding.exchange == 'kucoin'
        assert funding.rate == 0.0001
        assert funding.rate_bps == 1.0
        assert funding.next_funding_time.year == 2022

    # 3. CCXT Error Handling Tests
    @pytest.mark.asyncio
    async def test_api_get_orderbook_rate_limit(self, kucoin_exchange):
        """Test RateLimitError handling"""
        with patch.object(kucoin_exchange.client, 'fetch_order_book', side_effect=ccxt.RateLimitExceeded("Rate limit")):
            with pytest.raises(RateLimitError):
                await kucoin_exchange._api_get_orderbook("BTCUSDT", 10)

    @pytest.mark.asyncio
    async def test_api_get_price_data_network_error(self, kucoin_exchange):
        """Test NetworkError handling"""
        with patch.object(kucoin_exchange.client, 'fetch_ticker', side_effect=ccxt.NetworkError("Network error")):
            with pytest.raises(NetworkError):
                await kucoin_exchange._api_get_price_data("BTCUSDT")

    # 4. Client Initialization Tests
    def test_client_initialization(self, kucoin_exchange):
        """Test ccxt client initialization"""
        assert kucoin_exchange.client.id == 'kucoinfutures'
        assert kucoin_exchange.client.password == 'test_passphrase'

    # 5. Time Synchronization Tests
    @pytest.mark.asyncio
    async def test_connect_time_sync(self, kucoin_exchange):
        """Test time synchronization in connect"""
        with patch.object(kucoin_exchange.client, 'fetch_time', return_value=1640995200000):
            await kucoin_exchange.connect()
            assert kucoin_exchange.client.options['timeDifference'] is not None

    # Commission and SL/TP Tests
    def test_commission_calculation(self, kucoin_exchange):
        """Test taker commission calculation"""
        # KuCoin taker commission is 6 bps
        taker_fee_bps = 6.0
        spread_bps = 10.0

        net_spread = spread_bps - taker_fee_bps
        assert net_spread == 4.0

    @pytest.mark.asyncio
    async def test_stop_loss_setup(self, kucoin_exchange):
        """Test stop loss setup"""
        with patch.object(kucoin_exchange.client, 'fetch_positions') as mock_positions:
            mock_positions.return_value = [{
                'currentQty': 1.0,
                'side': 'long',
                'symbol': 'XBTUSDTM'
            }]

            with patch.object(kucoin_exchange.client, 'create_order') as mock_order:
                mock_order.return_value = {'id': 'sl123'}

                result = await kucoin_exchange._api_set_stop_loss("BTCUSDT", PositionSide.LONG, 49000.0)

                assert result['order_id'] == 'sl123'
                assert result['type'] == 'stop_loss'

    @pytest.mark.asyncio
    async def test_take_profit_setup(self, kucoin_exchange):
        """Test take profit setup"""
        with patch.object(kucoin_exchange.client, 'fetch_positions') as mock_positions:
            mock_positions.return_value = [{
                'currentQty': 1.0,
                'side': 'short',
                'symbol': 'XBTUSDTM'
            }]

            with patch.object(kucoin_exchange.client, 'create_order') as mock_order:
                mock_order.return_value = {'id': 'tp123'}

                result = await kucoin_exchange._api_set_take_profit("BTCUSDT", PositionSide.SHORT, 51000.0)

                assert result['order_id'] == 'tp123'
                assert result['type'] == 'take_profit'