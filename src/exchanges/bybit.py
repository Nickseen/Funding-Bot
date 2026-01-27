"""
Bybit Futures Exchange Adapter

This adapter implements only the exchange-specific API calls.
All validation, logging, and error handling is done in BaseExchange.

Each method is 5-15 lines of pure API calls - no business logic!

Bybit API Reference:
- Futures (Linear): https://bybit-exchange.github.io/docs/v5/intro
- Uses Unified Trading Account (UTA) by default
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
import ccxt.async_support as ccxt

from .base import BaseExchange, ExchangeError, RateLimitError, NetworkError
from .enums import Exchange, PositionSide, OrderSide, OrderType
from .types import Position, Order, OrderBook, PriceData, Balance, FundingRate


class BybitExchange(BaseExchange):
    """
    Bybit Futures adapter (Linear Perpetual - USDT settled)
    
    Implements ONLY:
    1. _api_* methods (raw API calls)
    2. _parse_* methods (convert Bybit response format to our types)
    
    All business logic is in BaseExchange!
    
    Notes:
    - Bybit uses Unified Trading Account (UTA) by default
    - Symbol format: BTCUSDT (same as Binance)
    - Position mode: One-way mode by default (can be set to Hedge mode)
    """
    
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        testnet: bool = False
    ):
        super().__init__(api_key, secret_key, testnet=testnet)
        self.exchange_name = Exchange.BYBIT
        
        # Initialize ccxt client
        self.client = ccxt.bybit({
            'apiKey': api_key,
            'secret': secret_key,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'linear',  # Linear perpetual (USDT-settled)
                'adjustForTimeDifference': True,
                'recvWindow': 60000,  # Increase receive window to 60 seconds
                'timeDifference': 0,  # Will be auto-adjusted
            }
        })
        
        if testnet:
            self.client.set_sandbox_mode(True)
    
    # ============================================
    # CONNECTION
    # ============================================
    
    async def connect(self) -> bool:
        """Connect to Bybit"""
        try:
            # First, sync time with server
            server_time = await self.client.fetch_time()
            local_time = self.client.milliseconds()
            time_diff = server_time - local_time
            
            # Adjust client time difference
            self.client.options['timeDifference'] = time_diff
            
            # Now load markets
            await self.client.load_markets()
            self.connected = True
            return True
        except Exception as e:
            raise ExchangeError(f"Failed to connect: {e}")
    
    async def disconnect(self) -> None:
        """Disconnect from Bybit"""
        await self.client.close()
        self.connected = False
    
    async def test_connection(self) -> bool:
        """Test connection"""
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
        Convert simple symbol format to Bybit ccxt format
        
        BTCUSDT -> BTC/USDT:USDT (linear perpetual)
        """
        # Already in correct format
        if '/' in symbol:
            return symbol
        
        # Convert BTCUSDT -> BTC/USDT:USDT
        # Find where base ends and quote begins
        quote_currencies = ['USDT', 'USDC', 'USD']
        for quote in quote_currencies:
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                return f"{base}/{quote}:{quote}"
        
        # Fallback - return as is
        return symbol
    
    # ============================================
    # API ADAPTERS (pure API calls, no validation!)
    # ============================================
    
    async def _api_get_orderbook(self, symbol: str, limit: int) -> Dict[str, Any]:
        """Bybit: GET /v5/market/orderbook"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            return await self.client.fetch_order_book(ccxt_symbol, limit)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
    
    async def _api_get_price_data(self, symbol: str) -> Dict[str, Any]:
        """Bybit: GET /v5/market/tickers"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            ticker = await self.client.fetch_ticker(ccxt_symbol)
            return {
                'symbol': symbol,
                'bid': ticker['bid'],
                'ask': ticker['ask'],
                'bid_qty': ticker.get('bidVolume', 0),
                'ask_qty': ticker.get('askVolume', 0),
                'timestamp': ticker['timestamp']
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_mark_price(self, symbol: str) -> float:
        """Bybit: GET /v5/market/tickers - mark price from ticker"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            ticker = await self.client.fetch_ticker(ccxt_symbol)
            # Bybit returns mark price in the info field
            if 'info' in ticker and 'markPrice' in ticker['info']:
                return float(ticker['info']['markPrice'])
            # Fallback to last price
            return float(ticker['last'])
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
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
        Bybit: Open position
        
        Steps:
        1. Set leverage
        2. Place order
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            
            # 1. Set isolated margin mode
            try:
                await self.client.set_margin_mode('isolated', ccxt_symbol)
            except Exception as e:
                # Ignore if already set or not supported
                if 'not modified' not in str(e).lower() and 'same' not in str(e).lower():
                    pass
            
            # 2. Set leverage (ignore "not modified" error)
            try:
                await self.client.set_leverage(leverage, ccxt_symbol)
            except Exception as e:
                if 'not modified' not in str(e).lower():
                    raise
            
            # 2. Place order
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
            
            # 3. Get position info
            positions = await self.client.fetch_positions([ccxt_symbol])
            position_data = next(
                (p for p in positions if p['symbol'] == ccxt_symbol and float(p['contracts'] or 0) != 0),
                None
            )
            
            return position_data or order
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.InsufficientFunds as e:
            raise ExchangeError(f"Insufficient balance: {e}")
    
    async def _api_close_position(
        self,
        symbol: str,
        order_type: OrderType,
        price: Optional[float]
    ) -> Dict[str, Any]:
        """
        Bybit: Close position
        
        Get current position, place opposite order with reduceOnly=True
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            
            # 1. Get current position
            positions = await self.client.fetch_positions([ccxt_symbol])
            position = next(
                (p for p in positions if p['symbol'] == ccxt_symbol and float(p['contracts'] or 0) != 0),
                None
            )
            
            if not position:
                raise ExchangeError(f"No position found for {symbol}")
            
            contracts = float(position['contracts'])
            position_side = position['side']  # 'long' or 'short'
            
            # 2. Place opposite order
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = 'sell' if position_side == 'long' else 'buy'
            
            params = {'reduceOnly': True}
            
            if order_type == OrderType.LIMIT:
                await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=abs(contracts),
                    price=price,
                    params=params
                )
            else:
                await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=abs(contracts),
                    params=params
                )
            
            # 3. Return updated position (should be closed now)
            positions = await self.client.fetch_positions([ccxt_symbol])
            return next(
                (p for p in positions if p['symbol'] == ccxt_symbol),
                {'symbol': symbol, 'contracts': 0, 'side': None}
            )
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float],
        reduce_only: bool
    ) -> Dict[str, Any]:
        """Bybit: POST /v5/order/create"""
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
    
    async def _api_cancel_order(self, order_id: str, symbol: str) -> bool:
        """Bybit: POST /v5/order/cancel"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            await self.client.cancel_order(order_id, ccxt_symbol)
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_set_leverage(self, symbol: str, leverage: int) -> bool:
        """Bybit: POST /v5/position/set-leverage"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            await self.client.set_leverage(leverage, ccxt_symbol)
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            # Bybit may return error if leverage is already set
            if 'leverage not modified' in str(e).lower():
                return True
            raise ExchangeError(f"Failed to set leverage: {e}")
    
    async def _api_set_margin_mode(self, mode: str) -> bool:
        """Bybit: POST /v5/position/set-margin-mode"""
        try:
            # Bybit uses 'REGULAR_MARGIN' for cross and 'ISOLATED_MARGIN' for isolated
            # Or we can use set_margin_mode on ccxt
            margin_mode = 'cross' if mode == 'CROSS' else 'isolated'
            await self.client.set_margin_mode(margin_mode)
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            # May already be set
            if 'not modified' in str(e).lower():
                return True
            raise ExchangeError(f"Failed to set margin mode: {e}")
    
    async def _api_set_stop_loss(
        self,
        symbol: str,
        side: 'PositionSide',
        stop_price: float,
        quantity: Optional[float]
    ) -> Dict[str, Any]:
        """
        Bybit: POST /v5/position/trading-stop
        
        Sets stop loss directly on the position (not a separate trigger order)
        """
        try:
            from .enums import PositionSide
            ccxt_symbol = self._convert_symbol(symbol)
            
            # Get symbol in Bybit format (BTCUSDT)
            bybit_symbol = ccxt_symbol.replace('/', '').replace(':USDT', '')
            
            # positionIdx: 0 = one-way mode, 1 = hedge-mode Buy, 2 = hedge-mode Sell
            position_idx = 0  # One-way mode
            
            # Use Bybit's trading-stop API to set SL on position
            response = await self.client.private_post_v5_position_trading_stop({
                'category': 'linear',
                'symbol': bybit_symbol,
                'stopLoss': str(stop_price),
                'slTriggerBy': 'MarkPrice',
                'positionIdx': position_idx,
            })
            
            return response
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to set position stop loss: {e}")
    
    async def _api_set_take_profit(
        self,
        symbol: str,
        side: 'PositionSide',
        take_profit_price: float,
        quantity: Optional[float]
    ) -> Dict[str, Any]:
        """
        Bybit: POST /v5/position/trading-stop
        
        Sets take profit directly on the position (not a separate trigger order)
        """
        try:
            from .enums import PositionSide
            ccxt_symbol = self._convert_symbol(symbol)
            
            # Get symbol in Bybit format (BTCUSDT)
            bybit_symbol = ccxt_symbol.replace('/', '').replace(':USDT', '')
            
            # positionIdx: 0 = one-way mode
            position_idx = 0
            
            # Use Bybit's trading-stop API to set TP on position
            response = await self.client.private_post_v5_position_trading_stop({
                'category': 'linear',
                'symbol': bybit_symbol,
                'takeProfit': str(take_profit_price),
                'tpTriggerBy': 'MarkPrice',
                'positionIdx': position_idx,
            })
            
            return response
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to set position take profit: {e}")
    
    async def _api_get_balance(self) -> Dict[str, Any]:
        """Bybit: GET /v5/account/wallet-balance"""
        try:
            balance = await self.client.fetch_balance()
            return balance
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_positions(self, symbol: Optional[str]) -> List[Dict[str, Any]]:
        """Bybit: GET /v5/position/list"""
        try:
            # Re-sync time before fetching positions to avoid timestamp errors
            try:
                server_time = await self.client.fetch_time()
                local_time = self.client.milliseconds()
                time_diff = server_time - local_time
                self.client.options['timeDifference'] = time_diff
            except:
                pass  # Continue even if re-sync fails
            
            if symbol:
                ccxt_symbol = self._convert_symbol(symbol)
                positions = await self.client.fetch_positions([ccxt_symbol])
            else:
                positions = await self.client.fetch_positions()
            
            # Filter out zero positions
            return [p for p in positions if float(p.get('contracts', 0) or 0) != 0]
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_position_by_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """Bybit: GET /v5/position/list for specific symbol"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            positions = await self.client.fetch_positions([ccxt_symbol])
            position = next(
                (p for p in positions if float(p.get('contracts', 0) or 0) != 0),
                None
            )
            return position
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_account_info(self) -> Dict[str, Any]:
        """Bybit: GET /v5/account/info"""
        try:
            # ccxt doesn't have a direct method, use private API
            return await self.client.private_get_v5_account_info()
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception:
            # Fallback to balance info
            return await self._api_get_balance()
    
    async def _api_get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Bybit: GET /v5/market/instruments-info"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            markets = self.client.markets
            if ccxt_symbol not in markets:
                await self.client.load_markets(True)  # Reload
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
                'max_leverage': 100,  # Bybit max for most pairs
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        """Bybit: GET /v5/market/tickers - funding info"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            # Use fetch_funding_rate for accurate funding time
            funding = await self.client.fetch_funding_rate(ccxt_symbol)
            
            # Bybit uses 'fundingTimestamp' not 'nextFundingTimestamp'
            next_funding_ts = funding.get('fundingTimestamp') or funding.get('nextFundingTimestamp', 0)
            
            return {
                'symbol': symbol,
                'fundingRate': funding.get('fundingRate', 0),
                'nextFundingTime': next_funding_ts,
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    # ============================================
    # PARSERS (convert Bybit format to our types)
    # ============================================
    
    def _parse_position(self, data: Dict[str, Any]) -> Position:
        """Convert Bybit position data to Position object"""
        # ccxt normalizes position data
        symbol = data.get('symbol', '')
        contracts = float(data.get('contracts', 0) or 0)
        side = data.get('side', '')  # 'long' or 'short'
        
        return Position(
            id=f"bybit_{symbol}_{int(datetime.utcnow().timestamp())}",
            pair=symbol,
            exchange1=self.exchange_name.value,
            exchange1_pos_id=data.get('id', ''),
            exchange1_side=side.upper() if side else 'LONG',
            exchange1_entry_price=float(data.get('entryPrice', 0) or 0),
            exchange1_current_price=float(data.get('markPrice', 0) or 0),
            exchange1_leverage=int(data.get('leverage', 1) or 1),
            # For single exchange position, duplicate fields
            exchange2='',
            exchange2_pos_id='',
            exchange2_side='',
            exchange2_entry_price=0,
            exchange2_current_price=0,
            exchange2_leverage=1,
            quantity=abs(contracts),
            entry_time=datetime.utcnow().timestamp(),
            stop_loss_price=float(data.get('stopLossPrice', 0) or 0),
            take_profit_price=float(data.get('takeProfitPrice', 0) or 0),
            liquidation_price_ex1=float(data.get('liquidationPrice', 0) or 0),
            liquidation_price_ex2=0,
            status='OPEN' if contracts != 0 else 'CLOSED',
            unrealized_pnl=float(data.get('unrealizedPnl', 0) or 0),
        )
    
    def _parse_order(self, data: Dict[str, Any]) -> Order:
        """Convert Bybit order data to Order object"""
        from .types import Order
        
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
        """Convert Bybit orderbook to OrderBook object"""
        return OrderBook(
            symbol=data.get('symbol', ''),
            exchange=self.exchange_name.value,
            bids=[(float(price), float(qty)) for price, qty in data.get('bids', [])],
            asks=[(float(price), float(qty)) for price, qty in data.get('asks', [])],
            timestamp=data.get('timestamp', datetime.utcnow().timestamp())
        )
    
    def _parse_price_data(self, data: Dict[str, Any]) -> PriceData:
        """Convert Bybit ticker to PriceData"""
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
        """Convert Bybit balance to Balance object"""
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
            unrealized_pnl=0,  # Would need separate call
            timestamp=datetime.utcnow().timestamp()
        )
    
    def _parse_funding_rate(self, data: Dict[str, Any]) -> FundingRate:
        """
        Convert Bybit funding data to FundingRate.
        
        Uses timezone-aware datetime to ensure correct time calculations.
        """
        from datetime import timezone
        
        next_funding_ts = int(data.get('nextFundingTime', 0) or 0)
        rate = float(data.get('fundingRate', 0) or 0)
        
        # Convert timestamp to timezone-aware UTC datetime
        # Check if timestamp is in seconds or milliseconds
        if next_funding_ts:
            # If > year 2100 in seconds (4102444800), it's likely milliseconds
            if next_funding_ts > 4102444800:
                next_funding_time = datetime.fromtimestamp(next_funding_ts / 1000, tz=timezone.utc)
            else:
                # Already in seconds
                next_funding_time = datetime.fromtimestamp(next_funding_ts, tz=timezone.utc)
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
        """Subscribe to Bybit orderbook WebSocket"""
        # TODO: Implement WebSocket subscription
        # Bybit WS: wss://stream.bybit.com/v5/public/linear
        pass
    
    async def subscribe_position_updates(self, callback) -> None:
        """Subscribe to position updates"""
        # TODO: Implement WebSocket subscription
        pass
    
    async def subscribe_order_updates(self, callback) -> None:
        """Subscribe to order updates"""
        # TODO: Implement WebSocket subscription
        pass
    
    async def subscribe_account_updates(self, callback) -> None:
        """Subscribe to account updates"""
        # TODO: Implement WebSocket subscription
        pass
    
    # ============================================
    # HELPER METHODS
    # ============================================
    
    async def get_server_time(self) -> int:
        """Get Bybit server time"""
        try:
            time = await self.client.fetch_time()
            return time
        except Exception as e:
            raise ExchangeError(f"Failed to get server time: {e}")
    
    async def sync_time(self) -> None:
        """Sync time with Bybit"""
        # ccxt handles this automatically with adjustForTimeDifference
        pass
    
    # ============================================
    # BYBIT-SPECIFIC METHODS
    # ============================================
    
    async def set_position_mode(self, hedge_mode: bool = False) -> bool:
        """
        Set position mode
        
        Args:
            hedge_mode: True for hedge mode (dual positions), False for one-way mode
            
        Note: Bybit requires no open positions to change this
        """
        try:
            mode = 'BothSide' if hedge_mode else 'MergedSingle'
            await self.client.set_position_mode(hedge_mode, symbol=None)
            return True
        except Exception as e:
            if 'not modified' in str(e).lower():
                return True
            raise ExchangeError(f"Failed to set position mode: {e}")
