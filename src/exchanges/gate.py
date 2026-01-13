"""
Gate.io Futures Exchange Adapter

This adapter implements only the exchange-specific API calls.
All validation, logging, and error handling is done in BaseExchange.

Gate.io API Reference:
- Futures: https://www.gate.io/docs/developers/futures/index.html
- Uses USDT-settled perpetual contracts

Gate.io Testnet:
- https://www.gate.io/docs/developers/futures/index.html#testnet
- Testnet available at: https://fx-testnet.gateio.ws
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
import ccxt.async_support as ccxt

from .base import BaseExchange, ExchangeError, RateLimitError, NetworkError
from .enums import Exchange, PositionSide, OrderSide, OrderType
from .types import Position, Order, OrderBook, PriceData, Balance, FundingRate


class GateExchange(BaseExchange):
    """
    Gate.io Futures adapter (USDT-settled perpetual contracts)
    
    Implements ONLY:
    1. _api_* methods (raw API calls)
    2. _parse_* methods (convert Gate response format to our types)
    
    All business logic is in BaseExchange!
    
    Notes:
    - Gate.io uses "swap" for perpetual futures in ccxt
    - Symbol format: BTC_USDT (with underscore for API), BTC/USDT:USDT (ccxt)
    - Contract size varies by symbol
    - Dual position mode: can have both LONG and SHORT simultaneously
    """
    
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        testnet: bool = False
    ):
        super().__init__(api_key, secret_key, testnet=testnet)
        self.exchange_name = Exchange.GATE
        
        # Initialize ccxt client
        self.client = ccxt.gate({
            'apiKey': api_key,
            'secret': secret_key,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',  # Perpetual futures
                'defaultSettle': 'usdt',  # USDT-settled
            }
        })
        
        if testnet:
            # Gate.io testnet
            self.client.set_sandbox_mode(True)
    
    # ============================================
    # CONNECTION
    # ============================================
    
    async def connect(self) -> bool:
        """Connect to Gate.io"""
        try:
            await self.client.load_markets()
            self.connected = True
            return True
        except Exception as e:
            raise ExchangeError(f"Failed to connect: {e}")
    
    async def disconnect(self) -> None:
        """Disconnect from Gate.io"""
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
        Convert simple symbol format to Gate.io ccxt format
        
        BTCUSDT -> BTC/USDT:USDT (perpetual swap)
        BTC_USDT -> BTC/USDT:USDT
        """
        # Already in correct format
        if '/' in symbol and ':' in symbol:
            return symbol
        
        # Handle Gate.io native format BTC_USDT
        if '_' in symbol:
            parts = symbol.split('_')
            if len(parts) == 2:
                base, quote = parts
                return f"{base}/{quote}:{quote}"
        
        # Convert BTCUSDT -> BTC/USDT:USDT
        quote_currencies = ['USDT', 'USDC', 'USD']
        for quote in quote_currencies:
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                return f"{base}/{quote}:{quote}"
        
        # Fallback
        return symbol
    
    def _to_gate_symbol(self, symbol: str) -> str:
        """
        Convert to Gate.io native symbol format for API calls
        
        BTCUSDT -> BTC_USDT
        BTC/USDT:USDT -> BTC_USDT
        """
        if '_' in symbol and '/' not in symbol:
            return symbol
        
        # From ccxt format
        if '/' in symbol:
            base = symbol.split('/')[0]
            quote = symbol.split('/')[1].split(':')[0]
            return f"{base}_{quote}"
        
        # From simple format
        quote_currencies = ['USDT', 'USDC', 'USD']
        for quote in quote_currencies:
            if symbol.endswith(quote):
                base = symbol[:-len(quote)]
                return f"{base}_{quote}"
        
        return symbol
    
    # ============================================
    # API ADAPTERS (pure API calls, no validation!)
    # ============================================
    
    async def _api_get_orderbook(self, symbol: str, limit: int) -> Dict[str, Any]:
        """Gate.io: GET /api/v4/futures/usdt/order_book"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            return await self.client.fetch_order_book(ccxt_symbol, limit)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
    
    async def _api_get_price_data(self, symbol: str) -> Dict[str, Any]:
        """Gate.io: GET /api/v4/futures/usdt/tickers"""
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
        """Gate.io: GET /api/v4/futures/usdt/tickers - mark price from ticker"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            ticker = await self.client.fetch_ticker(ccxt_symbol)
            # Gate.io includes mark price in ticker info
            if 'info' in ticker and 'mark_price' in ticker['info']:
                return float(ticker['info']['mark_price'])
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
        Gate.io: Open position
        
        Steps:
        1. Set leverage
        2. Convert quantity to contracts
        3. Place order
        
        Note: Gate.io uses contracts, contract size varies by symbol
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            
            # 1. Set leverage (ignore "already set" errors)
            try:
                await self.client.set_leverage(leverage, ccxt_symbol)
            except Exception as e:
                if 'leverage' not in str(e).lower() and 'not changed' not in str(e).lower():
                    raise
            
            # 2. Get market info for contract size
            market = self.client.markets.get(ccxt_symbol, {})
            contract_size = float(market.get('contractSize', 1))
            
            # Convert quantity (in base currency like BTC) to contracts
            # If contract size is 0.001 BTC and quantity is 0.01 BTC, contracts = 10
            contracts = quantity / contract_size if contract_size else quantity
            contracts = round(contracts)  # Gate uses integer contracts
            
            # 3. Place order
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = 'buy' if side == PositionSide.LONG else 'sell'
            
            params = {}
            
            if order_type == OrderType.LIMIT:
                order = await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=contracts,
                    price=price,
                    params=params
                )
            else:
                order = await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=contracts,
                    params=params
                )
            
            # 4. Get position info
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
    
    async def _api_close_position(
        self,
        symbol: str,
        order_type: OrderType,
        price: Optional[float]
    ) -> Dict[str, Any]:
        """
        Gate.io: Close position
        
        Get current position, place opposite order with reduceOnly=True
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
            
            contracts = float(position['contracts'])
            position_side = position.get('side', '')  # 'long' or 'short'
            
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
            
            # 3. Return updated position
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
        """Gate.io: POST /api/v4/futures/usdt/orders"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = side.value.lower()
            
            # Convert quantity to contracts
            market = self.client.markets.get(ccxt_symbol, {})
            contract_size = float(market.get('contractSize', 1))
            contracts = round(quantity / contract_size) if contract_size else round(quantity)
            
            params = {}
            if reduce_only:
                params['reduceOnly'] = True
            
            if order_type == OrderType.LIMIT:
                return await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=contracts,
                    price=price,
                    params=params
                )
            else:
                return await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=contracts,
                    params=params
                )
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_cancel_order(self, order_id: str, symbol: str) -> bool:
        """Gate.io: DELETE /api/v4/futures/usdt/orders/{order_id}"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            await self.client.cancel_order(order_id, ccxt_symbol)
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_set_leverage(self, symbol: str, leverage: int) -> bool:
        """Gate.io: POST /api/v4/futures/usdt/positions/{contract}/leverage"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            await self.client.set_leverage(leverage, ccxt_symbol)
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            # Gate may return error if leverage is already set
            if 'leverage' in str(e).lower() or 'not changed' in str(e).lower():
                return True
            raise ExchangeError(f"Failed to set leverage: {e}")
    
    async def _api_set_margin_mode(self, mode: str) -> bool:
        """Gate.io: Set margin mode (cross/isolated)"""
        try:
            # Gate uses 'cross' or 'isolated'
            margin_mode = 'cross' if mode == 'CROSS' else 'isolated'
            await self.client.set_margin_mode(margin_mode)
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            if 'already' in str(e).lower() or 'not changed' in str(e).lower():
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
        Gate.io: Set stop loss order using CCXT createStopLossOrder
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
            
            # Get contracts count (Gate uses contracts, not base currency amount)
            contracts = abs(float(position.get('contracts', 0)))
            pos_side = position.get('side', '')  # 'long' or 'short'
            
            # For SL: if LONG, sell when price falls; if SHORT, buy when price rises
            order_side = 'sell' if pos_side == 'long' else 'buy'
            
            # Use CCXT's createStopLossOrder with contracts as amount
            response = await self.client.create_stop_loss_order(
                symbol=ccxt_symbol,
                type='market',  # Market order when triggered
                side=order_side,
                amount=contracts,  # Number of contracts
                stopLossPrice=stop_price,  # Required parameter name
                params={
                    'reduceOnly': True,
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
        Gate.io: Set take profit order using CCXT createTakeProfitOrder
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
            
            # Get contracts count (Gate uses contracts, not base currency amount)
            contracts = abs(float(position.get('contracts', 0)))
            pos_side = position.get('side', '')  # 'long' or 'short'
            
            # For TP: if LONG, sell when price rises; if SHORT, buy when price falls
            order_side = 'sell' if pos_side == 'long' else 'buy'
            
            # Use CCXT's createTakeProfitOrder with contracts as amount
            response = await self.client.create_take_profit_order(
                symbol=ccxt_symbol,
                type='market',  # Market order when triggered
                side=order_side,
                amount=contracts,  # Number of contracts
                takeProfitPrice=take_profit_price,  # Required parameter name
                params={
                    'reduceOnly': True,
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
        """Gate.io: GET /api/v4/futures/usdt/accounts"""
        try:
            balance = await self.client.fetch_balance({'type': 'swap'})
            return balance
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_positions(self, symbol: Optional[str]) -> List[Dict[str, Any]]:
        """Gate.io: GET /api/v4/futures/usdt/positions"""
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
        """Gate.io: GET /api/v4/futures/usdt/positions/{contract}"""
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
        """Gate.io: GET /api/v4/futures/usdt/accounts"""
        try:
            return await self._api_get_balance()
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Gate.io: GET /api/v4/futures/usdt/contracts/{contract}"""
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
                'max_leverage': 100,  # Gate.io max for major pairs
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        """Gate.io: GET /api/v4/futures/usdt/funding_rate"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            
            # Try to get funding rate via ccxt
            try:
                funding = await self.client.fetch_funding_rate(ccxt_symbol)
                return {
                    'symbol': symbol,
                    'fundingRate': funding.get('fundingRate', 0),
                    'nextFundingTime': funding.get('fundingTimestamp', 0),
                }
            except Exception:
                # Fallback: get from ticker info
                ticker = await self.client.fetch_ticker(ccxt_symbol)
                info = ticker.get('info', {})
                return {
                    'symbol': symbol,
                    'fundingRate': float(info.get('funding_rate', 0) or 0),
                    'nextFundingTime': int(info.get('funding_next_apply', 0) or 0) * 1000,
                }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    # ============================================
    # PARSERS (convert Gate format to our types)
    # ============================================
    
    def _parse_position(self, data: Dict[str, Any]) -> Position:
        """Convert Gate.io position data to Position object"""
        symbol = data.get('symbol', '')
        contracts = float(data.get('contracts', 0) or 0)
        side = data.get('side', '')  # 'long' or 'short'
        
        return Position(
            id=f"gate_{symbol}_{int(datetime.utcnow().timestamp())}",
            pair=symbol,
            exchange1=self.exchange_name.value,
            exchange1_pos_id=data.get('id', ''),
            exchange1_side=side.upper() if side else 'LONG',
            exchange1_entry_price=float(data.get('entryPrice', 0) or 0),
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
        )
    
    def _parse_order(self, data: Dict[str, Any]) -> Order:
        """Convert Gate.io order data to Order object"""
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
        """Convert Gate.io orderbook to OrderBook object"""
        return OrderBook(
            symbol=data.get('symbol', ''),
            exchange=self.exchange_name.value,
            bids=[(float(price), float(qty)) for price, qty in data.get('bids', [])],
            asks=[(float(price), float(qty)) for price, qty in data.get('asks', [])],
            timestamp=data.get('timestamp', datetime.utcnow().timestamp())
        )
    
    def _parse_price_data(self, data: Dict[str, Any]) -> PriceData:
        """Convert Gate.io ticker to PriceData"""
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
        """Convert Gate.io balance to Balance object"""
        # ccxt normalizes balance - data is a dict with currency keys
        # Structure: {'USDT': {'free': X, 'used': Y, 'total': Z}, 'info': [...]}
        
        # Gate.io testnet may return list in 'info', or balances directly
        usdt = None
        
        # Try to get USDT directly from data
        if 'USDT' in data and isinstance(data['USDT'], dict):
            usdt = data['USDT']
        elif 'info' in data:
            info = data['info']
            # info could be a list of balances
            if isinstance(info, list):
                for item in info:
                    if item.get('currency', '').upper() == 'USDT':
                        usdt = {
                            'total': float(item.get('total', 0) or 0),
                            'free': float(item.get('available', item.get('free', 0)) or 0),
                            'used': float(item.get('position_margin', item.get('used', 0)) or 0)
                        }
                        break
            elif isinstance(info, dict) and 'USDT' in info:
                usdt = info['USDT']
        
        if usdt and isinstance(usdt, dict):
            total = float(usdt.get('total', 0) or 0)
            free = float(usdt.get('free', usdt.get('available', 0)) or 0)
            used = float(usdt.get('used', usdt.get('position_margin', 0)) or 0)
        else:
            # Fallback - try to sum all from free dict
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
        """Convert Gate.io funding data to FundingRate"""
        next_funding_ts = int(data.get('nextFundingTime', 0) or 0)
        rate = float(data.get('fundingRate', 0) or 0)
        
        return FundingRate(
            symbol=data.get('symbol', ''),
            exchange=self.exchange_name.value,
            rate=rate,
            rate_bps=rate * 10000,
            next_funding_time=datetime.fromtimestamp(next_funding_ts / 1000) if next_funding_ts else datetime.utcnow(),
            timestamp=datetime.utcnow()
        )
    
    # ============================================
    # WEBSOCKET (to be implemented)
    # ============================================
    
    async def subscribe_orderbook(self, symbol: str, callback) -> None:
        """Subscribe to Gate.io orderbook WebSocket"""
        # TODO: Implement WebSocket subscription
        # Gate.io WS: wss://fx-ws.gateio.ws/v4/ws/usdt
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
        """Get Gate.io server time"""
        try:
            time = await self.client.fetch_time()
            return time
        except Exception as e:
            raise ExchangeError(f"Failed to get server time: {e}")
    
    async def sync_time(self) -> None:
        """Sync time with Gate.io"""
        # ccxt handles this automatically
        pass
    
    # ============================================
    # GATE-SPECIFIC METHODS
    # ============================================
    
    async def set_dual_position_mode(self, dual_mode: bool = True) -> bool:
        """
        Set position mode
        
        Args:
            dual_mode: True for dual position mode (can have both LONG and SHORT)
            
        Note: Gate.io supports dual position mode by default
        """
        try:
            # Gate.io may not need explicit mode setting through ccxt
            # The mode is determined by how orders are placed
            return True
        except Exception as e:
            raise ExchangeError(f"Failed to set position mode: {e}")
    
    async def get_contract_info(self, symbol: str) -> Dict[str, Any]:
        """Get detailed contract information"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            market = self.client.markets.get(ccxt_symbol)
            if not market:
                await self.client.load_markets(True)
                market = self.client.markets.get(ccxt_symbol)
            
            return {
                'symbol': symbol,
                'contract_size': market.get('contractSize'),
                'tick_size': market.get('precision', {}).get('price'),
                'min_qty': market.get('limits', {}).get('amount', {}).get('min'),
                'max_leverage': market.get('info', {}).get('leverage_max', 100),
                'maker_fee': market.get('maker'),
                'taker_fee': market.get('taker'),
            }
        except Exception as e:
            raise ExchangeError(f"Failed to get contract info: {e}")
