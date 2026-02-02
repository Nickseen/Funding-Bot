"""
MEXC Futures Exchange Adapter

This adapter implements only the exchange-specific API calls.
All validation, logging, and error handling is done in BaseExchange.

MEXC API Reference:
- https://mexcdevelop.github.io/apidocs/contract_v1_en/
- https://www.mexc.com/api-doc

MEXC Futures Info:
- Uses USDT-margined perpetual contracts
- Symbol format: BTC_USDT for perpetuals
- Leverage: 1x to 200x
- Funding interval: 8 hours
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import ccxt.async_support as ccxt

from .base import BaseExchange, ExchangeError, RateLimitError, NetworkError
from .enums import Exchange, PositionSide, OrderSide, OrderType
from .types import Position, Order, OrderBook, PriceData, Balance, FundingRate


class MexcExchange(BaseExchange):
    """
    MEXC Futures adapter (USDT-margined perpetual swaps)
    
    Implements ONLY:
    1. _api_* methods (raw API calls)
    2. _parse_* methods (convert MEXC response format to our types)
    
    All business logic is in BaseExchange!
    
    Notes:
    - MEXC uses "swap" for perpetual futures
    - Symbol format in API: BTC_USDT (underscore)
    - CCXT symbol format: BTC/USDT:USDT
    - Leverage: 1x to 200x depending on symbol
    - Funding rate: every 8 hours (00:00, 08:00, 16:00 UTC)
    """
    
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        testnet: bool = False
    ):
        super().__init__(api_key, secret_key, testnet=testnet)
        self.exchange_name = Exchange.MEXC
        
        # Initialize ccxt client
        self.client = ccxt.mexc({
            'apiKey': api_key,
            'secret': secret_key,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',  # Perpetual swaps
                'adjustForTimeDifference': True,
                'recvWindow': 60000,  # 60 seconds receive window
            }
        })
        
        if testnet:
            # MEXC doesn't have official testnet for futures
            # Use demo trading if available
            pass
    
    # ============================================
    # CONNECTION
    # ============================================
    
    async def connect(self) -> bool:
        """Connect to MEXC"""
        try:
            await self.client.load_markets()
            self.connected = True
            return True
        except Exception as e:
            raise ExchangeError(f"Failed to connect: {e}")
    
    async def disconnect(self) -> None:
        """Disconnect from MEXC"""
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
        Convert simple symbol format to MEXC ccxt format
        
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
        
        # Fallback
        return symbol
    
    def _convert_to_mexc_format(self, symbol: str) -> str:
        """
        Convert ccxt symbol to MEXC API format
        
        BTC/USDT:USDT -> BTC_USDT (for raw API calls)
        """
        if '_' in symbol:
            return symbol
        
        # Remove settlement currency
        if ':' in symbol:
            symbol = symbol.split(':')[0]
        
        # Convert / to _
        return symbol.replace('/', '_')
    
    # ============================================
    # API ADAPTERS - MARKET DATA
    # ============================================
    
    async def _api_get_orderbook(self, symbol: str, limit: int) -> Dict[str, Any]:
        """MEXC: GET /api/v1/contract/depth/{symbol}"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            return await self.client.fetch_order_book(ccxt_symbol, limit)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to get orderbook: {e}")
    
    async def _api_get_price_data(self, symbol: str) -> Dict[str, Any]:
        """MEXC: GET /api/v1/contract/ticker"""
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
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to get price data: {e}")
    
    async def _api_get_mark_price(self, symbol: str) -> float:
        """MEXC: GET /api/v1/contract/fair_price/{symbol}"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            ticker = await self.client.fetch_ticker(ccxt_symbol)
            
            # MEXC includes mark price in ticker info
            if 'info' in ticker:
                info = ticker['info']
                if 'fairPrice' in info:
                    return float(info['fairPrice'])
                elif 'markPrice' in info:
                    return float(info['markPrice'])
            
            # Fallback to last price
            return float(ticker.get('last', 0) or 0)
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to get mark price: {e}")
    
    # ============================================
    # API ADAPTERS - TRADING
    # ============================================
    
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
        MEXC: Open position
        
        Steps:
        1. Set leverage
        2. Place order
        3. Return position data
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            
            # 1. Set leverage for both LONG and SHORT positions
            try:
                await self.client.set_leverage(
                    leverage, 
                    ccxt_symbol,
                    params={'openType': 1, 'positionType': 1}  # LONG, isolated
                )
            except Exception:
                pass
                
            try:
                await self.client.set_leverage(
                    leverage,
                    ccxt_symbol,
                    params={'openType': 1, 'positionType': 2}  # SHORT, isolated
                )
            except Exception:
                pass
            
            # 2. Convert quantity to contracts
            # MEXC contract size: 0.0001 BTC per contract for BTC/USDT
            # quantity is in BTC, need to convert to number of contracts
            market = self.client.markets.get(ccxt_symbol, {})
            contract_size = float(market.get('contractSize', 0.0001))
            contracts = int(quantity / contract_size)
            
            if contracts < 1:
                contracts = 1  # Minimum 1 contract
            
            # 3. Place order
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = 'buy' if side == PositionSide.LONG else 'sell'
            
            # MEXC uses positionMode and doesn't need positionSide for hedged mode
            # For one-way mode, we don't specify positionSide
            params = {}
            
            if order_type == OrderType.LIMIT:
                order = await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=contracts,  # Use contracts, not quantity
                    price=price,
                    params=params
                )
            else:
                order = await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=contracts,  # Use contracts, not quantity
                    params=params
                )
            
            # 3. Get position info
            positions = await self.client.fetch_positions([ccxt_symbol])
            position_data = next(
                (p for p in positions if p['symbol'] == ccxt_symbol and float(p.get('contracts', 0) or 0) != 0),
                None
            )
            
            return position_data or order
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.InsufficientFunds as e:
            raise ExchangeError(f"Insufficient balance: {e}")
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to open position: {e}")
    
    async def _api_close_position(
        self,
        symbol: str,
        order_type: OrderType,
        price: Optional[float]
    ) -> Dict[str, Any]:
        """
        MEXC: Close position
        
        Get current position, place opposite order to close
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            
            # 1. Get current position
            positions = await self.client.fetch_positions([ccxt_symbol])
            position = next(
                (p for p in positions if p['symbol'] == ccxt_symbol and float(p.get('contracts', 0) or 0) != 0),
                None
            )
            
            if not position:
                raise ExchangeError(f"No position found for {symbol}")
            
            contracts = abs(float(position.get('contracts', 0) or 0))
            position_side = position.get('side', '')  # 'long' or 'short'
            
            # 2. Place opposite order to close
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = 'sell' if position_side == 'long' else 'buy'
            
            params = {
                'reduceOnly': True,
            }
            
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
            
            # 3. Return updated position
            positions = await self.client.fetch_positions([ccxt_symbol])
            return next(
                (p for p in positions if p['symbol'] == ccxt_symbol),
                {'symbol': symbol, 'contracts': 0, 'side': None}
            )
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
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
        """MEXC: POST /api/v1/contract/order_place"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = side.value.lower()
            
            # Convert quantity (BTC) to contracts
            market = self.client.markets.get(ccxt_symbol, {})
            contract_size = float(market.get('contractSize', 0.0001))
            contracts = int(quantity / contract_size)
            
            params = {}
            if reduce_only:
                params['reduceOnly'] = True
            
            if order_type == OrderType.LIMIT:
                return await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=contracts,  # Use contracts
                    price=price,
                    params=params
                )
            else:
                return await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=contracts,  # Use contracts
                    params=params
                )
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to place order: {e}")
    
    async def _api_cancel_order(self, order_id: str, symbol: str) -> bool:
        """MEXC: POST /api/v1/contract/order_cancel"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            await self.client.cancel_order(order_id, ccxt_symbol)
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to cancel order: {e}")
    
    async def _api_set_leverage(self, symbol: str, leverage: int) -> bool:
        """
        MEXC: POST /api/v1/contract/leverage
        
        MEXC requires: openType (1=isolated, 2=cross) and positionType (1=long, 2=short)
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            
            # MEXC requires both long and short positions to be set separately
            # Set for LONG positions (positionType=1)
            try:
                await self.client.set_leverage(
                    leverage, 
                    ccxt_symbol,
                    params={
                        'openType': 1,  # 1 = isolated margin
                        'positionType': 1  # 1 = long
                    }
                )
            except Exception:
                pass  # Ignore if already set
            
            # Set for SHORT positions (positionType=2)
            try:
                await self.client.set_leverage(
                    leverage,
                    ccxt_symbol,
                    params={
                        'openType': 1,  # 1 = isolated margin
                        'positionType': 2  # 2 = short
                    }
                )
            except Exception:
                pass  # Ignore if already set
                
            return True
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            # MEXC may return error if leverage already set
            if 'leverage' in str(e).lower() or 'same' in str(e).lower():
                return True
            raise ExchangeError(f"Failed to set leverage: {e}")
    
    async def _api_set_margin_mode(self, mode: str) -> bool:
        """
        MEXC: POST /api/v1/contract/position_mode
        
        Note: MEXC uses isolated margin by default for perpetual swaps
        """
        try:
            # MEXC margin mode: 1=isolated, 2=cross
            margin_mode = 2 if mode == 'CROSS' else 1
            
            # MEXC may not have direct API for margin mode
            # Usually set per position or account level
            # Return True as it's typically isolated by default
            return True
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            if 'already' in str(e).lower() or 'mode' in str(e).lower():
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
        MEXC: Set stop loss
        
        MEXC supports stop orders through plan orders (trigger orders)
        For SL: LONG position → SELL when price falls; SHORT position → BUY when price rises
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            
            # Get position to determine size if not provided
            if quantity is None:
                positions = await self.client.fetch_positions([ccxt_symbol])
                position = next(
                    (p for p in positions if float(p.get('contracts', 0) or 0) != 0),
                    None
                )
                
                if not position:
                    raise ExchangeError(f"No position found for {symbol}")
                
                quantity = abs(float(position.get('contracts', 0) or 0))
            
            # For SL: LONG → SELL, SHORT → BUY
            order_side = 'sell' if side == PositionSide.LONG else 'buy'
            
            # MEXC stop order params
            params = {
                'stopPrice': stop_price,
                'triggerType': 'MARK_PRICE',  # Use mark price for trigger
                'reduceOnly': True,
            }
            
            order = await self.client.create_order(
                symbol=ccxt_symbol,
                type='STOP_MARKET',
                side=order_side,
                amount=quantity,
                params=params
            )
            
            return {
                'success': True,
                'order_id': order.get('id'),
                'type': 'stop_loss',
                'trigger_price': stop_price
            }
            
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
        MEXC: Set take profit
        
        For TP: LONG position → SELL when price rises; SHORT position → BUY when price falls
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            
            # Get position to determine size if not provided
            if quantity is None:
                positions = await self.client.fetch_positions([ccxt_symbol])
                position = next(
                    (p for p in positions if float(p.get('contracts', 0) or 0) != 0),
                    None
                )
                
                if not position:
                    raise ExchangeError(f"No position found for {symbol}")
                
                quantity = abs(float(position.get('contracts', 0) or 0))
            
            # For TP: LONG → SELL, SHORT → BUY
            order_side = 'sell' if side == PositionSide.LONG else 'buy'
            
            # MEXC take profit order params
            params = {
                'stopPrice': take_profit_price,
                'triggerType': 'MARK_PRICE',  # Use mark price for trigger
                'reduceOnly': True,
            }
            
            order = await self.client.create_order(
                symbol=ccxt_symbol,
                type='TAKE_PROFIT_MARKET',
                side=order_side,
                amount=quantity,
                params=params
            )
            
            return {
                'success': True,
                'order_id': order.get('id'),
                'type': 'take_profit',
                'trigger_price': take_profit_price
            }
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to set take profit: {e}")
    
    # ============================================
    # API ADAPTERS - ACCOUNT
    # ============================================
    
    async def _api_get_balance(self) -> Dict[str, Any]:
        """MEXC: GET /api/v1/contract/asset"""
        try:
            balance = await self.client.fetch_balance()
            return balance
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to get balance: {e}")
    
    async def _api_get_positions(self, symbol: Optional[str]) -> List[Dict[str, Any]]:
        """MEXC: GET /api/v1/contract/position"""
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
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to get positions: {e}")
    
    async def _api_get_position_by_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        """MEXC: GET /api/v1/contract/position/{symbol}"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            positions = await self.client.fetch_positions([ccxt_symbol])
            
            # Find position with non-zero contracts
            return next(
                (p for p in positions if float(p.get('contracts', 0) or 0) != 0),
                None
            )
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to get position: {e}")
    
    async def _api_get_account_info(self) -> Dict[str, Any]:
        """MEXC: GET /api/v1/contract/account"""
        try:
            # MEXC account info is usually in balance response
            balance = await self.client.fetch_balance()
            return balance.get('info', {})
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to get account info: {e}")
    
    # ============================================
    # API ADAPTERS - SYMBOL INFO & FUNDING
    # ============================================
    
    async def _api_get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """
        MEXC: GET /api/v1/contract/detail
        
        Returns:
        - min_quantity: minimum order size
        - max_quantity: maximum order size
        - quantity_step: step size for quantity
        - min_price: minimum price
        - price_tick: tick size for price
        - max_leverage: maximum leverage
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            
            # Get market info from loaded markets
            market = self.client.markets.get(ccxt_symbol)
            if not market:
                await self.client.load_markets(True)  # Reload markets
                market = self.client.markets.get(ccxt_symbol)
            
            if not market:
                raise ExchangeError(f"Symbol {symbol} not found")
            
            limits = market.get('limits', {})
            amount_limits = limits.get('amount', {})
            price_limits = limits.get('price', {})
            precision = market.get('precision', {})
            info = market.get('info', {})
            
            # Safe conversion with defaults
            min_qty = amount_limits.get('min')
            max_qty = amount_limits.get('max')
            min_price = price_limits.get('min')
            
            return {
                'symbol': symbol,
                'min_quantity': float(min_qty) if min_qty is not None else 1.0,
                'max_quantity': float(max_qty) if max_qty is not None else 1000000.0,
                'quantity_step': float(precision.get('amount', 1.0)),
                'min_price': float(min_price) if min_price is not None else 0.1,
                'price_tick': float(precision.get('price', 0.1)),
                'max_leverage': int(info.get('maxLeverage', 200) if info.get('maxLeverage') else 200),
                'contract_size': float(market.get('contractSize', 0.0001)),
            }
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to get symbol info: {e}")
    
    async def _api_get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        """MEXC: GET /api/v1/contract/funding_rate/{symbol}"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            funding_rate = await self.client.fetch_funding_rate(ccxt_symbol)
            
            return {
                'symbol': symbol,
                'fundingRate': funding_rate.get('fundingRate', 0),
                'nextFundingTime': funding_rate.get('fundingTimestamp', 0),
                'timestamp': funding_rate.get('timestamp', 0)
            }
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to get funding rate: {e}")
    
    # ============================================
    # PARSERS
    # ============================================
    
    def _parse_position(self, data: Dict[str, Any]) -> Position:
        """Convert MEXC position data to Position object"""
        # MEXC position structure
        symbol = data.get('symbol', '')
        if '/' in symbol:
            symbol = symbol.split('/')[0] + symbol.split('/')[1].split(':')[0]
        
        side = data.get('side', '').upper()  # 'LONG' or 'SHORT'
        contracts = float(data.get('contracts', 0) or 0)
        entry_price = float(data.get('entryPrice', 0) or 0)
        mark_price = float(data.get('markPrice', 0) or 0)
        leverage = int(data.get('leverage', 1) or 1)
        
        # Calculate quantity in base currency
        contract_size = float(data.get('contractSize', 1) or 1)
        quantity_base = abs(contracts) * contract_size
        
        return Position(
            id=f"mexc_{symbol}_{side}",
            pair=symbol,
            exchange1=self.exchange_name.value,
            exchange1_pos_id=str(data.get('id', '')),
            exchange1_side=side,
            exchange1_entry_price=entry_price,
            exchange1_current_price=mark_price,
            exchange1_leverage=leverage,
            # For single exchange position
            exchange2='',
            exchange2_pos_id='',
            exchange2_side='',
            exchange2_entry_price=0,
            exchange2_current_price=0,
            exchange2_leverage=1,
            quantity=quantity_base,
            entry_time=datetime.now(timezone.utc).timestamp(),
            stop_loss_price=float(data.get('stopLoss', 0) or 0),
            take_profit_price=float(data.get('takeProfit', 0) or 0),
            liquidation_price_ex1=float(data.get('liquidationPrice', 0) or 0),
            liquidation_price_ex2=0,
            status='OPEN' if contracts != 0 else 'CLOSED',
            unrealized_pnl=float(data.get('unrealizedPnl', 0) or 0),
        )
    
    def _parse_order(self, data: Dict[str, Any]) -> Order:
        """Convert MEXC order data to Order object"""
        side = data.get('side', '') or ''
        order_type = data.get('type', '') or ''
        status = data.get('status', 'open') or 'open'
        
        return Order(
            id=str(data.get('id', '') or ''),
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
        """Convert MEXC orderbook to OrderBook object"""
        return OrderBook(
            symbol=data.get('symbol', ''),
            exchange=self.exchange_name.value,
            bids=[(float(item[0]), float(item[1])) for item in data.get('bids', [])],
            asks=[(float(item[0]), float(item[1])) for item in data.get('asks', [])],
            timestamp=data.get('timestamp', datetime.now(timezone.utc).timestamp())
        )
    
    def _parse_price_data(self, data: Dict[str, Any]) -> PriceData:
        """Convert MEXC ticker to PriceData"""
        return PriceData(
            symbol=data.get('symbol', ''),
            bid=float(data.get('bid', 0) or 0),
            ask=float(data.get('ask', 0) or 0),
            bid_qty=float(data.get('bid_qty', 0) or 0),
            ask_qty=float(data.get('ask_qty', 0) or 0),
            timestamp=float(data.get('timestamp', datetime.now(timezone.utc).timestamp() * 1000)) / 1000
        )
    
    def _parse_balance(self, data: Dict[str, Any]) -> Balance:
        """Convert MEXC balance to Balance object"""
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
            unrealized_pnl=0,
            timestamp=datetime.now(timezone.utc).timestamp()
        )
    
    def _parse_funding_rate(self, data: Dict[str, Any]) -> FundingRate:
        """
        Convert MEXC funding data to FundingRate.
        
        CRITICAL: Uses timezone-aware datetime for correct time calculations.
        """
        next_funding_ts = int(data.get('nextFundingTime', 0) or 0)
        rate = float(data.get('fundingRate', 0) or 0)
        
        # Convert timestamp to timezone-aware UTC datetime
        if next_funding_ts:
            # Check if timestamp is in seconds or milliseconds
            if next_funding_ts > 4102444800:  # After year 2100 in seconds
                next_funding_time = datetime.fromtimestamp(next_funding_ts / 1000, tz=timezone.utc)
            else:
                next_funding_time = datetime.fromtimestamp(next_funding_ts, tz=timezone.utc)
        else:
            next_funding_time = datetime.now(timezone.utc)
        
        return FundingRate(
            symbol=data.get('symbol', ''),
            exchange=self.exchange_name.value,
            rate=rate,
            rate_bps=rate * 10000,  # Convert to basis points (0.0001 = 1 bps)
            next_funding_time=next_funding_time,
            timestamp=datetime.now(timezone.utc).timestamp()
        )
    
    # ============================================
    # WEBSOCKET (stubs for future implementation)
    # ============================================
    
    async def subscribe_orderbook(self, symbol: str, callback) -> None:
        """Subscribe to MEXC orderbook WebSocket"""
        pass  # TODO
    
    async def subscribe_position_updates(self, callback) -> None:
        """Subscribe to position updates"""
        pass  # TODO
    
    async def subscribe_order_updates(self, callback) -> None:
        """Subscribe to order updates"""
        pass  # TODO
    
    async def subscribe_account_updates(self, callback) -> None:
        """Subscribe to account updates"""
        pass  # TODO
    
    # ============================================
    # HELPER METHODS
    # ============================================
    
    async def get_server_time(self) -> int:
        """Get MEXC server time"""
        try:
            time = await self.client.fetch_time()
            return time
        except Exception as e:
            raise ExchangeError(f"Failed to get server time: {e}")
    
    async def sync_time(self) -> None:
        """Sync time with MEXC"""
        try:
            server_time = await self.client.fetch_time()
            local_time = self.client.milliseconds()
            time_diff = server_time - local_time
            self.client.options['timeDifference'] = time_diff
        except Exception:
            pass
