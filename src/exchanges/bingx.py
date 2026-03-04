"""
BingX Futures Exchange Adapter

This adapter implements only the exchange-specific API calls.
All validation, logging, and error handling is done in BaseExchange.

Each method is 5-15 lines of pure API calls - no business logic!

BingX API Reference:
- Futures (Linear): https://bingx-api.github.io/docs/
- Uses USDT-M Perpetual Futures

BingX Notes:
- Symbol format: BTC-USDT (with dash for API), BTC/USDT:USDT (ccxt)
- Supports one-way mode and hedge mode
- Contract size: varies by symbol (check contractSize)
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
import ccxt.async_support as ccxt

from .base import BaseExchange, ExchangeError, RateLimitError, NetworkError
from .enums import Exchange, PositionSide, OrderSide, OrderType
from .types import Position, Order, OrderBook, PriceData, Balance, FundingRate


class BingXExchange(BaseExchange):
    """
    BingX Futures adapter (USDT-M Perpetual)
    
    Implements ONLY:
    1. _api_* methods (raw API calls)
    2. _parse_* methods (convert BingX response format to our types)
    
    All business logic is in BaseExchange!
    
    Notes:
    - BingX uses "swap" for perpetual futures in ccxt
    - Symbol format: BTC-USDT (native), BTC/USDT:USDT (ccxt)
    - Position mode: One-way by default
    - Leverage: up to 150x on major pairs
    """
    
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        testnet: bool = False
    ):
        super().__init__(api_key, secret_key, testnet=testnet)
        self.exchange_name = Exchange.BINGX
        
        # Initialize ccxt client
        self.client = ccxt.bingx({
            'apiKey': api_key,
            'secret': secret_key,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',  # Perpetual futures
                'adjustForTimeDifference': True,
                'recvWindow': 60000,  # 60 seconds receive window
                'timeDifference': 0,
            }
        })
        
        # BingX has demo trading mode, not traditional testnet
        if testnet:
            self.client.set_sandbox_mode(True)
    
    # ============================================
    # CONNECTION
    # ============================================
    
    async def connect(self) -> bool:
        """Connect to BingX"""
        try:
            # Sync time with server first
            server_time = await self.client.fetch_time()
            local_time = self.client.milliseconds()
            time_diff = server_time - local_time
            self.client.options['timeDifference'] = time_diff
            
            await self.client.load_markets()
            self.connected = True
            return True
        except Exception as e:
            raise ExchangeError(f"Failed to connect: {e}")
    
    async def disconnect(self) -> None:
        """Disconnect from BingX"""
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
        Convert simple symbol format to BingX ccxt format
        
        BTCUSDT -> BTC/USDT:USDT (perpetual swap)
        """
        # Already in correct format
        if '/' in symbol:
            return symbol
        
        # Convert BTCUSDT -> BTC/USDT:USDT
        quote_currencies = ['USDT', 'USDC', 'USD']
        for quote in quote_currencies:
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                return f"{base}/{quote}:{quote}"
        
        # Fallback - return as is
        return symbol
    
    def _to_bingx_symbol(self, symbol: str) -> str:
        """
        Convert to BingX native format
        
        BTCUSDT -> BTC-USDT
        BTC/USDT:USDT -> BTC-USDT
        """
        # If ccxt format, convert to native
        if '/' in symbol:
            base_quote = symbol.split(':')[0]  # BTC/USDT
            base, quote = base_quote.split('/')
            return f"{base}-{quote}"
        
        # If simple format (BTCUSDT), convert
        quote_currencies = ['USDT', 'USDC', 'USD']
        for quote in quote_currencies:
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                return f"{base}-{quote}"
        
        return symbol
    
    # ============================================
    # API ADAPTERS (pure API calls, no validation!)
    # ============================================
    
    async def _api_get_orderbook(self, symbol: str, limit: int) -> Dict[str, Any]:
        """BingX: GET /openApi/swap/v2/quote/depth"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            return await self.client.fetch_order_book(ccxt_symbol, limit)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
    
    async def _api_get_price_data(self, symbol: str) -> Dict[str, Any]:
        """BingX: GET /openApi/swap/v2/quote/ticker"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            ticker = await self.client.fetch_ticker(ccxt_symbol)
            # For futures tickers bid/ask may be None — fall back to last traded price
            last_price = ticker.get('last') or 0
            return {
                'symbol': symbol,
                'bid': ticker.get('bid') or last_price,
                'ask': ticker.get('ask') or last_price,
                'bid_qty': ticker.get('bidVolume') or 0,
                'ask_qty': ticker.get('askVolume') or 0,
                'timestamp': ticker.get('timestamp') or int(datetime.utcnow().timestamp() * 1000)
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_mark_price(self, symbol: str) -> float:
        """BingX: GET mark price from ticker"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            ticker = await self.client.fetch_ticker(ccxt_symbol)
            
            # Try to get mark price from info
            info = ticker.get('info', {})
            if 'markPrice' in info:
                return float(info['markPrice'])
            
            # Fallback to last price
            return float(ticker.get('last', 0))
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
        BingX: Open position
        
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
                if 'same' not in str(e).lower() and 'not modified' not in str(e).lower():
                    pass
            
            # 2. Set leverage for the position side we're opening
            position_side_str = 'LONG' if side == PositionSide.LONG else 'SHORT'
            try:
                await self.client.set_leverage(leverage, ccxt_symbol, params={'side': position_side_str})
            except Exception as e:
                if 'same' not in str(e).lower() and 'not modified' not in str(e).lower():
                    raise
            
            # 2. Place order
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = 'buy' if side == PositionSide.LONG else 'sell'
            
            # BingX requires positionSide in hedge mode
            params = {
                'positionSide': position_side_str
            }
            
            if order_type == OrderType.LIMIT and price:
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
                (p for p in positions if float(p.get('contracts', 0) or 0) != 0),
                None
            )
            
            return position_data or order
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.InsufficientFunds as e:
            raise ExchangeError(f"Insufficient balance: {e}")
        except Exception as e:
            raise ExchangeError(f"Failed to open position: {e}")
    
    async def _api_close_position(
        self,
        symbol: str,
        order_type: OrderType,
        price: Optional[float]
    ) -> Dict[str, Any]:
        """
        BingX: Close position
        
        Get current position, place opposite order with reduceOnly=True
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            
            # 1. Get current position
            positions = await self.client.fetch_positions([ccxt_symbol])
            position = next(
                (p for p in positions if float(p.get('contracts', 0) or 0) != 0),
                None
            )
            
            if not position:
                raise ExchangeError(f"No position found for {symbol}")
            
            contracts = float(position['contracts'])
            position_side = position.get('side', '')  # 'long' or 'short'
            
            # 2. Place opposite order
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = 'sell' if position_side == 'long' else 'buy'
            
            # BingX hedge mode: don't use reduceOnly, use positionSide instead
            params = {
                'positionSide': 'LONG' if position_side == 'long' else 'SHORT'
            }
            
            if order_type == OrderType.LIMIT and price:
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
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to close position: {e}")
    
    async def _api_place_order(
        self,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: Optional[float],
        reduce_only: bool
    ) -> Dict[str, Any]:
        """BingX: POST /openApi/swap/v2/trade/order"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = side.value.lower()
            
            # BingX hedge mode requires positionSide
            # BUY opens LONG, SELL opens SHORT
            # If reduce_only: BUY closes SHORT, SELL closes LONG
            if reduce_only:
                position_side = 'SHORT' if order_side == 'buy' else 'LONG'
            else:
                position_side = 'LONG' if order_side == 'buy' else 'SHORT'
            
            params = {
                'positionSide': position_side
            }
            # BingX hedge mode: don't use reduceOnly, positionSide handles direction
            
            if order_type == OrderType.LIMIT and price:
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
        except Exception as e:
            raise ExchangeError(f"Failed to place order: {e}")
    
    async def _api_cancel_order(self, order_id: str, symbol: str) -> bool:
        """BingX: DELETE /openApi/swap/v2/trade/order"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            await self.client.cancel_order(order_id, ccxt_symbol)
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to cancel order: {e}")
    
    async def _api_set_leverage(self, symbol: str, leverage: int) -> bool:
        """BingX: POST /openApi/swap/v2/trade/leverage"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            # BingX in hedge mode requires setting leverage for both LONG and SHORT separately
            try:
                await self.client.set_leverage(leverage, ccxt_symbol, params={'side': 'LONG'})
            except Exception as e:
                if 'same' not in str(e).lower() and 'not modified' not in str(e).lower():
                    pass  # Continue to try SHORT
            
            try:
                await self.client.set_leverage(leverage, ccxt_symbol, params={'side': 'SHORT'})
            except Exception as e:
                if 'same' not in str(e).lower() and 'not modified' not in str(e).lower():
                    pass  # Both may already be set
            
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            # BingX may return error if leverage is already set
            if 'same' in str(e).lower() or 'not modified' in str(e).lower():
                return True
            raise ExchangeError(f"Failed to set leverage: {e}")
    
    async def _api_set_margin_mode(self, mode: str) -> bool:
        """BingX: Set margin mode (cross or isolated)"""
        try:
            margin_mode = 'cross' if mode.upper() == 'CROSS' else 'isolated'
            await self.client.set_margin_mode(margin_mode)
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            if 'not modified' in str(e).lower() or 'same' in str(e).lower():
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
        BingX: Set stop loss order using CCXT createStopLossOrder
        """
        try:
            from .enums import PositionSide
            ccxt_symbol = self._convert_symbol(symbol)
            
            # Get position to determine size and side
            positions = await self.client.fetch_positions([ccxt_symbol])
            position = next(
                (p for p in positions if float(p.get('contracts', 0) or 0) != 0),
                None
            )
            
            if not position:
                raise ExchangeError(f"No position found for {symbol}")
            
            contracts = abs(float(position.get('contracts', 0)))
            pos_side = position.get('side', '')  # 'long' or 'short'
            
            # For SL: if LONG, sell when price falls; if SHORT, buy when price rises
            order_side = 'sell' if pos_side == 'long' else 'buy'
            
            # BingX hedge mode requires positionSide
            position_side_param = 'LONG' if pos_side == 'long' else 'SHORT'
            
            # Use CCXT's createStopLossOrder
            # BingX hedge mode: don't use reduceOnly, positionSide is sufficient
            response = await self.client.create_stop_loss_order(
                symbol=ccxt_symbol,
                type='market',
                side=order_side,
                amount=contracts,
                stopLossPrice=stop_price,
                params={
                    'positionSide': position_side_param,
                }
            )
            
            return {
                'success': True,
                'order_id': response.get('id'),
                'type': 'stop_loss',
                'trigger_price': stop_price,
                'info': response
            }
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to set stop loss: {e}")
    
    async def _api_set_take_profit(
        self,
        symbol: str,
        side: 'PositionSide',
        take_profit_price: float,
        quantity: Optional[float]
    ) -> Dict[str, Any]:
        """
        BingX: Set take profit order using CCXT createTakeProfitOrder
        """
        try:
            from .enums import PositionSide
            ccxt_symbol = self._convert_symbol(symbol)
            
            # Get position to determine size and side
            positions = await self.client.fetch_positions([ccxt_symbol])
            position = next(
                (p for p in positions if float(p.get('contracts', 0) or 0) != 0),
                None
            )
            
            if not position:
                raise ExchangeError(f"No position found for {symbol}")
            
            contracts = abs(float(position.get('contracts', 0)))
            pos_side = position.get('side', '')  # 'long' or 'short'
            
            # For TP: if LONG, sell when price rises; if SHORT, buy when price falls
            order_side = 'sell' if pos_side == 'long' else 'buy'
            
            # BingX hedge mode requires positionSide
            position_side_param = 'LONG' if pos_side == 'long' else 'SHORT'
            
            # Use CCXT's createTakeProfitOrder
            # BingX hedge mode: don't use reduceOnly, positionSide is sufficient
            response = await self.client.create_take_profit_order(
                symbol=ccxt_symbol,
                type='market',
                side=order_side,
                amount=contracts,
                takeProfitPrice=take_profit_price,
                params={
                    'positionSide': position_side_param,
                }
            )
            
            return {
                'success': True,
                'order_id': response.get('id'),
                'type': 'take_profit',
                'trigger_price': take_profit_price,
                'info': response
            }
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to set take profit: {e}")
    
    async def _api_get_balance(self) -> Dict[str, Any]:
        """BingX: GET /openApi/swap/v2/user/balance"""
        try:
            balance = await self.client.fetch_balance({'type': 'swap'})
            return balance
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to get balance: {e}")
    
    async def _api_get_positions(self, symbol: Optional[str]) -> List[Dict[str, Any]]:
        """BingX: GET /openApi/swap/v2/user/positions"""
        try:
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
        """BingX: GET position for specific symbol"""
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
        """BingX: GET account info"""
        try:
            return await self._api_get_balance()
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """BingX: GET /openApi/swap/v2/quote/contracts"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            markets = self.client.markets
            
            if ccxt_symbol not in markets:
                await self.client.load_markets(True)
                markets = self.client.markets
            
            market = markets.get(ccxt_symbol)
            if not market:
                raise ExchangeError(f"Symbol {symbol} not found")
            
            # Extract contract specifications
            limits = market.get('limits', {})
            precision = market.get('precision', {})
            info = market.get('info', {})
            
            return {
                'symbol': symbol,
                'contract_size': float(market.get('contractSize', 1) or 1),
                'min_quantity': float(limits.get('amount', {}).get('min', 0.001) or 0.001),
                'max_quantity': float(limits.get('amount', {}).get('max', 10000) or 10000),
                'quantity_step': float(precision.get('amount', 0.001) or 0.001),
                'min_price': float(limits.get('price', {}).get('min', 0) or 0),
                'price_tick': float(precision.get('price', 0.01) or 0.01),
                'max_leverage': int(info.get('maxLeverage', 150) or 150),
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to get symbol info: {e}")
    
    async def _api_get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        """BingX: GET funding rate"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            
            # Try to get funding rate from ticker or dedicated endpoint
            ticker = await self.client.fetch_ticker(ccxt_symbol)
            info = ticker.get('info', {})
            
            return {
                'symbol': symbol,
                'fundingRate': float(info.get('fundingRate', 0) or 0),
                'nextFundingTime': int(info.get('nextFundingTime', 0) or 0),
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            # Fallback - return defaults
            return {
                'symbol': symbol,
                'fundingRate': 0,
                'nextFundingTime': 0,
            }
    
    # ============================================
    # PARSERS (convert BingX format to our types)
    # ============================================
    
    def _parse_position(self, data: Dict[str, Any]) -> Position:
        """Convert BingX position data to Position object"""
        symbol = data.get('symbol', '')
        contracts = float(data.get('contracts', 0) or 0)
        side = data.get('side', '')  # 'long' or 'short'
        entry_price = float(data.get('entryPrice', 0) or 0)
        notional = float(data.get('notional', 0) or 0)  # Position value in USDT
        
        # Extract fees and funding from raw exchange data ('info' field)
        info = data.get('info', {})
        
        # BingX now extracts exact funding and fees from income history API
        # This is done via get_income_history() which should be called separately
        # For immediate parsing, we'll use approximation from realisedProfit
        # The CLI will update with exact values by calling get_income_history()
        
        funding_received = 0.0
        fees_paid = 0.0
        
        # Check for realized profit (combined value - approximate)
        if 'realisedProfit' in info and info['realisedProfit'] is not None:
            realised = float(info['realisedProfit'])
            # If negative, it's likely fees > funding (approximate)
            if realised < 0:
                fees_paid = abs(realised)
        
        # Initial capital (position value at entry)
        initial_capital = notional if notional > 0 else abs(contracts) * entry_price
        
        return Position(
            id=f"bingx_{symbol}_{int(datetime.utcnow().timestamp())}",
            pair=symbol,
            exchange1=self.exchange_name.value,
            exchange1_pos_id=data.get('id', ''),
            exchange1_side=side.upper() if side else 'LONG',
            exchange1_entry_price=entry_price,
            exchange1_current_price=float(data.get('markPrice', 0) or 0),
            exchange1_leverage=int(data.get('leverage', 1) or 1),
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
            initial_capital=initial_capital,
            funding_received=funding_received,
            fees_paid=fees_paid,
        )
    
    def _parse_order(self, data: Dict[str, Any]) -> Order:
        """Convert BingX order data to Order object"""
        from .types import Order
        
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
        """Convert BingX orderbook to OrderBook object"""
        return OrderBook(
            symbol=data.get('symbol', ''),
            exchange=self.exchange_name.value,
            bids=[(float(price), float(qty)) for price, qty in data.get('bids', [])],
            asks=[(float(price), float(qty)) for price, qty in data.get('asks', [])],
            timestamp=data.get('timestamp', datetime.utcnow().timestamp())
        )
    
    def _parse_price_data(self, data: Dict[str, Any]) -> PriceData:
        """Convert BingX ticker to PriceData"""
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
        """Convert BingX balance to Balance object"""
        # BingX uses different tokens for mainnet vs demo:
        # - Mainnet: USDT
        # - Demo: VST (Virtual Standard Token)
        token = 'VST' if self.testnet else 'USDT'
        
        # ccxt normalizes balance - look for the appropriate token
        balance_data = data.get(token, data.get('info', {}).get(token, {}))
        
        if isinstance(balance_data, dict):
            total = float(balance_data.get('total', 0) or 0)
            free = float(balance_data.get('free', 0) or 0)
            used = float(balance_data.get('used', 0) or 0)
        else:
            total = float(balance_data or 0)
            free = total
            used = 0
        
        return Balance(
            exchange=self.exchange_name.value,
            total=total,
            available=free,
            margin_used=used,
            unrealized_pnl=0,
            timestamp=datetime.utcnow().timestamp()
        )
    
    def _parse_funding_rate(self, data: Dict[str, Any]) -> FundingRate:
        """Convert BingX funding data to FundingRate"""
        from datetime import timezone, timedelta
        
        next_funding_ts = int(data.get('nextFundingTime', 0) or 0)
        rate = float(data.get('fundingRate', 0) or 0)
        
        now = datetime.now(timezone.utc)
        
        # Check if timestamp is in seconds or milliseconds
        if next_funding_ts:
            # If > year 2100 in seconds (4102444800), it's likely milliseconds
            if next_funding_ts > 4102444800:
                next_funding_time = datetime.fromtimestamp(next_funding_ts / 1000, tz=timezone.utc)
            else:
                # Already in seconds
                next_funding_time = datetime.fromtimestamp(next_funding_ts, tz=timezone.utc)
            
            # If funding time is in the past, calculate next occurrence (8h intervals)
            while next_funding_time < now:
                next_funding_time += timedelta(hours=8)
        else:
            # No funding time provided - estimate next 00:00, 08:00, or 16:00 UTC
            current_hour = now.hour
            if current_hour < 8:
                next_hour = 8
            elif current_hour < 16:
                next_hour = 16
            else:
                next_hour = 0  # Next day
            
            next_funding_time = now.replace(hour=next_hour, minute=0, second=0, microsecond=0)
            if next_hour == 0:
                next_funding_time += timedelta(days=1)
        
        return FundingRate(
            symbol=data.get('symbol', ''),
            exchange=self.exchange_name.value,
            rate=rate,
            rate_bps=rate * 10000,
            next_funding_time=next_funding_time,
            timestamp=datetime.now(timezone.utc)
        )
    
    async def _api_get_income_history(
        self, 
        symbol: str, 
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 100
    ) -> Dict[str, float]:
        """BingX: Get income history for funding and fees
        
        API: GET /openApi/swap/v2/user/income
        Docs: https://bingx-api.github.io/docs/#/en-us/swapV2/account-api
        
        Returns:
            Dict with 'funding_received' and 'fees_paid' keys
        """
        try:
            # Convert symbol format: BTCUSDT -> BTC-USDT
            bingx_symbol = symbol.replace('/', '-').replace(':USDT', '')
            if 'USDT' in bingx_symbol and '-' not in bingx_symbol:
                # BTCUSDT -> BTC-USDT
                bingx_symbol = bingx_symbol.replace('USDT', '-USDT')
            
            funding_received = 0.0
            fees_paid = 0.0
            
            # Fetch funding fees (FUNDING_FEE type)
            # Positive = received, Negative = paid
            try:
                funding_params = {
                    'symbol': bingx_symbol,
                    'incomeType': 'FUNDING_FEE',
                    'limit': limit
                }
                if start_time:
                    funding_params['startTime'] = start_time
                if end_time:
                    funding_params['endTime'] = end_time
                
                funding_response = await self.client.swap_v2_private_get_user_income(funding_params)
                
                if funding_response and 'data' in funding_response:
                    for record in funding_response['data'].get('data', []):
                        income = float(record.get('income', 0))
                        # Positive income = funding received, negative = paid
                        if income > 0:
                            funding_received += income
                        # Note: negative funding is paid out, but we track received separately
            except Exception as e:
                log.warning(f"BingX: Failed to fetch funding history: {e}")
            
            # Fetch commission fees (COMMISSION type)
            try:
                commission_params = {
                    'symbol': bingx_symbol,
                    'incomeType': 'COMMISSION',
                    'limit': limit
                }
                if start_time:
                    commission_params['startTime'] = start_time
                if end_time:
                    commission_params['endTime'] = end_time
                
                commission_response = await self.client.swap_v2_private_get_user_income(commission_params)
                
                if commission_response and 'data' in commission_response:
                    for record in commission_response['data'].get('data', []):
                        commission = abs(float(record.get('income', 0)))
                        fees_paid += commission
            except Exception as e:
                log.warning(f"BingX: Failed to fetch commission history: {e}")
            
            return {
                'funding_received': funding_received,
                'fees_paid': fees_paid
            }
            
        except Exception as e:
            log.error(f"BingX: get_income_history failed: {e}")
            return {'funding_received': 0.0, 'fees_paid': 0.0}
    
    # ============================================
    # WEBSOCKET (to be implemented)
    # ============================================
    
    async def subscribe_orderbook(self, symbol: str, callback) -> None:
        """Subscribe to BingX orderbook WebSocket"""
        # TODO: Implement WebSocket subscription
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
        """Get BingX server time"""
        try:
            time = await self.client.fetch_time()
            return time
        except Exception as e:
            raise ExchangeError(f"Failed to get server time: {e}")
    
    async def sync_time(self) -> None:
        """Sync time with BingX"""
        # ccxt handles this automatically with adjustForTimeDifference
        pass
    
    # ============================================
    # BINGX-SPECIFIC METHODS
    # ============================================
    
    async def set_position_mode(self, hedge_mode: bool = False) -> bool:
        """
        Set position mode
        
        Args:
            hedge_mode: True for hedge mode (dual positions), False for one-way mode
        """
        try:
            await self.client.set_position_mode(hedge_mode)
            return True
        except Exception as e:
            if 'not modified' in str(e).lower() or 'same' in str(e).lower():
                return True
            raise ExchangeError(f"Failed to set position mode: {e}")
