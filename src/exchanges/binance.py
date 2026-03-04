"""
Binance USDT-M Futures Exchange Adapter

This adapter implements only the exchange-specific API calls.
All validation, logging, and error handling is done in BaseExchange.

Each method is 5-20 lines of pure API calls - no business logic!

Binance Futures API Reference:
- USDT-M Futures: https://binance-docs.github.io/apidocs/futures/en/
- Uses One-Way Mode (positionSide="BOTH") by default
- Quantities are in base currency (NOT contracts)
- marginType: "ISOLATED" / "CROSSED" (note: CROSSED, not CROSS!)
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import ccxt.async_support as ccxt
from loguru import logger as log

from .base import BaseExchange, ExchangeError, RateLimitError, NetworkError
from .enums import Exchange, PositionSide, OrderSide, OrderType
from .types import Position, Order, OrderBook, PriceData, Balance, FundingRate


class BinanceExchange(BaseExchange):
    """
    Binance USDT-M Futures adapter (Linear Perpetual - USDT settled)

    Implements ONLY:
    1. _api_* methods (raw API calls via ccxt)
    2. _parse_* methods (convert Binance response format to our types)

    All business logic is in BaseExchange!

    Notes:
    - Binance uses One-Way Mode by default (positionSide="BOTH")
    - Symbol format: BTCUSDT → BTC/USDT:USDT (ccxt)
    - Quantities are in BASE currency (e.g. BTC), NOT contracts
    - marginType uses "CROSSED" (not "CROSS" like other exchanges!)
    - Timestamps are ALWAYS in milliseconds
    - Funding intervals: 8h (00:00, 08:00, 16:00 UTC)
    """

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        testnet: bool = False
    ):
        super().__init__(api_key, secret_key, testnet=testnet)
        self.exchange_name = Exchange.BINANCE

        # Initialize ccxt client for USDT-M Futures
        self.client = ccxt.binance({
            'apiKey': api_key,
            'secret': secret_key,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',       # USDT-M Futures
                'adjustForTimeDifference': True,
                'recvWindow': 60000,           # 60-second receive window
                'timeDifference': 0,           # Will be auto-adjusted
            }
        })

        if testnet:
            # Binance deprecated futures sandbox/testnet in favour of Demo Trading.
            # Demo base: https://demo-fapi.binance.com
            # API keys: https://demo.binance.com/en/my/settings/api-management
            # Docs: https://developers.binance.com/docs/derivatives/
            demo = 'https://demo-fapi.binance.com'
            self.client.urls['api']['fapiPublic']    = f'{demo}/fapi/v1'
            self.client.urls['api']['fapiPublicV2']  = f'{demo}/fapi/v2'
            self.client.urls['api']['fapiPublicV3']  = f'{demo}/fapi/v3'
            self.client.urls['api']['fapiPrivate']   = f'{demo}/fapi/v1'
            self.client.urls['api']['fapiPrivateV2'] = f'{demo}/fapi/v2'
            self.client.urls['api']['fapiPrivateV3'] = f'{demo}/fapi/v3'
            self.client.urls['api']['fapiData']      = f'{demo}/futures/data'
            # Demo keys only work on fapi — disable spot/margin API calls
            # (ccxt loads currencies from sapi and margin pairs which fail with demo keys)
            self.client.options['fetchCurrencies'] = False
            self.client.options['fetchMarkets'] = ['linear']  # Only USDT-M futures

    # ============================================
    # CONNECTION
    # ============================================

    async def connect(self) -> bool:
        """
        Connect to Binance Futures

        Steps:
        1. Sync time with server
        2. Load markets
        3. Check position mode & set One-Way if needed
        """
        try:
            # 1. Sync time with server
            server_time = await self.client.fetch_time()
            local_time = self.client.milliseconds()
            time_diff = server_time - local_time
            self.client.options['timeDifference'] = time_diff

            # 2. Load markets
            await self.client.load_markets()

            # 3. Ensure One-Way position mode (dualSidePosition = false)
            try:
                await self.client.fapiPrivatePostPositionSideDual({
                    'dualSidePosition': 'false'
                })
            except Exception as e:
                # "No need to change position side" (code -4059) is expected
                err_msg = str(e).lower()
                if 'no need' not in err_msg and 'not changed' not in err_msg:
                    log.warning(f"Binance: Could not set One-Way mode: {e}")

            self.connected = True
            return True
        except Exception as e:
            raise ExchangeError(f"Failed to connect: {e}")

    async def disconnect(self) -> None:
        """Disconnect from Binance"""
        await self.client.close()
        self.connected = False

    async def test_connection(self) -> bool:
        """Test connection by fetching server time"""
        try:
            await self.client.fetch_time()
            return True
        except Exception:
            return False

    # ============================================
    # SYMBOL CONVERSION HELPERS
    # ============================================

    def _convert_symbol(self, symbol: str) -> str:
        """
        Convert simple symbol format to Binance ccxt format

        BTCUSDT  → BTC/USDT:USDT  (linear perpetual)
        BTC/USDT:USDT → BTC/USDT:USDT (already correct)
        """
        # Already in correct format
        if '/' in symbol:
            return symbol

        # Convert BTCUSDT → BTC/USDT:USDT
        quote_currencies = ['USDT', 'USDC', 'BUSD']
        for quote in quote_currencies:
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                return f"{base}/{quote}:{quote}"

        # Fallback - return as is
        return symbol

    def _round_price_str(self, price: float, tick_str: str) -> str:
        """
        Format price as string with exact precision from tickSize string.

        tick_str examples: '0.10' → 1 decimal, '1.00' → 0 decimals, '0.01' → 2 decimals.
        Uses string parsing (not float) to avoid floating point artifacts.
        """
        tick_size = float(tick_str)
        tick_stripped = tick_str.rstrip('0').rstrip('.')
        decimals = len(tick_stripped.split('.')[-1]) if '.' in tick_stripped else 0
        rounded = round(round(price / tick_size) * tick_size, decimals)
        return f"{rounded:.{decimals}f}"

    async def _get_tick_size(self, binance_symbol: str) -> str:
        """
        Get PRICE_FILTER tickSize for symbol.

        Uses ccxt's market() lookup which reads from the already-loaded markets
        cache (populated during connect() -> load_markets()). Falls back to
        a direct exchangeInfo API call if markets cache is empty (demo mode).

        Returns tick size as string (e.g. '0.10') to preserve exact precision.
        """
        if not hasattr(self, '_tick_size_cache'):
            self._tick_size_cache: Dict[str, str] = {}

        if binance_symbol in self._tick_size_cache:
            return self._tick_size_cache[binance_symbol]

        ccxt_symbol = self._convert_symbol(binance_symbol)

        # Try 1: ccxt markets cache (available after load_markets in connect())
        try:
            market = self.client.market(ccxt_symbol)
            for f in market.get('info', {}).get('filters', []):
                if f.get('filterType') == 'PRICE_FILTER':
                    tick_str = f['tickSize']
                    self._tick_size_cache[binance_symbol] = tick_str
                    log.debug(f"Binance {binance_symbol} tickSize={tick_str} (from markets cache)")
                    return tick_str
        except Exception:
            pass

        # Try 2: direct API call (demo mode may have empty markets)
        # ccxt generates method names from path: fapi/v1/exchangeInfo -> fapiPublicGetExchangeInfo
        for method_name in ('fapiPublicGetExchangeInfo', 'fapiPublicGetExchangeinfo'):
            try:
                method = getattr(self.client, method_name, None)
                if method:
                    info = await method({'symbol': binance_symbol})
                    for sym in info.get('symbols', []):
                        if sym.get('symbol') == binance_symbol:
                            for f in sym.get('filters', []):
                                if f.get('filterType') == 'PRICE_FILTER':
                                    tick_str = f['tickSize']
                                    self._tick_size_cache[binance_symbol] = tick_str
                                    log.debug(f"Binance {binance_symbol} tickSize={tick_str} (from API)")
                                    return tick_str
                    break
            except Exception as e:
                log.warning(f"Binance: {method_name} failed: {e}")

        log.warning(f"Binance: could not determine tickSize for {binance_symbol}, using '0.1'")
        return '0.1'

    def _to_binance_symbol(self, symbol: str) -> str:
        """
        Convert any symbol format to raw Binance format (BTCUSDT)

        BTC/USDT:USDT → BTCUSDT
        BTCUSDT → BTCUSDT
        """
        if '/' in symbol:
            # BTC/USDT:USDT → BTCUSDT
            return symbol.split('/')[0] + symbol.split('/')[1].split(':')[0]
        return symbol

    # ============================================
    # API ADAPTERS (pure API calls, no validation!)
    # ============================================

    async def _api_get_orderbook(self, symbol: str, limit: int) -> Dict[str, Any]:
        """Binance: GET /fapi/v1/depth"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            return await self.client.fetch_order_book(ccxt_symbol, limit)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))

    async def _api_get_price_data(self, symbol: str) -> Dict[str, Any]:
        """Binance: GET /fapi/v1/ticker/bookTicker"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            ticker = await self.client.fetch_ticker(ccxt_symbol)
            # For futures tickers bid/ask may be None — fall back to last traded price
            last_price = ticker.get('last') or 0
            bid = ticker.get('bid') or last_price
            ask = ticker.get('ask') or last_price
            return {
                'symbol': symbol,
                'bid': bid,
                'ask': ask,
                'bid_qty': ticker.get('bidVolume', 0),
                'ask_qty': ticker.get('askVolume', 0),
                'timestamp': ticker['timestamp']
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))

    async def _api_get_mark_price(self, symbol: str) -> float:
        """Binance: GET /fapi/v1/premiumIndex - mark price"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            ticker = await self.client.fetch_ticker(ccxt_symbol)
            # Binance returns mark price in the info field
            if 'info' in ticker and 'markPrice' in ticker['info']:
                return float(ticker['info']['markPrice'])
            # Fallback to last price
            return float(ticker['last'])
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))

    async def _api_open_position(
        self,
        symbol: str,
        side: PositionSide,
        quantity: float,
        leverage: int,
        order_type: OrderType,
        price: Optional[float]
    ) -> Dict[str, Any]:
        """
        Binance: Open position

        Steps:
        1. Set margin mode to isolated (ignore "No need to change" -4046)
        2. Set leverage
        3. Place order (One-Way mode, no positionSide needed)
        4. Fetch updated position
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)

            # 1. Set isolated margin mode
            try:
                await self.client.set_margin_mode('isolated', ccxt_symbol)
            except Exception as e:
                # Binance error -4046: "No need to change margin type"
                err_msg = str(e).lower()
                if 'no need' not in err_msg and 'not changed' not in err_msg:
                    log.warning(f"Binance: set_margin_mode warning: {e}")

            # 2. Set leverage (ignore "already set" error)
            try:
                await self.client.set_leverage(leverage, ccxt_symbol)
            except Exception as e:
                err_msg = str(e).lower()
                if 'not changed' not in err_msg and 'no need' not in err_msg:
                    raise

            # 3. Place order
            # In One-Way mode: no positionSide param needed
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = 'buy' if side == PositionSide.LONG else 'sell'

            params = {}

            if order_type == OrderType.LIMIT:
                order = await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=quantity,
                    price=price,
                    params=params
                )
            else:
                order = await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=quantity,
                    params=params
                )

            # 4. Fetch updated position
            positions = await self.client.fetch_positions([ccxt_symbol])
            position_data = next(
                (p for p in positions
                 if p['symbol'] == ccxt_symbol
                 and float(p.get('contracts', 0) or 0) != 0),
                None
            )

            return position_data or order

        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.InsufficientFunds as e:
            raise ExchangeError(f"Insufficient balance: {e}")
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))

    async def _api_close_position(
        self,
        symbol: str,
        order_type: OrderType,
        price: Optional[float]
    ) -> Dict[str, Any]:
        """
        Binance: Close position

        Steps:
        1. Get current position
        2. Place opposite order with reduceOnly=True (One-Way mode)
        3. Return updated position

        Note: Binance uses quantity in base currency, NOT contracts
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)

            # 1. Get current position
            positions = await self.client.fetch_positions([ccxt_symbol])
            position = next(
                (p for p in positions
                 if p['symbol'] == ccxt_symbol
                 and float(p.get('contracts', 0) or 0) != 0),
                None
            )

            if not position:
                raise ExchangeError(f"No position found for {symbol}")

            # Binance: contracts == quantity in base currency
            contracts = abs(float(position['contracts']))
            position_side = position['side']  # 'long' or 'short' (ccxt normalized)

            # 2. Place opposite order with reduceOnly=True
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = 'sell' if position_side == 'long' else 'buy'

            params = {'reduceOnly': True}

            if order_type == OrderType.LIMIT:
                await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=contracts,
                    price=price,
                    params=params
                )
            else:
                await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=contracts,
                    params=params
                )

            # 3. Return updated position (should be closed now)
            positions = await self.client.fetch_positions([ccxt_symbol])
            return next(
                (p for p in positions if p['symbol'] == ccxt_symbol),
                {'symbol': symbol, 'contracts': 0, 'side': None}
            )

        except ExchangeError:
            raise
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))

    async def _api_place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float],
        reduce_only: bool
    ) -> Dict[str, Any]:
        """Binance: POST /fapi/v1/order"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = side.value.lower()

            params = {}
            if reduce_only:
                params['reduceOnly'] = True

            if order_type == OrderType.LIMIT:
                return await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=quantity,
                    price=price,
                    params=params
                )
            else:
                return await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=quantity,
                    params=params
                )

        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))

    async def _api_cancel_order(self, order_id: str, symbol: str) -> bool:
        """Binance: DELETE /fapi/v1/order"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            await self.client.cancel_order(order_id, ccxt_symbol)
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))

    async def _api_set_leverage(self, symbol: str, leverage: int) -> bool:
        """Binance: POST /fapi/v1/leverage"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            await self.client.set_leverage(leverage, ccxt_symbol)
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            # Binance may return error if leverage is already set
            err_msg = str(e).lower()
            if 'not changed' in err_msg or 'no need' in err_msg:
                return True
            raise ExchangeError(f"Failed to set leverage: {e}")

    async def _api_set_margin_mode(self, mode: str) -> bool:
        """
        Binance: POST /fapi/v1/marginType

        IMPORTANT: Binance uses "CROSSED" (not "CROSS" like other exchanges!)
        Error -4046 "No need to change margin type" is silently ignored.
        """
        try:
            # Binance expects 'cross' or 'isolated' via ccxt
            margin_mode = 'cross' if mode == 'CROSS' else 'isolated'
            await self.client.set_margin_mode(margin_mode)
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            # Binance error -4046: "No need to change margin type"
            err_msg = str(e).lower()
            if 'no need' in err_msg or 'not changed' in err_msg:
                return True
            raise ExchangeError(f"Failed to set margin mode: {e}")

    async def _api_set_stop_loss(
        self,
        symbol: str,
        side: PositionSide,
        stop_price: float,
        quantity: Optional[float]
    ) -> Dict[str, Any]:
        """
        Binance: POST /fapi/v1/order (type=STOP_MARKET)

        Sets position-level SL that appears in the Positions table "TP/SL" column.
        Uses fapiPrivatePostOrder directly (NOT ccxt create_order) so that
        closePosition='true' is sent as a raw string — required by Binance to
        treat the order as position-level TP/SL rather than a regular conditional order.

        Key parameters:
        - closePosition='true'  → binds order to the position (shows in Positions row)
        - workingType=MARK_PRICE → triggers on mark price (not last price)
        - priceProtect='true'   → price manipulation protection

        Note: closePosition and reduceOnly cannot be used simultaneously!
        """
        try:
            binance_symbol = self._to_binance_symbol(symbol)
            ccxt_symbol = self._convert_symbol(symbol)
            order_side = 'sell' if side == PositionSide.LONG else 'buy'

            # Fetch tick size from markets/API (cached after first call)
            tick_str = await self._get_tick_size(binance_symbol)
            rounded_price = self._round_price_str(stop_price, tick_str)  # str e.g. '68930.1'
            log.debug(f"Binance SL: {symbol} raw={stop_price} tick={tick_str} rounded={rounded_price}")

            # Use create_order with closePosition=True — ccxt handles param serialisation
            # correctly for /fapi/v1/order (works on both demo and production).
            # closePosition=True attaches SL to position row ("TP/SL" column, not Open Orders).
            return await self.client.create_order(
                symbol=ccxt_symbol,
                type='STOP_MARKET',
                side=order_side,
                amount=None,
                price=None,
                params={
                    'stopPrice': rounded_price,  # string avoids float repr artifacts
                    'closePosition': True,
                    'workingType': 'MARK_PRICE',
                    'priceProtect': True,
                }
            )

        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to set stop loss: {e}")

    async def _api_set_take_profit(
        self,
        symbol: str,
        side: PositionSide,
        take_profit_price: float,
        quantity: Optional[float]
    ) -> Dict[str, Any]:
        """
        Binance: POST /fapi/v1/order (type=TAKE_PROFIT_MARKET)

        Sets position-level TP that appears in the Positions table "TP/SL" column.
        Uses fapiPrivatePostOrder directly (NOT ccxt create_order) so that
        closePosition='true' is sent as a raw string — required by Binance to
        treat the order as position-level TP/SL rather than a regular conditional order.

        Key parameters:
        - closePosition='true'  → binds order to the position (shows in Positions row)
        - workingType=MARK_PRICE → triggers on mark price (not last price)
        - priceProtect='true'   → price manipulation protection

        Note: closePosition and reduceOnly cannot be used simultaneously!
        """
        try:
            binance_symbol = self._to_binance_symbol(symbol)
            ccxt_symbol = self._convert_symbol(symbol)
            order_side = 'sell' if side == PositionSide.LONG else 'buy'

            # Fetch tick size from markets/API (cached after first call)
            tick_str = await self._get_tick_size(binance_symbol)
            rounded_price = self._round_price_str(take_profit_price, tick_str)  # str e.g. '68930.1'
            log.debug(f"Binance TP: {symbol} raw={take_profit_price} tick={tick_str} rounded={rounded_price}")

            # Use create_order with closePosition=True — ccxt handles param serialisation
            # correctly for /fapi/v1/order (works on both demo and production).
            # closePosition=True attaches TP to position row ("TP/SL" column, not Open Orders).
            return await self.client.create_order(
                symbol=ccxt_symbol,
                type='TAKE_PROFIT_MARKET',
                side=order_side,
                amount=None,
                price=None,
                params={
                    'stopPrice': rounded_price,
                    'closePosition': True,
                    'workingType': 'MARK_PRICE',
                    'priceProtect': True,
                }
            )

        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to set take profit: {e}")

    async def _api_get_balance(self) -> Dict[str, Any]:
        """Binance: GET /fapi/v3/balance"""
        try:
            balance = await self.client.fetch_balance({'type': 'future'})
            return balance
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))

    async def _api_get_positions(self, symbol: Optional[str]) -> List[Dict[str, Any]]:
        """Binance: GET /fapi/v2/positionRisk"""
        try:
            if symbol:
                ccxt_symbol = self._convert_symbol(symbol)
                positions = await self.client.fetch_positions([ccxt_symbol])
            else:
                positions = await self.client.fetch_positions()

            # Filter out zero positions (Binance returns all symbols with contracts=0)
            return [
                p for p in positions
                if float(p.get('contracts', 0) or 0) != 0
            ]
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))

    async def _api_get_position_by_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Binance: GET /fapi/v2/positionRisk for specific symbol"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            positions = await self.client.fetch_positions([ccxt_symbol])
            position = next(
                (p for p in positions
                 if float(p.get('contracts', 0) or 0) != 0),
                None
            )
            return position
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))

    async def _api_get_account_info(self) -> Dict[str, Any]:
        """Binance: GET /fapi/v2/account"""
        try:
            return await self.client.fetch_balance({'type': 'future'})
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))

    async def _api_get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Binance: GET /fapi/v1/exchangeInfo - symbol trading rules"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            markets = self.client.markets
            if ccxt_symbol not in markets:
                await self.client.load_markets(True)  # Force reload
                markets = self.client.markets

            market = markets.get(ccxt_symbol)
            if not market:
                raise ExchangeError(f"Symbol {symbol} not found")

            return {
                'min_quantity': market['limits']['amount']['min'],
                'max_quantity': market['limits']['amount']['max'],
                'quantity_step': market['precision']['amount'],
                'min_price': market['limits']['price']['min'],
                'price_tick': market['precision']['price'],
                'max_leverage': int(market.get('info', {}).get('maxLeverage', 125)),
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))

    async def _api_get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        """
        Binance: GET /fapi/v1/premiumIndex

        Returns lastFundingRate and nextFundingTime (always ms!).
        8h intervals: 00:00, 08:00, 16:00 UTC.
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            funding = await self.client.fetch_funding_rate(ccxt_symbol)

            # Binance uses 'nextFundingTimestamp' (ccxt normalized)
            next_funding_ts = (
                funding.get('nextFundingTimestamp')
                or funding.get('fundingTimestamp', 0)
            )

            return {
                'symbol': symbol,
                'fundingRate': funding.get('fundingRate', 0),
                'nextFundingTime': next_funding_ts,
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))

    async def _api_get_income_history(
        self,
        symbol: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 100
    ) -> Dict[str, float]:
        """
        Binance: GET /fapi/v1/income

        Fetches FUNDING_FEE and COMMISSION income history.
        - FUNDING_FEE: income > 0 → funding_received; income < 0 → fees_paid (funding paid)
        - COMMISSION: income is always negative → fees_paid += abs(income)

        Uses fapiPrivateGetIncome for direct Binance API access.

        Returns:
            Dict with 'funding_received' and 'fees_paid' keys
        """
        try:
            binance_symbol = self._to_binance_symbol(symbol)

            funding_received = 0.0
            fees_paid = 0.0

            # --- Fetch FUNDING_FEE records ---
            try:
                funding_params = {
                    'symbol': binance_symbol,
                    'incomeType': 'FUNDING_FEE',
                    'limit': limit,
                }
                if start_time:
                    funding_params['startTime'] = start_time
                if end_time:
                    funding_params['endTime'] = end_time

                funding_records = await self.client.fapiPrivateGetIncome(funding_params)

                for record in (funding_records or []):
                    income = float(record.get('income', 0))
                    if income > 0:
                        funding_received += income
                    else:
                        # Funding paid (negative income)
                        fees_paid += abs(income)

            except Exception as e:
                log.warning(f"Binance: Failed to fetch funding income: {e}")

            # --- Fetch COMMISSION records ---
            try:
                commission_params = {
                    'symbol': binance_symbol,
                    'incomeType': 'COMMISSION',
                    'limit': limit,
                }
                if start_time:
                    commission_params['startTime'] = start_time
                if end_time:
                    commission_params['endTime'] = end_time

                commission_records = await self.client.fapiPrivateGetIncome(commission_params)

                for record in (commission_records or []):
                    income = float(record.get('income', 0))
                    # Commission is always negative (fee paid)
                    fees_paid += abs(income)

            except Exception as e:
                log.warning(f"Binance: Failed to fetch commission income: {e}")

            return {
                'funding_received': funding_received,
                'fees_paid': fees_paid,
            }

        except Exception as e:
            log.error(f"Binance: get_income_history failed: {e}")
            return {'funding_received': 0.0, 'fees_paid': 0.0}

    # ============================================
    # PARSERS (convert Binance format to our types)
    # ============================================

    def _parse_position(self, data: Dict[str, Any]) -> Position:
        """
        Convert Binance position data to Position object.

        ccxt normalizes Binance position data:
        - contracts: quantity in base currency (Binance: contracts == base qty)
        - side: 'long' or 'short' (normalized by ccxt)
        - entryPrice, markPrice, notional, unrealizedPnl, etc.

        Note: For accurate funding/fees, use get_income_history() separately.
        """
        symbol = data.get('symbol', '')
        contracts = float(data.get('contracts', 0) or 0)
        side = data.get('side', '')  # 'long' or 'short' (ccxt normalized)
        entry_price = float(data.get('entryPrice', 0) or 0)
        notional = float(data.get('notional', 0) or 0)

        # Binance: contracts == quantity in base currency (no conversion needed)
        quantity = abs(contracts)

        # For exact funding/fees, use get_income_history() separately
        funding_received = 0.0
        fees_paid = 0.0

        # Initial capital (position value at entry)
        initial_capital = notional if notional > 0 else quantity * entry_price

        return Position(
            id=f"binance_{symbol}_{int(datetime.utcnow().timestamp())}",
            pair=symbol,
            exchange1=self.exchange_name.value,
            exchange1_pos_id=data.get('id', ''),
            exchange1_side=side.upper() if side else 'LONG',
            exchange1_entry_price=entry_price,
            exchange1_current_price=float(data.get('markPrice', 0) or 0),
            exchange1_leverage=int(data.get('leverage', 1) or 1),
            # Single exchange position - exchange2 fields empty
            exchange2='',
            exchange2_pos_id='',
            exchange2_side='',
            exchange2_entry_price=0,
            exchange2_current_price=0,
            exchange2_leverage=1,
            quantity=quantity,
            entry_time=datetime.utcnow().timestamp(),
            stop_loss_price=float(data.get('stopLossPrice', 0) or 0),
            take_profit_price=float(data.get('takeProfitPrice', 0) or 0),
            liquidation_price_ex1=float(data.get('liquidationPrice', 0) or 0),
            liquidation_price_ex2=0,
            status='OPEN' if contracts != 0 else 'CLOSED',
            unrealized_pnl=float(data.get('unrealizedPnl', 0) or 0),
            initial_capital=initial_capital,
            funding_received=funding_received,
            fees_paid=fees_paid,
        )

    def _parse_order(self, data: Dict[str, Any]) -> Order:
        """Convert Binance order data to Order object"""
        # Handle None values safely
        side = data.get('side') or ''
        order_type = data.get('type') or ''
        status = data.get('status') or 'open'

        return Order(
            id=data.get('id', '') or '',
            symbol=data.get('symbol', '') or '',
            exchange=self.exchange_name.value,
            side=side.upper() if side else '',
            order_type=order_type.upper() if order_type else '',
            quantity=float(data.get('amount', 0) or 0),
            price=float(data.get('price', 0) or 0),
            filled_quantity=float(data.get('filled', 0) or 0),
            status=status.upper() if status else 'OPEN',
        )

    def _parse_orderbook(self, data: Dict[str, Any]) -> OrderBook:
        """Convert Binance orderbook to OrderBook object"""
        return OrderBook(
            symbol=data.get('symbol', ''),
            exchange=self.exchange_name.value,
            bids=[(float(price), float(qty)) for price, qty in data.get('bids', [])],
            asks=[(float(price), float(qty)) for price, qty in data.get('asks', [])],
            timestamp=data.get('timestamp', datetime.utcnow().timestamp())
        )

    def _parse_price_data(self, data: Dict[str, Any]) -> PriceData:
        """Convert Binance ticker to PriceData"""
        # Handle timestamp - may be None on testnet
        ts = data.get('timestamp')
        if ts is None:
            ts = datetime.utcnow().timestamp() * 1000

        return PriceData(
            symbol=data.get('symbol', ''),
            bid=float(data.get('bid', 0) or 0),
            ask=float(data.get('ask', 0) or 0),
            bid_qty=float(data.get('bid_qty', 0) or 0),
            ask_qty=float(data.get('ask_qty', 0) or 0),
            timestamp=float(ts) / 1000
        )

    def _parse_balance(self, data: Dict[str, Any]) -> Balance:
        """
        Convert Binance balance to Balance object.

        Binance Futures balance (GET /fapi/v3/balance):
        - balance: total balance
        - availableBalance: available for trading
        - crossUnPnl: unrealized PnL

        ccxt normalizes this into standard format with USDT key.
        """
        # ccxt normalizes balance - look for USDT
        usdt = data.get('USDT', data.get('info', {}).get('USDT', {}))

        if isinstance(usdt, dict):
            total = float(usdt.get('total', 0) or 0)
            free = float(usdt.get('free', 0) or 0)
            used = float(usdt.get('used', 0) or 0)
        else:
            total = float(usdt or 0)
            free = total
            used = 0

        return Balance(
            exchange=self.exchange_name.value,
            total=total,
            available=free,
            margin_used=used,
            unrealized_pnl=0,  # Would need separate call or parse from info
            timestamp=datetime.utcnow().timestamp()
        )

    def _parse_funding_rate(self, data: Dict[str, Any]) -> FundingRate:
        """
        Convert Binance funding data to FundingRate.

        Binance premiumIndex response:
        - lastFundingRate: e.g., 0.00038246 (3.82 bps)
        - nextFundingTime: always in milliseconds!

        Uses timezone-aware datetime for correct time calculations.
        """
        next_funding_ts = int(data.get('nextFundingTime', 0) or 0)
        rate = float(data.get('fundingRate', 0) or 0)

        # Convert timestamp to timezone-aware UTC datetime
        # Binance nextFundingTime is ALWAYS in milliseconds
        if next_funding_ts:
            # Safety check: if > year 2100 in seconds, it's milliseconds
            if next_funding_ts > 4102444800:
                next_funding_time = datetime.fromtimestamp(
                    next_funding_ts / 1000, tz=timezone.utc
                )
            else:
                next_funding_time = datetime.fromtimestamp(
                    next_funding_ts, tz=timezone.utc
                )
        else:
            next_funding_time = datetime.now(timezone.utc)

        return FundingRate(
            symbol=data.get('symbol', ''),
            exchange=self.exchange_name.value,
            rate=rate,
            rate_bps=rate * 10000,
            next_funding_time=next_funding_time,
            timestamp=datetime.now(timezone.utc)
        )

    # ============================================
    # WEBSOCKET (to be implemented)
    # ============================================

    async def subscribe_orderbook(self, symbol: str, callback) -> None:
        """Subscribe to Binance orderbook WebSocket"""
        # TODO: Implement WebSocket subscription
        # Binance WS: wss://fstream.binance.com/ws/<symbol>@depth
        pass

    async def subscribe_position_updates(self, callback) -> None:
        """Subscribe to position updates via listenKey user data stream"""
        # TODO: Implement WebSocket subscription
        # Binance WS: wss://fstream.binance.com/ws/<listenKey>
        pass

    async def subscribe_order_updates(self, callback) -> None:
        """Subscribe to order updates via listenKey user data stream"""
        # TODO: Implement WebSocket subscription
        pass

    async def subscribe_account_updates(self, callback) -> None:
        """Subscribe to account updates via listenKey user data stream"""
        # TODO: Implement WebSocket subscription
        pass

    # ============================================
    # HELPER METHODS
    # ============================================

    async def get_server_time(self) -> int:
        """Get Binance server time (milliseconds)"""
        try:
            time_ms = await self.client.fetch_time()
            return time_ms
        except Exception as e:
            raise ExchangeError(f"Failed to get server time: {e}")

    async def sync_time(self) -> None:
        """Sync time with Binance server"""
        # ccxt handles this automatically with adjustForTimeDifference
        pass

    # ============================================
    # BINANCE-SPECIFIC METHODS
    # ============================================

    async def set_position_mode(self, hedge_mode: bool = False) -> bool:
        """
        Set position mode (One-Way or Hedge Mode)

        Args:
            hedge_mode: True for Hedge Mode (positionSide=LONG/SHORT),
                       False for One-Way Mode (positionSide=BOTH)

        API: POST /fapi/v1/positionSide/dual
        - dualSidePosition=true  → Hedge Mode
        - dualSidePosition=false → One-Way Mode

        Error -4059 "No need to change position side" is silently ignored.
        """
        try:
            await self.client.fapiPrivatePostPositionSideDual({
                'dualSidePosition': 'true' if hedge_mode else 'false'
            })
            return True
        except Exception as e:
            err_msg = str(e).lower()
            if 'no need' in err_msg or 'not changed' in err_msg:
                return True
            raise ExchangeError(f"Failed to set position mode: {e}")

    async def get_position_mode(self) -> bool:
        """
        Get current position mode

        API: GET /fapi/v1/positionSide/dual

        Returns:
            True if Hedge Mode (dual), False if One-Way Mode
        """
        try:
            response = await self.client.fapiPrivateGetPositionSideDual()
            return response.get('dualSidePosition', False)
        except Exception as e:
            raise ExchangeError(f"Failed to get position mode: {e}")

    async def set_margin_type_for_symbol(
        self,
        symbol: str,
        margin_type: str = 'ISOLATED'
    ) -> bool:
        """
        Set margin type for a specific symbol

        API: POST /fapi/v1/marginType
        - marginType: "ISOLATED" or "CROSSED" (Binance uses CROSSED, not CROSS!)

        Error -4046 "No need to change margin type" is silently ignored.

        Args:
            symbol: Trading pair (e.g., BTCUSDT)
            margin_type: "ISOLATED" or "CROSSED"
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            mode = 'isolated' if margin_type == 'ISOLATED' else 'cross'
            await self.client.set_margin_mode(mode, ccxt_symbol)
            return True
        except Exception as e:
            err_msg = str(e).lower()
            if 'no need' in err_msg or 'not changed' in err_msg:
                return True
            raise ExchangeError(f"Failed to set margin type: {e}")
