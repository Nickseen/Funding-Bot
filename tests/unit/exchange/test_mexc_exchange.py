"""
Quick test script for MEXC Exchange Adapter
Run: python -m pytest tests/unit/exchange/test_mexc_exchange.py -v
"""

import pytest
from datetime import datetime, timezone
from src.exchanges.mexc import MexcExchange
from src.exchanges.enums import Exchange, PositionSide, OrderSide, OrderType


class TestMexcExchangeBasics:
    """Basic tests for MEXC adapter structure"""
    
    def test_exchange_initialization(self):
        """Test that MEXC exchange initializes correctly"""
        exchange = MexcExchange(
            api_key="test_key",
            secret_key="test_secret",
            testnet=True
        )
        
        assert exchange.exchange_name == Exchange.MEXC
        assert exchange.client is not None
        assert exchange.connected is False
    
    def test_symbol_conversion(self):
        """Test symbol format conversion"""
        exchange = MexcExchange("key", "secret")
        
        # Test BTCUSDT -> BTC/USDT:USDT
        assert exchange._convert_symbol("BTCUSDT") == "BTC/USDT:USDT"
        assert exchange._convert_symbol("ETHUSDT") == "ETH/USDT:USDT"
        
        # Already converted
        assert exchange._convert_symbol("BTC/USDT:USDT") == "BTC/USDT:USDT"
    
    def test_mexc_api_format_conversion(self):
        """Test conversion to MEXC API format"""
        exchange = MexcExchange("key", "secret")
        
        # Test BTC/USDT:USDT -> BTC_USDT
        assert exchange._convert_to_mexc_format("BTC/USDT:USDT") == "BTC_USDT"
        assert exchange._convert_to_mexc_format("ETH/USDT:USDT") == "ETH_USDT"
    
    def test_parse_funding_rate_timezone_aware(self):
        """Test that funding rate parser uses timezone-aware datetime"""
        exchange = MexcExchange("key", "secret")
        
        # Test data with milliseconds timestamp
        test_data = {
            'symbol': 'BTC/USDT:USDT',
            'fundingRate': 0.0001,
            'nextFundingTime': 1704067200000,  # Jan 1, 2024 00:00:00 UTC in ms
        }
        
        funding_rate = exchange._parse_funding_rate(test_data)
        
        # Check that datetime is timezone-aware
        assert funding_rate.next_funding_time.tzinfo is not None
        assert funding_rate.next_funding_time.tzinfo == timezone.utc
        assert funding_rate.rate == 0.0001
        assert funding_rate.rate_bps == 1.0  # 0.0001 * 10000
    
    def test_parse_funding_rate_seconds_timestamp(self):
        """Test funding rate parser with seconds timestamp"""
        exchange = MexcExchange("key", "secret")
        
        # Test data with seconds timestamp
        test_data = {
            'symbol': 'BTC/USDT:USDT',
            'fundingRate': 0.0005,
            'nextFundingTime': 1704067200,  # Jan 1, 2024 00:00:00 UTC in seconds
        }
        
        funding_rate = exchange._parse_funding_rate(test_data)
        
        # Check that datetime is timezone-aware
        assert funding_rate.next_funding_time.tzinfo is not None
        assert funding_rate.next_funding_time.tzinfo == timezone.utc
        assert funding_rate.rate_bps == 5.0  # 0.0005 * 10000


class TestMexcExchangeMethods:
    """Test that all required methods exist"""
    
    def test_connection_methods_exist(self):
        """Test connection methods are implemented"""
        exchange = MexcExchange("key", "secret")
        
        assert hasattr(exchange, 'connect')
        assert hasattr(exchange, 'disconnect')
        assert hasattr(exchange, 'test_connection')
    
    def test_market_data_methods_exist(self):
        """Test market data methods are implemented"""
        exchange = MexcExchange("key", "secret")
        
        assert hasattr(exchange, '_api_get_orderbook')
        assert hasattr(exchange, '_api_get_price_data')
        assert hasattr(exchange, '_api_get_mark_price')
    
    def test_trading_methods_exist(self):
        """Test trading methods are implemented"""
        exchange = MexcExchange("key", "secret")
        
        assert hasattr(exchange, '_api_open_position')
        assert hasattr(exchange, '_api_close_position')
        assert hasattr(exchange, '_api_place_order')
        assert hasattr(exchange, '_api_cancel_order')
        assert hasattr(exchange, '_api_set_leverage')
        assert hasattr(exchange, '_api_set_margin_mode')
        assert hasattr(exchange, '_api_set_stop_loss')
        assert hasattr(exchange, '_api_set_take_profit')
    
    def test_account_methods_exist(self):
        """Test account methods are implemented"""
        exchange = MexcExchange("key", "secret")
        
        assert hasattr(exchange, '_api_get_balance')
        assert hasattr(exchange, '_api_get_positions')
        assert hasattr(exchange, '_api_get_position_by_symbol')
        assert hasattr(exchange, '_api_get_account_info')
    
    def test_info_methods_exist(self):
        """Test symbol info and funding methods are implemented"""
        exchange = MexcExchange("key", "secret")
        
        assert hasattr(exchange, '_api_get_symbol_info')
        assert hasattr(exchange, '_api_get_funding_rate')
    
    def test_parser_methods_exist(self):
        """Test parser methods are implemented"""
        exchange = MexcExchange("key", "secret")
        
        assert hasattr(exchange, '_parse_position')
        assert hasattr(exchange, '_parse_order')
        assert hasattr(exchange, '_parse_orderbook')
        assert hasattr(exchange, '_parse_price_data')
        assert hasattr(exchange, '_parse_balance')
        assert hasattr(exchange, '_parse_funding_rate')
    
    def test_websocket_stubs_exist(self):
        """Test WebSocket stub methods exist"""
        exchange = MexcExchange("key", "secret")
        
        assert hasattr(exchange, 'subscribe_orderbook')
        assert hasattr(exchange, 'subscribe_position_updates')
        assert hasattr(exchange, 'subscribe_order_updates')
        assert hasattr(exchange, 'subscribe_account_updates')
    
    def test_helper_methods_exist(self):
        """Test helper methods exist"""
        exchange = MexcExchange("key", "secret")

        assert hasattr(exchange, '_convert_symbol')
        assert hasattr(exchange, 'get_server_time')
        assert hasattr(exchange, 'sync_time')


class TestMexcParsePosition:
    """Tests for _parse_position correctness."""

    def _make_exchange(self):
        ex = MexcExchange("key", "secret")
        # Inject a fake market so contractSize can be looked up
        ex.client.markets = {
            'BTC/USDT:USDT': {'contractSize': 0.0001},
            'ETH/USDT:USDT': {'contractSize': 0.01},
        }
        return ex

    def test_parse_long_position_base_quantity(self):
        """holdVol (contracts) × contractSize must equal quantity in base currency."""
        ex = self._make_exchange()
        data = {
            'symbol': 'BTC/USDT:USDT',
            'side': 'long',
            'contracts': 1000.0,    # 1000 contracts × 0.0001 BTC = 0.1 BTC
            'entryPrice': 50000.0,
            'markPrice': 51000.0,
            'leverage': 10,
            'liquidationPrice': 45000.0,
            'info': {
                'positionId': 99,
                'unrealized': 10.5,
            }
        }
        pos = ex._parse_position(data)

        assert pos.quantity == pytest.approx(0.1)
        assert pos.exchange1_side == 'LONG'
        assert pos.exchange1_entry_price == 50000.0
        assert pos.exchange1_leverage == 10
        assert pos.unrealized_pnl == pytest.approx(10.5)
        assert pos.liquidation_price_ex1 == 45000.0
        assert '99' in pos.id

    def test_parse_short_position(self):
        """Short position should parse side = SHORT."""
        ex = self._make_exchange()
        data = {
            'symbol': 'ETH/USDT:USDT',
            'side': 'short',
            'contracts': 50.0,      # 50 contracts × 0.01 ETH = 0.5 ETH
            'entryPrice': 3000.0,
            'markPrice': 2900.0,
            'leverage': 5,
            'liquidationPrice': 3500.0,
            'info': {'positionId': 42, 'unrealized': -5.0},
        }
        pos = ex._parse_position(data)

        assert pos.exchange1_side == 'SHORT'
        assert pos.quantity == pytest.approx(0.5)
        assert pos.unrealized_pnl == pytest.approx(-5.0)

    def test_parse_position_contractsize_from_info_fallback(self):
        """When market cache is empty, fall back to contractSize in raw info."""
        ex = MexcExchange("key", "secret")
        ex.client.markets = {}  # empty cache

        data = {
            'symbol': 'SOL/USDT:USDT',
            'side': 'long',
            'contracts': 100.0,
            'entryPrice': 100.0,
            'markPrice': 105.0,
            'leverage': 10,
            'liquidationPrice': 80.0,
            'info': {'contractSize': 1.0, 'unrealized': 0.0},
        }
        pos = ex._parse_position(data)
        assert pos.quantity == pytest.approx(100.0)


class TestMexcParseBalance:
    """Tests for _parse_balance."""

    def test_parse_unified_ccxt_balance(self):
        """Should correctly extract free/used/total from ccxt unified balance."""
        ex = MexcExchange("key", "secret")
        data = {
            'USDT': {'free': 500.0, 'used': 100.0, 'total': 600.0},
            'info': {
                'data': [
                    {'currency': 'USDT', 'availableBalance': 500.0,
                     'equity': 600.0, 'unrealized': 12.5}
                ]
            }
        }
        bal = ex._parse_balance(data)

        assert bal.total == pytest.approx(600.0)
        assert bal.available == pytest.approx(500.0)
        assert bal.margin_used == pytest.approx(100.0)
        assert bal.unrealized_pnl == pytest.approx(12.5)
        assert bal.exchange == 'mexc'

    def test_parse_balance_missing_usdt(self):
        """Should return zeros when USDT balance is absent."""
        ex = MexcExchange("key", "secret")
        bal = ex._parse_balance({})

        assert bal.total == 0.0
        assert bal.available == 0.0


class TestMexcOptions:
    """Tests for adapter-level ccxt option overrides."""

    def test_unavailable_contracts_cleared(self):
        """BTC/USDT:USDT must NOT be in unavailableContracts."""
        ex = MexcExchange("key", "secret")
        unavailable = ex.client.options.get('unavailableContracts', {})
        assert 'BTC/USDT:USDT' not in unavailable
        assert 'ETH/USDT:USDT' not in unavailable
        assert 'LTC/USDT:USDT' not in unavailable

    def test_default_type_is_swap(self):
        """Adapter must use swap market type."""
        ex = MexcExchange("key", "secret")
        assert ex.client.options.get('defaultType') == 'swap'

    def test_testnet_flag_accepted_no_error(self):
        """testnet=True should not raise; MEXC has no futures testnet."""
        ex = MexcExchange("key", "secret", testnet=True)
        assert ex is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
