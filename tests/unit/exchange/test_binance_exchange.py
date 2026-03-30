"""Unit tests for BinanceExchange adapter."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.exchanges.binance import BinanceExchange


class TestBinanceExchange:
    """Test BinanceExchange symbol-info leverage behavior."""

    @pytest.fixture
    def binance_exchange(self):
        """Create BinanceExchange with mocked ccxt client."""
        with patch("src.exchanges.binance.ccxt.binance") as mock_ctor:
            mock_client = MagicMock()
            mock_client.id = "binance"
            mock_client.options = {"timeDifference": 0}
            mock_client.milliseconds.return_value = 1000
            mock_client.fetch_time = AsyncMock(return_value=2000)
            mock_client.load_markets = AsyncMock(return_value={})
            mock_client.close = AsyncMock(return_value=None)
            mock_client.markets = {}
            mock_ctor.return_value = mock_client

            exchange = BinanceExchange(
                api_key="test_key",
                secret_key="test_secret",
                testnet=True,
            )
            yield exchange

    @pytest.mark.asyncio
    async def test_api_get_symbol_info_uses_symbol_specific_max_leverage(self, binance_exchange):
        ccxt_symbol = "DOGE/USDT:USDT"
        binance_exchange.client.markets = {
            ccxt_symbol: {
                "limits": {
                    "amount": {"min": 1, "max": 1000000},
                    "price": {"min": 0.0001},
                    "leverage": {"max": 50},
                },
                "precision": {"amount": 1, "price": 0.0001},
                "info": {"maxLeverage": "125"},
            }
        }

        symbol_info = await binance_exchange._api_get_symbol_info("DOGEUSDT")
        assert symbol_info["max_leverage"] == 50

    def test_extract_max_leverage_from_filters(self, binance_exchange):
        market = {
            "limits": {},
            "info": {
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.001"},
                    {"filterType": "LEVERAGE", "maxLeverage": "75"},
                ]
            },
        }

        assert binance_exchange._extract_max_leverage(market) == 75
