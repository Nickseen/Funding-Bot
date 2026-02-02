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


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
