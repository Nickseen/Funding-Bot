"""KuCoin Futures exchange adapter."""

from typing import Dict, Any, List, Optional
from datetime import datetime, timezone, timedelta
import ccxt.async_support as ccxt

from .base import BaseExchange, ExchangeError, RateLimitError, NetworkError
from .enums import Exchange, PositionSide, OrderSide, OrderType
from .types import Position, Order, OrderBook, PriceData, Balance, FundingRate


class KuCoinExchange(BaseExchange):
    """KuCoin Futures adapter (kucoinfutures, linear USDT perpetual)."""

    def __init__(
        self,
        api_key: str,
        secret_key: str,
        passphrase: str = "",
        testnet: bool = False,
        api_test_mode: bool = False,
    ):
        super().__init__(api_key, secret_key, passphrase=passphrase, testnet=testnet)
        self.exchange_name = Exchange.KUCOIN
        self.api_test_mode = api_test_mode
        self.sandbox_enabled = False

        self.client = ccxt.kucoinfutures({
            "apiKey": api_key,
            "secret": secret_key,
            "password": passphrase,
            "enableRateLimit": True,
            "options": {
                "defaultType": "swap",
                "adjustForTimeDifference": True,
                "recvWindow": 60000,
                "timeDifference": 0,
            },
        })

        if testnet:
            try:
                self.client.set_sandbox_mode(True)
                self.sandbox_enabled = True
            except Exception:
                # KuCoin Futures sandbox endpoint can be unavailable in some ccxt versions.
                # Fallback: keep standard endpoint and rely on test-order mode when enabled.
                self.sandbox_enabled = False

    # ============================================
    # CONNECTION
    # ============================================

    async def connect(self) -> bool:
        """Connect to KuCoin Futures."""
        try:
            server_time = await self.client.fetch_time()
            local_time = self.client.milliseconds()
            self.client.options["timeDifference"] = server_time - local_time
            await self.client.load_markets()
            self.connected = True
            return True
        except Exception as e:
            raise ExchangeError(f"Failed to connect: {e}")

    async def disconnect(self) -> None:
        """Disconnect from KuCoin Futures."""
        await self.client.close()
        self.connected = False

    async def test_connection(self) -> bool:
        """Test exchange connectivity."""
        try:
            await self.client.fetch_time()
            return True
        except Exception:
            return False

    # ============================================
    # HELPERS
    # ============================================

    def _convert_symbol(self, symbol: str) -> str:
        """Convert symbol to ccxt contract format."""
        if "/" in symbol and ":" in symbol:
            return symbol

        # Native KuCoin format, e.g. XBTUSDTM
        if symbol.endswith("USDTM"):
            base = symbol[:-5]
            if base == "XBT":
                base = "BTC"
            return f"{base}/USDT:USDT"

        # Simple format, e.g. BTCUSDT
        if symbol.endswith("USDT"):
            base = symbol[:-4]
            return f"{base}/USDT:USDT"

        return symbol

    async def _ensure_market(self, symbol: str) -> Dict[str, Any]:
        """Ensure markets are loaded and return market metadata."""
        ccxt_symbol = self._convert_symbol(symbol)
        market = self.client.markets.get(ccxt_symbol) if hasattr(self.client, "markets") else None
        if not market:
            await self.client.load_markets()
            market = self.client.markets.get(ccxt_symbol)
        if not market:
            raise ExchangeError(f"Symbol {symbol} not found on KuCoin Futures")
        return market

    async def _to_contracts(self, symbol: str, quantity: float) -> int:
        """Convert base quantity to integer contracts."""
        market = await self._ensure_market(symbol)
        contract_size = float(market.get("contractSize", 1) or 1)
        contracts = int(quantity / contract_size) if contract_size > 0 else int(quantity)
        return max(1, contracts)

    def _is_test_order_mode(self) -> bool:
        """Use test-order endpoint mode when requested and not in sandbox."""
        if self.testnet and not self.sandbox_enabled:
            return True
        return self.api_test_mode and not self.testnet

    def _maybe_test_flag(self, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Inject KuCoin test-order flag when in API test mode."""
        out = dict(params or {})
        if self._is_test_order_mode():
            out["test"] = True
        return out

    def _extract_max_leverage(self, market: Dict[str, Any]) -> int:
        """Extract symbol-specific max leverage from market metadata."""
        limits = market.get("limits", {}) or {}
        leverage_limits = limits.get("leverage", {}) or {}
        info = market.get("info", {}) or {}

        candidates: List[Any] = [
            leverage_limits.get("max"),
            info.get("maxLeverage"),
            info.get("max_leverage"),
            info.get("leverageMax"),
            info.get("maxLever"),
        ]

        def walk(node: Any) -> None:
            if isinstance(node, dict):
                for key, value in node.items():
                    key_l = str(key).lower()
                    if "leverage" in key_l and "max" in key_l:
                        candidates.append(value)
                    if isinstance(value, (dict, list)):
                        walk(value)
            elif isinstance(node, list):
                for item in node:
                    walk(item)

        walk(info)

        parsed_values: List[int] = []
        for value in candidates:
            try:
                parsed = int(float(value))
                if parsed > 0:
                    parsed_values.append(parsed)
            except (TypeError, ValueError):
                continue

        if parsed_values:
            return min(parsed_values)

        return 100

    # ============================================
    # API ADAPTERS
    # ============================================

    async def _api_get_orderbook(self, symbol: str, limit: int) -> Dict[str, Any]:
        try:
            return await self.client.fetch_order_book(self._convert_symbol(symbol), limit)
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))

    async def _api_get_price_data(self, symbol: str) -> Dict[str, Any]:
        try:
            market = await self._ensure_market(symbol)
            contract_size = float(market.get("contractSize", 1) or 1)
            ticker = await self.client.fetch_ticker(self._convert_symbol(symbol))
            last_price = ticker.get("last") or 0
            bid_volume = float(ticker.get("bidVolume", 0) or 0)
            ask_volume = float(ticker.get("askVolume", 0) or 0)

            # KuCoin futures may report depth volumes in contracts; convert to base amount.
            bid_qty = bid_volume * contract_size
            ask_qty = ask_volume * contract_size
            return {
                "symbol": symbol,
                "bid": ticker.get("bid") or last_price,
                "ask": ticker.get("ask") or last_price,
                "bid_qty": bid_qty,
                "ask_qty": ask_qty,
                "timestamp": ticker.get("timestamp"),
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))

    async def _api_get_mark_price(self, symbol: str) -> float:
        try:
            ticker = await self.client.fetch_ticker(self._convert_symbol(symbol))
            info = ticker.get("info", {}) if isinstance(ticker, dict) else {}
            if "markPrice" in info and info["markPrice"] is not None:
                return float(info["markPrice"])
            return float(ticker.get("last", 0) or 0)
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
        price: Optional[float],
    ) -> Dict[str, Any]:
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            contracts = await self._to_contracts(symbol, quantity)

            # Best effort leverage setup (ignore idempotent errors)
            try:
                await self.client.set_leverage(leverage, ccxt_symbol)
            except Exception as e:
                msg = str(e).lower()
                if (
                    "not modified" not in msg
                    and "same" not in msg
                    and "supports only params[\"marginmode\"] = \"cross\"" not in msg
                    and "marginmode" not in msg
                    and "cross" not in msg
                ):
                    raise

            order_side = "buy" if side == PositionSide.LONG else "sell"
            order_type_str = "market" if order_type == OrderType.MARKET else "limit"

            params = self._maybe_test_flag({
                "marginMode": "ISOLATED",
                "leverage": leverage,
            })

            if order_type == OrderType.LIMIT and price is not None:
                order = await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=contracts,
                    price=price,
                    params=params,
                )
            else:
                order = await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=contracts,
                    params=params,
                )

            if self._is_test_order_mode():
                return {
                    "id": order.get("id", f"kucoin_test_open_{int(datetime.utcnow().timestamp())}"),
                    "symbol": ccxt_symbol,
                    "contracts": contracts,
                    "contractSize": 1,
                    "side": "long" if side == PositionSide.LONG else "short",
                    "entryPrice": float(price or 0),
                    "markPrice": float(price or 0),
                    "leverage": leverage,
                    "initialMargin": 0,
                    "liquidationPrice": 0,
                    "unrealizedPnl": 0,
                    "info": order,
                }

            positions = await self.client.fetch_positions([ccxt_symbol])
            position_data = next(
                (p for p in positions if float(p.get("contracts", 0) or 0) != 0),
                None,
            )
            return position_data or order
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to open position: {e}")

    async def _api_close_position(
        self,
        symbol: str,
        order_type: OrderType,
        price: Optional[float],
    ) -> Dict[str, Any]:
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            positions = await self.client.fetch_positions([ccxt_symbol])
            position = next(
                (p for p in positions if float(p.get("contracts", 0) or 0) != 0),
                None,
            )

            if position:
                pos_side = (position.get("side") or "").lower()
                close_side = "sell" if pos_side == "long" else "buy"
                contracts = float(position.get("contracts", 0) or 0)
            elif self._is_test_order_mode():
                close_side = "sell"
                contracts = 1.0
            else:
                raise ExchangeError("No open position found")

            order_type_str = "market" if order_type == OrderType.MARKET else "limit"
            params = self._maybe_test_flag({"reduceOnly": True})

            if order_type == OrderType.LIMIT and price is not None:
                order = await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=close_side,
                    amount=contracts,
                    price=price,
                    params=params,
                )
            else:
                order = await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=close_side,
                    amount=contracts,
                    params=params,
                )

            if self._is_test_order_mode():
                return {
                    "id": order.get("id", f"kucoin_test_close_{int(datetime.utcnow().timestamp())}"),
                    "symbol": ccxt_symbol,
                    "contracts": 0,
                    "contractSize": 1,
                    "side": "long",
                    "entryPrice": 0,
                    "markPrice": 0,
                    "leverage": 1,
                    "initialMargin": 0,
                    "liquidationPrice": 0,
                    "unrealizedPnl": 0,
                    "info": order,
                }

            updated_positions = await self.client.fetch_positions([ccxt_symbol])
            updated_position = next(
                (p for p in updated_positions if float(p.get("contracts", 0) or 0) != 0),
                None,
            )
            return updated_position or order
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
        reduce_only: bool,
    ) -> Dict[str, Any]:
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            contracts = await self._to_contracts(symbol, quantity)
            order_side = "buy" if side == OrderSide.BUY else "sell"
            order_type_str = "market" if order_type == OrderType.MARKET else "limit"
            params = self._maybe_test_flag({"reduceOnly": reduce_only})

            if order_type == OrderType.LIMIT and price is not None:
                return await self.client.create_order(
                    symbol=ccxt_symbol,
                    type=order_type_str,
                    side=order_side,
                    amount=contracts,
                    price=price,
                    params=params,
                )

            return await self.client.create_order(
                symbol=ccxt_symbol,
                type=order_type_str,
                side=order_side,
                amount=contracts,
                params=params,
            )
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to place order: {e}")

    async def _api_cancel_order(self, order_id: str, symbol: str) -> bool:
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
        try:
            await self.client.set_leverage(leverage, self._convert_symbol(symbol))
            return True
        except Exception as e:
            msg = str(e).lower()
            if (
                "not modified" in msg
                or "same" in msg
                or "supports only params[\"marginmode\"] = \"cross\"" in msg
                or ("marginmode" in msg and "cross" in msg)
            ):
                return True
            raise ExchangeError(f"Failed to set leverage: {e}")

    async def _api_set_margin_mode(self, mode: str) -> bool:
        # KuCoin requires symbol for strict margin-mode changes in many endpoints.
        # We keep this as best-effort and return success to avoid breaking flows that
        # set mode at order level via params.
        _ = mode
        return True

    async def _api_set_stop_loss(
        self,
        symbol: str,
        side: PositionSide,
        stop_price: float,
        quantity: Optional[float],
    ) -> Dict[str, Any]:
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            contracts = await self._to_contracts(symbol, quantity or 1.0)
            order_side = "sell" if side == PositionSide.LONG else "buy"
            params = self._maybe_test_flag({
                "stopLossPrice": stop_price,
                "reduceOnly": True,
            })
            return await self.client.create_order(
                symbol=ccxt_symbol,
                type="market",
                side=order_side,
                amount=contracts,
                params=params,
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
        quantity: Optional[float],
    ) -> Dict[str, Any]:
        try:
            ccxt_symbol = self._convert_symbol(symbol)
            contracts = await self._to_contracts(symbol, quantity or 1.0)
            order_side = "sell" if side == PositionSide.LONG else "buy"
            params = self._maybe_test_flag({
                "takeProfitPrice": take_profit_price,
                "reduceOnly": True,
            })
            return await self.client.create_order(
                symbol=ccxt_symbol,
                type="market",
                side=order_side,
                amount=contracts,
                params=params,
            )
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))
        except Exception as e:
            raise ExchangeError(f"Failed to set take profit: {e}")

    async def _api_get_balance(self) -> Dict[str, Any]:
        try:
            return await self.client.fetch_balance({"type": "swap"})
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))

    async def _api_get_positions(self, symbol: Optional[str]) -> List[Dict[str, Any]]:
        try:
            if symbol:
                positions = await self.client.fetch_positions([self._convert_symbol(symbol)])
            else:
                positions = await self.client.fetch_positions()
            return [p for p in positions if float(p.get("contracts", 0) or 0) != 0]
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))

    async def _api_get_position_by_symbol(self, symbol: str) -> Optional[Dict[str, Any]]:
        positions = await self._api_get_positions(symbol)
        return positions[0] if positions else None

    async def _api_get_account_info(self) -> Dict[str, Any]:
        return await self._api_get_balance()

    async def _api_get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        market = await self._ensure_market(symbol)
        limits = market.get("limits", {})
        precision = market.get("precision", {})
        return {
            "min_quantity": (limits.get("amount") or {}).get("min"),
            "max_quantity": (limits.get("amount") or {}).get("max"),
            "quantity_step": precision.get("amount"),
            "min_price": (limits.get("price") or {}).get("min"),
            "price_tick": precision.get("price"),
            "contract_size": market.get("contractSize", 1),
            "max_leverage": self._extract_max_leverage(market),
        }

    async def _api_get_funding_rate(self, symbol: str) -> Dict[str, Any]:
        try:
            funding = await self.client.fetch_funding_rate(self._convert_symbol(symbol))
            return {
                "symbol": symbol,
                "fundingRate": funding.get("fundingRate", 0),
                "nextFundingTime": funding.get("nextFundingTimestamp", 0),
            }
        except ccxt.RateLimitExceeded as e:
            raise RateLimitError(str(e))
        except ccxt.NetworkError as e:
            raise NetworkError(str(e))

    # ============================================
    # PARSERS
    # ============================================

    def _parse_position(self, data: Dict[str, Any]) -> Position:
        symbol = data.get("symbol", "")
        contracts = float(data.get("contracts", 0) or 0)
        side = (data.get("side") or "long").lower()
        entry_price = float(data.get("entryPrice", 0) or 0)

        return Position(
            id=f"kucoin_{symbol}_{int(datetime.utcnow().timestamp())}",
            pair=symbol,
            exchange1=self.exchange_name.value,
            exchange1_pos_id=data.get("id", ""),
            exchange1_side=side.upper(),
            exchange1_entry_price=entry_price,
            exchange1_current_price=float(data.get("markPrice", 0) or 0),
            exchange1_leverage=int(float(data.get("leverage", 1) or 1)),
            exchange2="",
            exchange2_pos_id="",
            exchange2_side="",
            exchange2_entry_price=0,
            exchange2_current_price=0,
            exchange2_leverage=1,
            quantity=abs(contracts),
            entry_time=datetime.utcnow().timestamp(),
            stop_loss_price=float(data.get("stopLossPrice", 0) or 0),
            take_profit_price=float(data.get("takeProfitPrice", 0) or 0),
            liquidation_price_ex1=float(data.get("liquidationPrice", 0) or 0),
            liquidation_price_ex2=0,
            status="OPEN" if contracts != 0 else "CLOSED",
            unrealized_pnl=float(data.get("unrealizedPnl", 0) or 0),
            initial_capital=float(data.get("initialMargin", 0) or 0),
        )

    def _parse_order(self, data: Dict[str, Any]) -> Order:
        side = (data.get("side") or "").upper()
        order_type = (data.get("type") or "").upper() or "MARKET"
        status = (data.get("status") or "OPEN").upper()

        return Order(
            id=data.get("id", "") or "",
            symbol=data.get("symbol", "") or "",
            exchange=self.exchange_name.value,
            side=side,
            order_type=order_type,
            quantity=float(data.get("amount", 0) or 0),
            price=float(data.get("price", 0) or 0),
            filled_quantity=float(data.get("filled", 0) or 0),
            status=status,
        )

    def _parse_orderbook(self, data: Dict[str, Any]) -> OrderBook:
        symbol = data.get("symbol", "")
        market = self.client.markets.get(symbol, {}) if hasattr(self.client, "markets") else {}
        contract_size = float(market.get("contractSize", 1) or 1)

        # KuCoin futures depth size is contract-oriented; normalize to base amount.
        bids = [
            (float(item[0]), float(item[1]) * contract_size)
            for item in data.get("bids", [])
        ]
        asks = [
            (float(item[0]), float(item[1]) * contract_size)
            for item in data.get("asks", [])
        ]

        return OrderBook(
            symbol=symbol,
            exchange=self.exchange_name.value,
            bids=bids,
            asks=asks,
            timestamp=float(data.get("timestamp", datetime.utcnow().timestamp())),
        )

    def _parse_price_data(self, data: Dict[str, Any]) -> PriceData:
        ts = data.get("timestamp")
        if ts is None:
            ts = datetime.utcnow().timestamp() * 1000
        return PriceData(
            symbol=data.get("symbol", ""),
            bid=float(data.get("bid", 0) or 0),
            ask=float(data.get("ask", 0) or 0),
            bid_qty=float(data.get("bid_qty", 0) or 0),
            ask_qty=float(data.get("ask_qty", 0) or 0),
            timestamp=float(ts) / 1000,
        )

    def _parse_balance(self, data: Dict[str, Any]) -> Balance:
        usdt = data.get("USDT") if isinstance(data, dict) else None
        if isinstance(usdt, dict):
            total = float(usdt.get("total", 0) or 0)
            free = float(usdt.get("free", 0) or 0)
            used = float(usdt.get("used", 0) or 0)
        else:
            total = float((data.get("total") or {}).get("USDT", 0) or 0)
            free = float((data.get("free") or {}).get("USDT", 0) or 0)
            used = float((data.get("used") or {}).get("USDT", 0) or 0)

        return Balance(
            exchange=self.exchange_name.value,
            total=total,
            available=free,
            margin_used=used,
            unrealized_pnl=0.0,
            timestamp=datetime.utcnow().timestamp(),
        )

    def _parse_funding_rate(self, data: Dict[str, Any]) -> FundingRate:
        rate = float(data.get("fundingRate", 0) or 0)
        next_ts = int(data.get("nextFundingTime", 0) or 0)

        if next_ts > 0:
            if next_ts > 4102444800:
                next_funding = datetime.fromtimestamp(next_ts / 1000, tz=timezone.utc)
            else:
                next_funding = datetime.fromtimestamp(next_ts, tz=timezone.utc)
        else:
            now = datetime.now(timezone.utc)
            next_funding = now.replace(minute=0, second=0, microsecond=0)
            while next_funding <= now:
                next_funding += timedelta(hours=8)

        return FundingRate(
            exchange=self.exchange_name.value,
            symbol=data.get("symbol", ""),
            rate=rate,
            rate_bps=rate * 10000,
            next_funding_time=next_funding,
            timestamp=datetime.now(timezone.utc).timestamp(),
        )

    # ============================================
    # WEBSOCKET STUBS
    # ============================================

    async def subscribe_orderbook(self, symbol: str, callback) -> None:
        _ = (symbol, callback)
        pass

    async def subscribe_position_updates(self, callback) -> None:
        _ = callback
        pass

    async def subscribe_order_updates(self, callback) -> None:
        _ = callback
        pass

    async def subscribe_account_updates(self, callback) -> None:
        _ = callback
        pass

    async def get_server_time(self) -> int:
        try:
            return await self.client.fetch_time()
        except Exception as e:
            raise ExchangeError(f"Failed to get server time: {e}")

    async def sync_time(self) -> None:
        """Sync local/exchange time offset used by ccxt."""
        try:
            server_time = await self.client.fetch_time()
            local_time = self.client.milliseconds()
            self.client.options["timeDifference"] = server_time - local_time
        except Exception as e:
            raise ExchangeError(f"Failed to sync time: {e}")
