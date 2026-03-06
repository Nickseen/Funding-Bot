"""
Bitget Futures Exchange Adapter

This adapter implements only the exchange-specific API calls.
All validation, logging, and error handling is done in BaseExchange.

Bitget API Reference:
- https://www.bitget.com/api-doc/contract/intro

Bitget Testnet:
- Bitget has demo trading mode with sandbox environment
- Demo mode can be enabled via sandbox parameter
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
import asyncio
import ccxt.async_support as ccxt

from .base import BaseExchange, ExchangeError, RateLimitError, NetworkError
from .enums import Exchange, PositionSide, OrderSide, OrderType
from .types import Position, Order, OrderBook, PriceData, Balance, FundingRate
from ..utils.logger import log


class BitgetExchange(BaseExchange):
    """
    Bitget Futures adapter (USDT-margined perpetual contracts)
    
    Implements ONLY:
    1. _api_* methods (raw API calls)
    2. _parse_* methods (convert Bitget response format to our types)
    
    All business logic is in BaseExchange!
    
    Notes:
    - Bitget uses "swap" for perpetual futures in ccxt
    - Symbol format: BTC/USDT:USDT (ccxt), BTCUSDT_UMCBL (native)
    - Contract size varies by symbol
    - Supports both One-way and Hedge mode
    - Uses productType: 'umcbl' (USDT-margined contracts)
    """
    
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        passphrase: str = "",
        testnet: bool = False
    ):
        super().__init__(api_key, secret_key, passphrase=passphrase, testnet=testnet)
        self.exchange_name = Exchange.BITGET
        
        # Initialize ccxt client
        self.client = ccxt.bitget({
            'apiKey': api_key,
            'secret': secret_key,
            'password': passphrase,  # Bitget requires passphrase
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',  # Perpetual futures
                'defaultSubType': 'linear',  # USDT-margined
                'adjustForTimeDifference': True,
                'recvWindow': 60000,
                'timeDifference': 0,
            }
        })
        
        if testnet:
            # Bitget demo trading
            self.client.set_sandbox_mode(True)
    
    # ============================================
    # CONNECTION
    # ============================================
    
    async def connect(self) -> bool:
        """Connect to Bitget"""
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
        """Disconnect from Bitget"""
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
        Convert simple symbol format to Bitget ccxt format
        
        BTCUSDT -> BTC/USDT:USDT (perpetual swap)
        """
        # Already in correct format
        if '/' in symbol and ':' in symbol:
            return symbol
        
        # Convert BTCUSDT -> BTC/USDT:USDT
        quote_currencies = ['USDT', 'USDC', 'USD']
        for quote in quote_currencies:
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                return f"{base}/{quote}:{quote}"
        
        # Fallback
        return symbol
    
    def _to_bitget_symbol(self, symbol: str) -> str:
        """
        Convert to Bitget native symbol format for API calls
        
        BTCUSDT -> BTCUSDT_UMCBL
        BTC/USDT:USDT -> BTCUSDT_UMCBL
        """
        # From ccxt format
        if '/' in symbol:
            base = symbol.split('/')[0]
            quote = symbol.split('/')[1].split(':')[0]
            return f"{base}{quote}_UMCBL"
        
        # From simple format - add suffix
        if not symbol.endswith('_UMCBL'):
            return f"{symbol}_UMCBL"
        
        return symbol
    
    # ============================================
    # API ADAPTERS (pure API calls, no validation!)
    # ============================================
    
    async def _api_get_orderbook(self, symbol: str, limit: int) -> Dict[str, Any]:
        """Bitget: GET /api/mix/v1/market/depth"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            return await self.client.fetch_order_book(ccxt_symbol, limit)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
    
    async def _api_get_price_data(self, symbol: str) -> Dict[str, Any]:
        """Bitget: GET /api/mix/v1/market/ticker"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            ticker = await self.client.fetch_ticker(ccxt_symbol)
            return {
                'symbol': symbol,
                'bid': ticker.get('bid') or ticker.get('last'),
                'ask': ticker.get('ask') or ticker.get('last'),
                'bid_qty': ticker.get('bidVolume', 0) or 0,
                'ask_qty': ticker.get('askVolume', 0) or 0,
                'timestamp': ticker.get('timestamp')
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_mark_price(self, symbol: str) -> float:
        """Bitget: GET /api/mix/v1/market/mark-price"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            ticker = await self.client.fetch_ticker(ccxt_symbol)
            # Bitget includes mark price in ticker info
            if 'info' in ticker and 'markPrice' in ticker['info']:
                return float(ticker['info']['markPrice'])
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
        Bitget: Open position
        
        Steps:
        1. Set isolated margin mode (MUST be first!)
        2. Set leverage
        3. Place order
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            
            # 1. Set leverage
            try:
                await self.client.set_leverage(
                    leverage, 
                    ccxt_symbol,
                    params={
                        'productType': 'umcbl',
                        'marginCoin': 'USDT',
                    }
                )
            except Exception as e:
                # Only ignore if leverage is already the same
                if 'leverage' not in str(e).lower() and 'same' not in str(e).lower():
                    raise ExchangeError(f"Failed to set leverage: {e}")
            
            # 2. Place order with isolated margin mode
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = 'buy' if side == PositionSide.LONG else 'sell'
            
            # IMPORTANT: Include marginMode in order params to force isolated mode
            # For one-way mode use 'oneWayMode': True, for hedge mode use 'hedged': True
            # We'll try one-way mode first (more common for demo accounts)
            try:
                params = {
                    'oneWayMode': True,
                    'marginMode': 'isolated',  # Force isolated margin
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
            except ccxt.InvalidOrder as e:
                # Try hedge mode if one-way fails
                if '40774' in str(e) or 'position' in str(e).lower():
                    params = {
                        'hedged': True,
                        'positionSide': 'long' if side == PositionSide.LONG else 'short',
                        'marginMode': 'isolated',  # Force isolated margin
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
                else:
                    raise
            
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
        Bitget: Close position using dedicated Flash Close endpoint.

        Bitget's regular place-order endpoint with reduceOnly/holdSide causes
        error 40774 depending on the account's position mode (unilateral vs hedge).
        The dedicated close-positions endpoint (Flash Close) works for both modes
        regardless of params, so we always use it.
        CCXT: POST /api/v2/mix/order/close-positions
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)

            # Verify position exists first
            positions = await self.client.fetch_positions([ccxt_symbol])
            position = next(
                (p for p in positions if float(p.get('contracts', 0) or 0) != 0),
                None
            )

            if not position:
                return {'symbol': symbol, 'contracts': 0, 'side': None, 'status': 'CLOSED'}

            # Use Flash Close endpoint - works for unilateral and hedge mode
            # Smart PnL already verifies instant fill so market execution is fine
            await self.client.close_position(ccxt_symbol)

            # Wait for the order to execute
            await asyncio.sleep(0.5)

            # Verify closure
            positions = await self.client.fetch_positions([ccxt_symbol])
            remaining = next(
                (p for p in positions if float(p.get('contracts', 0) or 0) != 0),
                None
            )
            if remaining:
                log.warning(f"Bitget: Position {symbol} not fully closed, remaining: {remaining.get('contracts', 0)}")

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
        """Bitget: POST /api/mix/v1/order/placeOrder"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = side.value.lower()
            
            params = {}
            if reduce_only:
                params['reduceOnly'] = True
            
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
        """Bitget: POST /api/mix/v1/order/cancel-order"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            await self.client.cancel_order(order_id, ccxt_symbol)
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to cancel order: {e}")
    
    async def _api_set_leverage(self, symbol: str, leverage: int) -> bool:
        """Bitget: POST /api/v2/mix/account/set-leverage"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            
            # CCXT handles symbol conversion and params internally for Bitget
            await self.client.set_leverage(
                leverage, 
                ccxt_symbol,
                params={
                    'productType': 'umcbl',  # USDT-margined perpetual
                    'marginCoin': 'USDT',
                }
            )
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            # Bitget may return error if leverage is already set
            if 'leverage' in str(e).lower() or 'same' in str(e).lower():
                return True
            raise ExchangeError(f"Failed to set leverage: {e}")
    
    async def _api_set_margin_mode(self, mode: str) -> bool:
        """Bitget: POST /api/v2/mix/account/set-margin-mode
        
        WARNING: This method sets margin mode GLOBALLY for the entire account.
        For opening positions, margin mode is automatically set per-order in _api_open_position().
        
        For this bot, we ALWAYS use ISOLATED mode regardless of the mode parameter.
        """
        try:
            # Force isolated mode for safety (delta-neutral trading requirement)
            margin_mode = 'isolated'
            
            log.warning(f"Bitget: Setting GLOBAL margin mode to {margin_mode}. "
                       f"This applies to all future positions on all symbols.")
            
            # Bitget API v2: set_margin_mode endpoint doesn't accept symbol parameter
            # It sets the default margin mode for the whole account
            await self.client.set_margin_mode(
                margin_mode,
                params={
                    'productType': 'umcbl',  # USDT-margined perpetual
                }
            )
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            if 'already' in str(e).lower() or 'same' in str(e).lower():
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
        Bitget: Set stop loss using CCXT createStopLossOrder
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
            
            # For SL: if LONG position, sell when price falls; if SHORT, buy when price rises
            order_side = 'sell' if pos_side == 'long' else 'buy'
            
            # Bitget requires holdSide parameter
            hold_side = 'long' if pos_side == 'long' else 'short'
            
            # Use CCXT's createStopLossOrder (if available) or create_order with stopLoss params
            try:
                # Try CCXT unified method first
                response = await self.client.create_stop_loss_order(
                    symbol=ccxt_symbol,
                    type='market',
                    side=order_side,
                    amount=contracts,
                    stopLossPrice=stop_price,
                    params={
                        'holdSide': hold_side,
                    }
                )
            except AttributeError:
                # Fallback: use create_order with Bitget-specific params
                response = await self.client.create_order(
                    symbol=ccxt_symbol,
                    type='market',
                    side=order_side,
                    amount=contracts,
                    params={
                        'stopLossPrice': stop_price,
                        'holdSide': hold_side,
                        'reduceOnly': 'true',
                    }
                )
            
            return {
                'success': True,
                'order_id': response.get('id', ''),
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
        Bitget: Set take profit using CCXT createTakeProfitOrder
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
            
            # For TP: if LONG position, sell when price rises; if SHORT, buy when price falls
            order_side = 'sell' if pos_side == 'long' else 'buy'
            
            # Bitget requires holdSide parameter
            hold_side = 'long' if pos_side == 'long' else 'short'
            
            # Use CCXT's createTakeProfitOrder (if available) or create_order with takeProfit params
            try:
                # Try CCXT unified method first
                response = await self.client.create_take_profit_order(
                    symbol=ccxt_symbol,
                    type='market',
                    side=order_side,
                    amount=contracts,
                    takeProfitPrice=take_profit_price,
                    params={
                        'holdSide': hold_side,
                    }
                )
            except AttributeError:
                # Fallback: use create_order with Bitget-specific params
                response = await self.client.create_order(
                    symbol=ccxt_symbol,
                    type='market',
                    side=order_side,
                    amount=contracts,
                    params={
                        'takeProfitPrice': take_profit_price,
                        'holdSide': hold_side,
                        'reduceOnly': 'true',
                    }
                )
            
            return {
                'success': True,
                'order_id': response.get('id', ''),
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
        """Bitget: GET /api/mix/v1/account/accounts"""
        try:
            balance = await self.client.fetch_balance({'type': 'swap'})
            return balance
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_positions(self, symbol: Optional[str]) -> List[Dict[str, Any]]:
        """Bitget: GET /api/mix/v1/position/allPosition"""
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
        """Bitget: GET /api/mix/v1/position/singlePosition"""
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
        """Bitget: GET /api/mix/v1/account/account"""
        try:
            return await self._api_get_balance()
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Bitget: GET /api/mix/v1/market/contracts"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            markets = self.client.markets
            if ccxt_symbol not in markets:
                await self.client.load_markets(True)
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
                'contract_size': market.get('contractSize', 1),
                'max_leverage': 125,  # Bitget max for major pairs
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        """Bitget: GET /api/mix/v1/market/current-fundRate"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            
            # Try to get funding rate via ccxt
            try:
                funding = await self.client.fetch_funding_rate(ccxt_symbol)
                return {
                    'symbol': symbol,
                    'fundingRate': funding.get('fundingRate', 0),
                    'nextFundingTime': funding.get('nextFundingTimestamp', 0),
                }
            except Exception:
                # Fallback to ticker
                ticker = await self.client.fetch_ticker(ccxt_symbol)
                return {
                    'symbol': symbol,
                    'fundingRate': float(ticker.get('info', {}).get('fundingRate', 0) or 0),
                    'nextFundingTime': int(ticker.get('info', {}).get('nextSettleTime', 0) or 0),
                }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    # ============================================
    # PARSERS (convert Bitget format to our types)
    # ============================================
    
    def _parse_position(self, data: Dict[str, Any]) -> Position:
        """Convert Bitget position data to Position object"""
        symbol = data.get('symbol', '')
        contracts = float(data.get('contracts', 0) or 0)
        side = data.get('side', '')  # 'long' or 'short'
        entry_price = float(data.get('entryPrice', 0) or 0)
        notional = float(data.get('notional', 0) or 0)  # Position value in USDT
        
        # Extract fees and funding from raw exchange data ('info' field)
        info = data.get('info', {})
        
        # Bitget-specific fields (verified from actual API response):
        # - totalFee: accumulated funding fees received (positive value)
        # - deductedFee: transaction fees paid (absolute value)
        
        # Funding received (accumulated funding fees - positive if received)
        funding_received = 0.0
        if info.get('totalFee') not in (None, '', '0', 0):
            funding_received = abs(float(info['totalFee'] or 0))

        # Transaction fees paid (opening + closing fees)
        fees_paid = 0.0
        if info.get('deductedFee') not in (None, '', '0', 0):
            fees_paid = abs(float(info['deductedFee'] or 0))
        
        # Initial capital (position value at entry)
        initial_capital = notional if notional > 0 else abs(contracts) * entry_price
        
        return Position(
            id=f"bitget_{symbol}_{int(datetime.utcnow().timestamp())}",
            pair=symbol,
            exchange1=self.exchange_name.value,
            exchange1_pos_id=data.get('id'),
            exchange1_side=side.upper() if side else 'LONG',
            exchange1_entry_price=entry_price,
            exchange1_current_price=float(data.get('markPrice', 0) or 0),
            exchange1_leverage=int(data.get('leverage', 1) or 1),
            # For single exchange position
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
        """Convert Bitget order data to Order object"""
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
        """Convert Bitget orderbook to OrderBook object"""
        return OrderBook(
            symbol=data.get('symbol', ''),
            exchange=self.exchange_name.value,
            bids=[(float(item[0]), float(item[1])) for item in data.get('bids', [])],
            asks=[(float(item[0]), float(item[1])) for item in data.get('asks', [])],
            timestamp=data.get('timestamp', datetime.utcnow().timestamp())
        )
    
    def _parse_price_data(self, data: Dict[str, Any]) -> PriceData:
        """Convert Bitget ticker to PriceData"""
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
        """Convert Bitget balance to Balance object"""
        # ccxt normalizes balance - data is a dict with currency keys
        usdt = None
        
        # Try to get USDT directly from data
        if 'USDT' in data and isinstance(data['USDT'], dict):
            usdt = data['USDT']
        elif 'info' in data:
            info = data['info']
            # info could be a list of balances
            if isinstance(info, list):
                for item in info:
                    if item.get('marginCoin', '').upper() == 'USDT':
                        usdt = {
                            'total': float(item.get('equity', item.get('available', 0)) or 0),
                            'free': float(item.get('available', item.get('crossMaxAvailable', 0)) or 0),
                            'used': float(item.get('locked', 0) or 0)
                        }
                        break
            elif isinstance(info, dict) and 'USDT' in info:
                usdt = info['USDT']
        
        if usdt and isinstance(usdt, dict):
            total = float(usdt.get('total', 0) or 0)
            free = float(usdt.get('free', usdt.get('available', 0)) or 0)
            used = float(usdt.get('used', usdt.get('locked', 0)) or 0)
        else:
            # Fallback - try to get from free/used/total dicts
            total = float(data.get('total', {}).get('USDT', 0) or 0)
            free = float(data.get('free', {}).get('USDT', 0) or 0)
            used = float(data.get('used', {}).get('USDT', 0) or 0)
        
        return Balance(
            exchange=self.exchange_name.value,
            total=total,
            available=free,
            margin_used=used,
            unrealized_pnl=0,
            timestamp=datetime.utcnow().timestamp()
        )
    
    def _parse_funding_rate(self, data: Dict[str, Any]) -> FundingRate:
        """
        Convert Bitget funding data to FundingRate.
        
        Uses timezone-aware datetime to ensure correct time calculations.
        """
        from datetime import timezone, timedelta
        
        next_funding_ts = int(data.get('nextFundingTime', 0) or 0)
        rate = float(data.get('fundingRate', 0) or 0)
        
        now = datetime.now(timezone.utc)
        
        # Convert timestamp to timezone-aware UTC datetime
        # Bitget returns timestamp in milliseconds
        if next_funding_ts:
            # Check if timestamp is in seconds or milliseconds
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
            rate_bps=rate * 10000,  # Convert to basis points (0.0001 = 1 bps)
            next_funding_time=next_funding_time,
            timestamp=datetime.now(timezone.utc).timestamp()
        )
    
    # ============================================
    # WEBSOCKET (to be implemented)
    # ============================================
    
    async def subscribe_orderbook(self, symbol: str, callback) -> None:
        """Subscribe to Bitget orderbook WebSocket"""
        pass
    
    async def subscribe_position_updates(self, callback) -> None:
        """Subscribe to position updates"""
        pass
    
    async def subscribe_order_updates(self, callback) -> None:
        """Subscribe to order updates"""
        pass
    
    async def subscribe_account_updates(self, callback) -> None:
        """Subscribe to account updates"""
        pass
    
    # ============================================
    # HELPER METHODS
    # ============================================
    
    async def get_server_time(self) -> int:
        """Get Bitget server time"""
        try:
            time = await self.client.fetch_time()
            return time
        except Exception as e:
            raise ExchangeError(f"Failed to get server time: {e}")
    
    async def sync_time(self) -> None:
        """Sync time with Bitget"""
        pass
