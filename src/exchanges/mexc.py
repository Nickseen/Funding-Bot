"""
MEXC Futures Exchange Adapter

This adapter implements only the exchange-specific API calls.
All validation, logging, and error handling is done in BaseExchange.

MEXC Contract API Reference:
- https://mexcdevelop.github.io/apidocs/contract_v1_en/
- Futures API domain (2026+): https://api.mexc.com

MEXC Futures Key Facts:
- Symbol format in API: BTC_USDT (underscore), ccxt: BTC/USDT:USDT
- Leverage: 1x–200x (symbol-dependent); for isolated margin `leverage` MUST be
  passed in create_order params (ccxt will reject otherwise).
- Contract side integers: 1=open long, 2=close short, 3=open short, 4=close long.
  ccxt translates buy/sell + reduceOnly automatically.
- Trigger/stop/TP orders: use `params.triggerPrice` in create_order → routes to
  planorder/place endpoint. triggerType: 1=≥, 2=≤; trend: 2=fair price; orderType: 5=market.
- Funding interval: 8 hours (00:00, 08:00, 16:00 UTC).
- NO official futures testnet. Do not attempt sandbox mode.
- ccxt marks BTC/USDT:USDT, ETH/USDT:USDT, LTC/USDT:USDT as unavailable by default;
  clear `unavailableContracts` option to enable them.
"""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
import asyncio
import ccxt.async_support as ccxt
from loguru import logger as log

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
                'defaultType': 'swap',
                'adjustForTimeDifference': True,
                'recvWindow': 60000,
            },
            # MEXC migrated futures API domain on 2026-01-19.
            # Old: contract.mexc.com → returns 403 Forbidden.
            # New: api.mexc.com
            'urls': {
                'api': {
                    'contract': {
                        'public': 'https://api.mexc.com/api/v1/contract',
                        'private': 'https://api.mexc.com/api/v1/private',
                    }
                }
            },
        })
        # ccxt merges class-level defaults AFTER the constructor dict, so the
        # unavailableContracts dict (which blocks BTC/ETH/LTC) must be cleared
        # after instantiation.
        self.client.options['unavailableContracts'] = {}

        if testnet:
            # MEXC does NOT provide an official futures testnet/demo API.
            # The testnet=True flag is accepted but has no effect.
            log.warning("MexcExchange: MEXC has no futures testnet. Running on MAINNET.")
    
    # ============================================
    # CONNECTION
    # ============================================

    async def connect(self) -> bool:
        """Connect to MEXC"""
        try:
            await self.sync_time()
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
        MEXC: Open position (isolated margin)

        Steps:
        1. Set leverage for both sides
        2. Place order with marginMode=isolated + leverage param
        3. Return position data
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)

            # 1. Set leverage for both position sides
            await self._api_set_leverage(symbol, leverage)

            # 2. Convert base quantity to contracts
            market = self.client.markets.get(ccxt_symbol, {})
            contract_size = float(market.get('contractSize') or 1.0)
            contracts = round(quantity / contract_size)
            if contracts < 1:
                contracts = 1

            # 3. For isolated margin ccxt requires `leverage` param
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = 'buy' if side == PositionSide.LONG else 'sell'
            params = {
                'marginMode': 'isolated',
                'leverage': leverage,
            }

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

            # 4. Poll for position confirmation (MEXC fills are not always
            #    reflected immediately in fetch_positions).
            position_data = None
            for attempt in range(5):
                positions = await self.client.fetch_positions([ccxt_symbol])
                position_data = next(
                    (p for p in positions
                     if p.get('symbol') == ccxt_symbol and float(p.get('contracts') or 0) != 0),
                    None
                )
                if position_data is not None:
                    break
                await asyncio.sleep(0.5)

            if position_data is None:
                raise ExchangeError(
                    f"Position not confirmed after order {order.get('id')} on MEXC for {symbol}"
                )

            return position_data

        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.InsufficientFunds as e:
            raise ExchangeError(f"Insufficient balance: {e}")
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except (ExchangeError, RateLimitError, NetworkError):
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to open position: {e}")
    
    async def _api_close_position(
        self,
        symbol: str,
        order_type: OrderType,
        price: Optional[float]
    ) -> Dict[str, Any]:
        """MEXC: Close position — place a reduce-only opposite order."""
        try:
            ccxt_symbol = self._convert_symbol(symbol)

            # 1. Get current position
            positions = await self.client.fetch_positions([ccxt_symbol])
            position = next(
                (p for p in positions
                 if p.get('symbol') == ccxt_symbol and float(p.get('contracts') or 0) != 0),
                None
            )
            if not position:
                return {'symbol': ccxt_symbol, 'contracts': 0, 'side': None}

            contracts = abs(float(position.get('contracts') or 0))
            position_side = position.get('side', '')  # 'long' or 'short'
            position_id = position.get('info', {}).get('positionId')

            # 2. Place opposite reduce-only order
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = 'sell' if position_side == 'long' else 'buy'
            params: Dict[str, Any] = {'reduceOnly': True}
            if position_id is not None:
                params['positionId'] = int(position_id)

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

            # 3. Return updated position state
            positions = await self.client.fetch_positions([ccxt_symbol])
            open_pos = next(
                (p for p in positions if p['symbol'] == ccxt_symbol and float(p.get('contracts') or 0) != 0),
                None
            )
            return open_pos or {'symbol': ccxt_symbol, 'contracts': 0, 'side': None}

        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except (ExchangeError, RateLimitError, NetworkError):
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
        """MEXC: POST contract/order/submit (via ccxt)"""
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            order_type_str = 'market' if order_type == OrderType.MARKET else 'limit'
            order_side = side.value.lower()

            # Convert base quantity to contracts
            market = self.client.markets.get(ccxt_symbol, {})
            contract_size = float(market.get('contractSize') or 1.0)
            contracts = round(quantity / contract_size)
            if contracts < 1:
                contracts = 1

            params: Dict[str, Any] = {}
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
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except (ExchangeError, RateLimitError, NetworkError):
            raise
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

    async def cancel_all_plan_orders(self, symbol: str) -> None:
        """
        MEXC: Cancel all trigger/plan orders (SL/TP) for a symbol.
        Uses POST /api/v1/private/planorder/cancel_all.
        MEXC does NOT auto-cancel plan orders when a position is closed.
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            # Get the exchange-native contract id (e.g. "NTRN_USDT")
            if self.client.markets and ccxt_symbol in self.client.markets:
                contract_id = self.client.markets[ccxt_symbol]['id']
            else:
                base = ccxt_symbol.split('/')[0]
                contract_id = f"{base}_USDT"
            await self.client.contractPrivatePostPlanorderCancelAll({'symbol': contract_id})
            log.info(f"mexc: Cancelled all plan orders (SL/TP) for {symbol}")
        except Exception as e:
            # Non-critical — log and continue
            log.warning(f"mexc: Failed to cancel plan orders for {symbol}: {e}")

    async def close_position(
        self,
        symbol: str,
        order_type: OrderType = OrderType.MARKET,
        price: Optional[float] = None
    ):
        """
        MEXC override: cancel all SL/TP plan orders before closing the position
        so they don't remain as orphaned triggers after the position is gone.
        """
        await self.cancel_all_plan_orders(symbol)
        return await super().close_position(symbol, order_type, price)
    
    async def _api_set_leverage(self, symbol: str, leverage: int) -> bool:
        """
        MEXC: POST contract/position/change_leverage

        Must be called for both LONG (positionType=1) and SHORT (positionType=2)
        sides, each with openType=1 (isolated).
        Idempotent "already set" responses are silently ignored.
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)

            for position_type in (1, 2):  # 1=long, 2=short
                try:
                    await self.client.set_leverage(
                        leverage,
                        ccxt_symbol,
                        params={'openType': 1, 'positionType': position_type}
                    )
                except Exception as e:
                    err = str(e).lower()
                    # Ignore only explicit "already set" responses
                    if 'already' in err or 'not modified' in err or 'same' in err:
                        continue
                    raise
            return True

        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except (ExchangeError, RateLimitError, NetworkError):
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to set leverage: {e}")
    
    async def _api_set_margin_mode(self, mode: str) -> bool:
        """
        MEXC: Margin mode is set per-order via openType (1=isolated, 2=cross).
        There is no account-level margin mode endpoint for MEXC futures.
        This adapter always opens positions with isolated margin (openType=1).
        """
        return True
    
    async def _api_set_stop_loss(
        self,
        symbol: str,
        side: PositionSide,
        stop_price: float,
        quantity: Optional[float]
    ) -> Dict[str, Any]:
        """
        MEXC: Place a trigger (plan) order to stop-loss an existing position.

        MEXC does not have a dedicated SL endpoint. Instead, we place a
        reduce-only market trigger order via planorder/place (routed by ccxt
        when params.triggerPrice is set):

          LONG SL  → SELL when price  ≤ stop_price  (triggerType=2)
          SHORT SL → BUY  when price  ≥ stop_price  (triggerType=1)

        trend=2 uses fair price as reference; orderType=5 for market execution.
        executeCycle=2 keeps the trigger active for 7 days.
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)

            # Resolve quantity (in contracts)
            if quantity is None:
                positions = await self.client.fetch_positions([ccxt_symbol])
                position = next(
                    (p for p in positions if p['symbol'] == ccxt_symbol and float(p.get('contracts') or 0) != 0),
                    None
                )
                if not position:
                    raise ExchangeError(f"No open position for {symbol}")
                quantity = abs(float(position.get('contracts') or 0))

            order_side = 'sell' if side == PositionSide.LONG else 'buy'
            # LONG SL triggers when price falls ≤ stop_price → triggerType=2
            # SHORT SL triggers when price rises ≥ stop_price → triggerType=1
            trigger_type = 2 if side == PositionSide.LONG else 1

            order = await self.client.create_order(
                symbol=ccxt_symbol,
                type='market',
                side=order_side,
                amount=quantity,
                params={
                    'triggerPrice': stop_price,
                    'triggerType': trigger_type,
                    'trend': 2,         # fair price
                    'orderType': 5,     # market
                    'executeCycle': 2,  # 7 days
                    'reduceOnly': True,
                }
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
        except (ExchangeError, RateLimitError, NetworkError):
            raise
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
        MEXC: Place a trigger (plan) order to take-profit an existing position.

          LONG TP  → SELL when price  ≥ tp_price  (triggerType=1)
          SHORT TP → BUY  when price  ≤ tp_price  (triggerType=2)
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)

            if quantity is None:
                positions = await self.client.fetch_positions([ccxt_symbol])
                position = next(
                    (p for p in positions if p['symbol'] == ccxt_symbol and float(p.get('contracts') or 0) != 0),
                    None
                )
                if not position:
                    raise ExchangeError(f"No open position for {symbol}")
                quantity = abs(float(position.get('contracts') or 0))

            order_side = 'sell' if side == PositionSide.LONG else 'buy'
            # LONG TP triggers when price rises ≥ tp_price → triggerType=1
            # SHORT TP triggers when price falls ≤ tp_price → triggerType=2
            trigger_type = 1 if side == PositionSide.LONG else 2

            order = await self.client.create_order(
                symbol=ccxt_symbol,
                type='market',
                side=order_side,
                amount=quantity,
                params={
                    'triggerPrice': take_profit_price,
                    'triggerType': trigger_type,
                    'trend': 2,         # fair price
                    'orderType': 5,     # market
                    'executeCycle': 2,  # 7 days
                    'reduceOnly': True,
                }
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
        except (ExchangeError, RateLimitError, NetworkError):
            raise
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
            return [p for p in positions if float(p.get('contracts') or 0) != 0]
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
                (p for p in positions
                 if p.get('symbol') == ccxt_symbol and float(p.get('contracts') or 0) != 0),
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
        MEXC: GET contract/detail (via ccxt market data)

        MEXC market fields:
          contractSize  → base units per contract (e.g., 0.0001 BTC for BTCUSDT)
          volUnit (precision.amount)  → contract step, always integer (usually 1)
          minVol (limits.amount.min)  → minimum contracts
          maxVol (limits.amount.max)  → maximum contracts
          priceUnit (precision.price) → price tick size

        We express min_quantity and quantity_step in BASE CURRENCY so the rest
        of the codebase can compare them directly to position quantity.
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)

            market = self.client.markets.get(ccxt_symbol)
            if not market:
                await self.client.load_markets(True)
                market = self.client.markets.get(ccxt_symbol)

            if not market:
                raise ExchangeError(f"Symbol {symbol} not found on MEXC")

            limits = market.get('limits', {}) or {}
            amount_limits = limits.get('amount', {}) or {}
            precision = market.get('precision', {}) or {}
            info = market.get('info', {}) or {}

            contract_size = float(market.get('contractSize') or 1.0)
            # volUnit is the contract quantity step (e.g., 1 contract)
            vol_unit = float(precision.get('amount') or 1.0)
            min_vol = float(amount_limits.get('min') or 1.0)
            max_vol = float(amount_limits.get('max') or 1_000_000.0)
            price_tick = float(precision.get('price') or 0.1)
            max_leverage = int(float(limits.get('leverage', {}).get('max') or info.get('maxLeverage') or 200))

            return {
                'symbol': symbol,
                'min_quantity': min_vol * contract_size,      # base currency
                'max_quantity': max_vol * contract_size,      # base currency
                'quantity_step': vol_unit * contract_size,    # base currency
                'min_price': price_tick,
                'price_tick': price_tick,
                'max_leverage': max_leverage,
                'contract_size': contract_size,
            }

        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except (ExchangeError, RateLimitError, NetworkError):
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to get symbol info: {e}")
    
    async def _api_get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        """MEXC: GET contract/funding_rate/{symbol}"""
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
        except (ExchangeError, RateLimitError, NetworkError):
            raise
        except Exception as e:
            raise ExchangeError(f"Failed to get funding rate: {e}")

    async def _api_get_income_history(
        self,
        symbol: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 100
    ) -> Dict[str, float]:
        """
        MEXC: GET contract/position/funding_records

        Returns accumulated funding payments for the symbol.
        Fees are not separately available here; only funding is summed.
        """
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            market = self.client.markets.get(ccxt_symbol, {}) or {}
            mexc_symbol = market.get('id') or self._convert_to_mexc_format(symbol)

            params: Dict[str, Any] = {
                'symbol': mexc_symbol,
                'page_size': min(limit, 100),
            }
            if start_time is not None:
                params['start_time'] = start_time
            if end_time is not None:
                params['end_time'] = end_time

            response = await self.client.contractPrivateGetPositionFundingRecords(params)
            records = (response.get('data') or {}).get('resultList') or []

            funding_received = 0.0
            for record in records:
                funding_received += float(record.get('amount') or 0)

            return {'funding_received': funding_received, 'fees_paid': 0.0}

        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            log.warning(f"MexcExchange: income history unavailable: {e}")
            return {'funding_received': 0.0, 'fees_paid': 0.0}
    
    # ============================================
    # PARSERS
    # ============================================
    
    def _parse_position(self, data: Dict[str, Any]) -> Position:
        """Convert MEXC position data (ccxt unified dict) to Position object.

        ccxt parses raw MEXC position fields:
          holdVol       → contracts
          openAvgPrice  → entryPrice
          liquidatePrice→ liquidationPrice
          positionType  → side  (1=long → 'long', 2=short → 'short')
          unrealized    (raw info field) → unrealizedPnl
        """
        ccxt_symbol = data.get('symbol', '')
        # Normalize symbol: BTC/USDT:USDT → BTCUSDT for storage
        symbol = ccxt_symbol
        if '/' in symbol:
            base = symbol.split('/')[0]
            quote = symbol.split('/')[1].split(':')[0]
            symbol = base + quote

        side = (data.get('side') or '').upper()  # 'LONG' or 'SHORT'
        contracts = abs(float(data.get('contracts') or 0))
        entry_price = float(data.get('entryPrice') or 0)
        mark_price = float(data.get('markPrice') or 0)
        leverage = int(float(data.get('leverage') or 1))
        liquidation_price = float(data.get('liquidationPrice') or 0)

        # contractSize is not included in ccxt's parsed position; look it up
        # from market cache. Fall back to info if cache is unavailable.
        raw_info = data.get('info', {}) or {}
        contract_size = 1.0
        if ccxt_symbol and self.client.markets:
            mkt = self.client.markets.get(ccxt_symbol, {}) or {}
            contract_size = float(mkt.get('contractSize') or raw_info.get('contractSize') or 1.0)
        else:
            contract_size = float(raw_info.get('contractSize') or 1.0)

        quantity_base = contracts * contract_size

        # Unrealized PnL: not populated in ccxt parsed structure for MEXC;
        # available in raw info as 'unrealized'.
        unrealized_pnl = float(raw_info.get('unrealized') or 0)

        pos_id = str(raw_info.get('positionId', ''))

        return Position(
            id=f"mexc_{symbol}_{side}_{pos_id}" if pos_id else f"mexc_{symbol}_{side}",
            pair=symbol,
            exchange1=self.exchange_name.value,
            exchange1_pos_id=pos_id,
            exchange1_side=side,
            exchange1_entry_price=entry_price,
            exchange1_current_price=mark_price,
            exchange1_leverage=leverage,
            exchange2='',
            exchange2_pos_id='',
            exchange2_side='',
            exchange2_entry_price=0,
            exchange2_current_price=0,
            exchange2_leverage=1,
            quantity=quantity_base,
            entry_time=datetime.now(timezone.utc).timestamp(),
            stop_loss_price=float(data.get('stopLossPrice') or raw_info.get('stopLoss') or 0),
            take_profit_price=float(data.get('takeProfitPrice') or raw_info.get('takeProfit') or 0),
            liquidation_price_ex1=liquidation_price,
            liquidation_price_ex2=0,
            status='OPEN' if contracts != 0 else 'CLOSED',
            unrealized_pnl=unrealized_pnl,
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
        """Convert MEXC balance to Balance object.

        ccxt.fetch_balance() for swap returns the unified structure:
          {
            'USDT': {'free': X, 'used': Y, 'total': Z},
            'info': {'success': True, 'data': [{'currency': 'USDT', 'availableBalance': X,
                      'equity': Y, 'unrealized': Z, 'positionMargin': W, ...}]},
            ...
          }
        We prefer the normalized top-level keys; fall back to raw info for unrealized PnL.
        """
        usdt = data.get('USDT') or {}
        if isinstance(usdt, dict):
            total = float(usdt.get('total') or 0)
            free = float(usdt.get('free') or 0)
            used = float(usdt.get('used') or 0)
        else:
            total = float(usdt or 0)
            free = total
            used = 0.0

        # Extract unrealized PnL from raw info if available
        unrealized_pnl = 0.0
        raw_data = data.get('info', {}).get('data') or []
        if isinstance(raw_data, list):
            for asset in raw_data:
                if (asset.get('currency') or '').upper() == 'USDT':
                    unrealized_pnl = float(asset.get('unrealized') or 0)
                    break

        return Balance(
            exchange=self.exchange_name.value,
            total=total,
            available=free,
            margin_used=used,
            unrealized_pnl=unrealized_pnl,
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
        """Get MEXC server time (Unix timestamp in milliseconds)"""
        try:
            return await self.client.fetch_time()
        except Exception as e:
            raise ExchangeError(f"Failed to get server time: {e}")

    async def sync_time(self) -> None:
        """Sync local time offset with MEXC server"""
        try:
            server_time = await self.client.fetch_time()
            local_time = self.client.milliseconds()
            self.client.options['timeDifference'] = server_time - local_time
        except Exception:
            pass
