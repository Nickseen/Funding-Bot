"""
OKX Futures Exchange Adapter

This adapter implements only the exchange-specific API calls.
All validation, logging, and error handling is done in BaseExchange.

OKX API Reference:
- https://www.okx.com/docs-v5/en/

OKX Testnet:
- https://www.okx.com/docs-v5/en/#overview-demo-trading-services
- Demo trading available at: https://www.okx.com/trade-swap/btc-usdt-swap (switch to demo mode)
"""

from typing import Dict, Any, List, Optional
from datetime import datetime
import ccxt.async_support as ccxt

from .base import BaseExchange, ExchangeError, RateLimitError, NetworkError
from .enums import Exchange, PositionSide, OrderSide, OrderType
from .types import Position, Order, OrderBook, PriceData, Balance, FundingRate


class OKXExchange(BaseExchange):
    """
    OKX Futures adapter (USDT-margined perpetual swaps)
    
    Implements ONLY:
    1. _api_* methods (raw API calls)
    2. _parse_* methods (convert OKX response format to our types)
    
    All business logic is in BaseExchange!
    
    Notes:
    - OKX uses "swap" for perpetual futures
    - Symbol format: BTC-USDT-SWAP
    - OKX requires passphrase for API authentication
    - Demo trading available (testnet)
    """
    
    def __init__(
        self,
        api_key: str,
        secret_key: str,
        passphrase: str = "",
        testnet: bool = False
    ):
        super().__init__(api_key, secret_key, passphrase=passphrase, testnet=testnet)
        self.exchange_name = Exchange.OKX
        
        # Initialize ccxt client
        self.client = ccxt.okx({
            'apiKey': api_key,
            'secret': secret_key,
            'password': passphrase,  # OKX requires passphrase
            'enableRateLimit': True,
            'options': {
                'defaultType': 'swap',  # Perpetual swaps
            }
        })
        
        if testnet:
            # OKX demo trading
            self.client.set_sandbox_mode(True)
    
    # ============================================
    # CONNECTION
    # ============================================
    
    async def connect(self) -> bool:
        """Connect to OKX"""
        try:
            await self.client.load_markets()
            self.connected = True
            return True
        except Exception as e:
            raise ExchangeError(f"Failed to connect: {e}")
    
    async def disconnect(self) -> None:
        """Disconnect from OKX"""
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
        Convert simple symbol format to OKX ccxt format
        
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
    
    # ============================================
    # API ADAPTERS (pure API calls, no validation!)
    # ============================================
    
    async def _api_get_orderbook(self, symbol: str, limit: int) -> Dict[str, Any]:
        """OKX: GET /api/v5/market/books"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            return await self.client.fetch_order_book(ccxt_symbol, limit)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
    
    async def _api_get_price_data(self, symbol: str) -> Dict[str, Any]:
        """OKX: GET /api/v5/market/ticker"""
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
        """OKX: GET /api/v5/public/mark-price"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            ticker = await self.client.fetch_ticker(ccxt_symbol)
            # OKX includes mark price in ticker info
            if 'info' in ticker and 'markPx' in ticker['info']:
                return float(ticker['info']['markPx'])
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
        OKX: Open position
        
        Steps:
        1. Set leverage
        2. Convert quantity to contracts (OKX uses contracts, not base currency)
        3. Place order
        
        Note: OKX contract size for BTC is 0.01 BTC per contract
        So 0.01 BTC = 1 contract
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            
            # 1. Set leverage (ignore "already set" errors)
            try:
                await self.client.set_leverage(leverage, ccxt_symbol)
            except Exception as e:
                if 'leverage' not in str(e).lower():
                    raise
            
            # 2. Convert quantity to contracts
            # OKX uses contracts, not base currency amount
            # Contract size is in market info
            market = self.client.markets.get(ccxt_symbol, {})
            contract_size = float(market.get('contractSize', 0.01))
            # quantity is in base currency (BTC), convert to number of contracts
            contracts = quantity / contract_size
            # Round to contract precision
            contracts = round(contracts)  # OKX contracts are whole numbers
            
            # 3. Place order
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = 'buy' if side == PositionSide.LONG else 'sell'
            
            params = {
                'tdMode': 'cross',  # Cross margin mode
            }
            
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
            
            # 4. Get position info
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
        OKX: Close position
        
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
            
            params = {
                'reduceOnly': True,
                'tdMode': 'cross',
            }
            
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
        """OKX: POST /api/v5/trade/order
        
        Note: quantity is in base currency (BTC), need to convert to contracts
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = side.value.lower()
            
            # Convert quantity (BTC) to contracts
            market = self.client.markets.get(ccxt_symbol, {})
            contract_size = float(market.get('contractSize', 0.01))
            contracts = round(quantity / contract_size)
            
            params = {
                'tdMode': 'cross',
            }
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
        """OKX: POST /api/v5/trade/cancel-order"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            await self.client.cancel_order(order_id, ccxt_symbol)
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_set_leverage(self, symbol: str, leverage: int) -> bool:
        """OKX: POST /api/v5/account/set-leverage"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            await self.client.set_leverage(leverage, ccxt_symbol)
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            # OKX may return error if leverage is already set
            if 'leverage' in str(e).lower():
                return True
            raise ExchangeError(f"Failed to set leverage: {e}")
    
    async def _api_set_margin_mode(self, mode: str) -> bool:
        """OKX: POST /api/v5/account/set-position-mode"""
        try:
            # OKX uses 'cross' or 'isolated'
            margin_mode = 'cross' if mode == 'CROSS' else 'isolated'
            await self.client.set_margin_mode(margin_mode)
            return True
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except Exception as e:
            if 'already' in str(e).lower():
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
        OKX: POST /api/v5/trade/order-algo with ordType=conditional
        
        Sets stop loss as conditional algo order attached to position
        """
        try:
            from .enums import PositionSide
            ccxt_symbol = self._convert_symbol(symbol)
            
            # Get position to determine size
            positions = await self.client.fetch_positions([ccxt_symbol])
            position = next(
                (p for p in positions if float(p.get('contracts', 0) or 0) != 0),
                None
            )
            
            if not position:
                raise ExchangeError(f"No position found for {symbol}")
            
            contracts = abs(float(position.get('contracts', 0)))
            pos_side = position.get('side', '')  # 'long' or 'short'
            
            # OKX instrument ID format: BTC-USDT-SWAP
            inst_id = ccxt_symbol.replace('/', '-').replace(':USDT', '-SWAP')
            
            # For SL: if LONG, sell when price falls below SL; if SHORT, buy when price rises above SL
            order_side = 'sell' if pos_side == 'long' else 'buy'
            
            # Use OKX algo order API directly
            response = await self.client.private_post_trade_order_algo({
                'instId': inst_id,
                'tdMode': 'cross',
                'side': order_side,
                'ordType': 'conditional',  # Conditional order (SL/TP)
                'sz': str(int(contracts)),
                'slTriggerPx': str(stop_price),
                'slOrdPx': '-1',  # -1 means market price
                'slTriggerPxType': 'mark',
                'reduceOnly': 'true',  # OKX requires string 'true', not boolean
            })
            
            # Check response
            if response.get('code') == '0':
                data = response.get('data', [{}])[0]
                return {
                    'success': True,
                    'algo_id': data.get('algoId'),
                    'type': 'stop_loss',
                    'trigger_price': stop_price
                }
            else:
                raise ExchangeError(f"OKX algo order error: {response}")
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ExchangeError:
            raise
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
        OKX: POST /api/v5/trade/order-algo with ordType=conditional
        
        Sets take profit as conditional algo order attached to position
        """
        try:
            from .enums import PositionSide
            ccxt_symbol = self._convert_symbol(symbol)
            
            # Get position to determine size
            positions = await self.client.fetch_positions([ccxt_symbol])
            position = next(
                (p for p in positions if float(p.get('contracts', 0) or 0) != 0),
                None
            )
            
            if not position:
                raise ExchangeError(f"No position found for {symbol}")
            
            contracts = abs(float(position.get('contracts', 0)))
            pos_side = position.get('side', '')  # 'long' or 'short'
            
            # OKX instrument ID format: BTC-USDT-SWAP
            inst_id = ccxt_symbol.replace('/', '-').replace(':USDT', '-SWAP')
            
            # For TP: if LONG, sell when price rises above TP; if SHORT, buy when price falls below TP
            order_side = 'sell' if pos_side == 'long' else 'buy'
            
            # Use OKX algo order API directly
            response = await self.client.private_post_trade_order_algo({
                'instId': inst_id,
                'tdMode': 'cross',
                'side': order_side,
                'ordType': 'conditional',  # Conditional order (SL/TP)
                'sz': str(int(contracts)),
                'tpTriggerPx': str(take_profit_price),
                'tpOrdPx': '-1',  # -1 means market price
                'tpTriggerPxType': 'mark',
                'reduceOnly': 'true',  # OKX requires string 'true', not boolean
            })
            
            # Check response
            if response.get('code') == '0':
                data = response.get('data', [{}])[0]
                return {
                    'success': True,
                    'algo_id': data.get('algoId'),
                    'type': 'take_profit',
                    'trigger_price': take_profit_price
                }
            else:
                raise ExchangeError(f"OKX algo order error: {response}")
            
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ExchangeError:
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to set position take profit: {e}")
    
    async def _api_get_balance(self) -> Dict[str, Any]:
        """OKX: GET /api/v5/account/balance"""
        try:
            balance = await self.client.fetch_balance()
            return balance
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_positions(self, symbol: Optional[str]) -> List[Dict[str, Any]]:
        """OKX: GET /api/v5/account/positions"""
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
        """OKX: GET /api/v5/account/positions for specific symbol"""
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
        """OKX: GET /api/v5/account/config"""
        try:
            return await self._api_get_balance()
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """OKX: GET /api/v5/public/instruments"""
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
                'max_leverage': 125,  # OKX max for major pairs
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    async def _api_get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        """OKX: GET /api/v5/public/funding-rate"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            # OKX doesn't include funding rate in ticker, use dedicated API
            funding = await self.client.fetch_funding_rate(ccxt_symbol)
            
            return {
                'symbol': symbol,
                'fundingRate': funding.get('fundingRate', 0),
                'nextFundingTime': funding.get('nextFundingTimestamp', 0),
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
    
    # ============================================
    # PARSERS (convert OKX format to our types)
    # ============================================
    
    def _parse_position(self, data: Dict[str, Any]) -> Position:
        """Convert OKX position data to Position object"""
        symbol = data.get('symbol', '')
        contracts = float(data.get('contracts', 0) or 0)
        side = data.get('side', '')  # 'long' or 'short'
        
        return Position(
            id=f"okx_{symbol}_{int(datetime.utcnow().timestamp())}",
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
        """Convert OKX order data to Order object"""
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
        """Convert OKX orderbook to OrderBook object"""
        # OKX returns [price, qty, numOrders] - take only first 2
        return OrderBook(
            symbol=data.get('symbol', ''),
            exchange=self.exchange_name.value,
            bids=[(float(item[0]), float(item[1])) for item in data.get('bids', [])],
            asks=[(float(item[0]), float(item[1])) for item in data.get('asks', [])],
            timestamp=data.get('timestamp', datetime.utcnow().timestamp())
        )
    
    def _parse_price_data(self, data: Dict[str, Any]) -> PriceData:
        """Convert OKX ticker to PriceData"""
        return PriceData(
            symbol=data.get('symbol', ''),
            bid=float(data.get('bid', 0) or 0),
            ask=float(data.get('ask', 0) or 0),
            bid_qty=float(data.get('bid_qty', 0) or 0),
            ask_qty=float(data.get('ask_qty', 0) or 0),
            timestamp=float(data.get('timestamp', datetime.utcnow().timestamp() * 1000)) / 1000
        )
    
    def _parse_balance(self, data: Dict[str, Any]) -> Balance:
        """Convert OKX balance to Balance object"""
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
            timestamp=datetime.utcnow().timestamp()
        )
    
    def _parse_funding_rate(self, data: Dict[str, Any]) -> FundingRate:
        """Convert OKX funding data to FundingRate"""
        next_funding_ts = int(data.get('nextFundingTime', 0) or 0)
        rate = float(data.get('fundingRate', 0) or 0)
        
        return FundingRate(
            symbol=data.get('symbol', ''),
            exchange=self.exchange_name.value,
            rate=rate,
            rate_bps=rate * 10000,  # Convert to basis points (0.0001 = 1 bps)
            next_funding_time=datetime.fromtimestamp(next_funding_ts / 1000) if next_funding_ts else datetime.utcnow(),
            timestamp=datetime.utcnow().timestamp()
        )
    
    # ============================================
    # WEBSOCKET (to be implemented)
    # ============================================
    
    async def subscribe_orderbook(self, symbol: str, callback) -> None:
        """Subscribe to OKX orderbook WebSocket"""
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
        """Get OKX server time"""
        try:
            time = await self.client.fetch_time()
            return time
        except Exception as e:
            raise ExchangeError(f"Failed to get server time: {e}")
    
    async def sync_time(self) -> None:
        """Sync time with OKX"""
        pass
