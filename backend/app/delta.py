"""Per-client Delta Exchange (India) client.

Self-contained — it does NOT import the live trading brain's adapter, so the
production system is never coupled to this SaaS code. It does reuse the proven
patterns hardened in the brain: contract-size normalization (Delta India futures
use contractSize<1, e.g. 0.01 for ETH), reduce-only closes, and trigger-aware
stop handling.

Each instance is constructed with ONE client's API key/secret. Price reference
for sizing comes from the public Binance feed (same source the brain uses), so
sizing is consistent across clients regardless of Delta's thin book.
"""
from __future__ import annotations

import ccxt  # type: ignore

from .config import settings

_INDIA_MAINNET = "https://api.india.delta.exchange"
_INDIA_TESTNET = "https://cdn-ind.testnet.deltaex.org"

# One shared public Binance client for price reference (no keys needed).
_public = ccxt.binance({"enableRateLimit": True})


class DeltaError(Exception):
    pass


class DeltaClient:
    def __init__(self, api_key: str, api_secret: str, sandbox: bool | None = None):
        self.sandbox = settings.delta_sandbox if sandbox is None else sandbox
        ex = ccxt.delta(
            {
                "apiKey": api_key,
                "secret": api_secret,
                "enableRateLimit": True,
                "options": {"fetchCurrencies": False},
            }
        )
        url = _INDIA_TESTNET if self.sandbox else _INDIA_MAINNET
        if self.sandbox:
            ex.set_sandbox_mode(True)
        ex.urls["api"] = {"public": url, "private": url}
        self.ex = ex
        self._markets_loaded = False

    # ── helpers ────────────────────────────────────────────────
    def _load(self) -> None:
        if not self._markets_loaded:
            try:
                self.ex.load_markets()
            except Exception:
                pass
            self._markets_loaded = True

    def pair(self, base: str) -> str:
        """Resolve base coin (e.g. 'BTC') to the Delta perpetual symbol."""
        base = base.split("/")[0] if "/" in base else base
        self._load()
        for sym, m in (self.ex.markets or {}).items():
            if m.get("swap") and m.get("base") == base:
                return sym
        return f"{base}/USD:USD"

    @staticmethod
    def ref_price(base: str) -> float:
        base = base.split("/")[0] if "/" in base else base
        try:
            t = _public.fetch_ticker(f"{base}/USDT:USDT")
            return float(t.get("last") or 0) or 0.0
        except Exception:
            try:
                t = _public.fetch_ticker(f"{base}/USDT")
                return float(t.get("last") or 0) or 0.0
            except Exception:
                return 0.0

    def _contract_size(self, pair: str) -> float:
        try:
            return float(self.ex.market(pair).get("contractSize", 1.0) or 1.0)
        except Exception:
            return 1.0

    @staticmethod
    def _normalize_fill(order: dict, contract_size: float) -> dict:
        if not order or contract_size == 1.0:
            return order
        for k in ("filled", "amount", "remaining"):
            v = order.get(k)
            if v is not None:
                try:
                    order[k] = float(v) * contract_size
                except (TypeError, ValueError):
                    pass
        return order

    # ── account ────────────────────────────────────────────────
    def validate(self) -> bool:
        """Confirm the key works AND our IP is whitelisted (a private read call)."""
        try:
            self.ex.fetch_balance(params={"type": "future"})
            return True
        except Exception as e:
            raise DeltaError(str(e))

    def equity_usd(self) -> float:
        try:
            bal = self.ex.fetch_balance(params={"type": "future"})
        except Exception as e:
            raise DeltaError(f"equity_usd: {e}")
        total = bal.get("total") or {}
        try:
            return float(total.get("USDT") or total.get("USD") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def open_positions(self) -> list[dict]:
        out = []
        try:
            raw_positions = self.ex.fetch_positions(params={"type": "future"}) or []
        except Exception as e:
            raise DeltaError(f"open_positions: {e}")
        for p in raw_positions:
            try:
                contracts = float(p.get("contracts") or p.get("amount") or 0)
            except (TypeError, ValueError):
                continue
            if abs(contracts) < 1e-12:
                continue
            sym = p.get("symbol") or ""
            base = sym.split("/")[0] if "/" in sym else sym
            cs = self._contract_size(sym)
            out.append(
                {
                    "base": base,
                    "side": (p.get("side") or "").lower(),
                    "coin_size": abs(contracts) * cs,
                    "entry": float(p.get("entryPrice") or 0),
                    "symbol": sym,
                }
            )
        return out

    # ── trading ────────────────────────────────────────────────
    def market_order(self, base: str, side: str, coin_size: float, reduce_only: bool = False) -> dict:
        """Place a market order; coin_size is in COIN units. Returns a fill dict
        with size/price/fee in coin/USD units."""
        self._load()
        pair = self.pair(base)
        cs = self._contract_size(pair)
        contracts = coin_size / cs if cs else coin_size
        amount = float(self.ex.amount_to_precision(pair, contracts))
        params = {"type": "future"}
        if reduce_only:
            params["reduce_only"] = True
        try:
            raw = self.ex.create_market_order(pair, side, amount, params=params)
        except Exception as e:
            raise DeltaError(f"market_order: {e}")
        raw = self._normalize_fill(raw, cs)
        return {
            "delta_order_id": str(raw.get("id", "")),
            "filled": float(raw.get("filled", 0) or 0),
            "avg_px": float(raw.get("average", 0) or 0),
            "fee": _extract_fee(raw),
        }

    def place_stop_loss(self, base: str, position_side: str, coin_size: float, sl_price: float) -> dict:
        """Reduce-only stop on the opposite side of the position."""
        self._load()
        pair = self.pair(base)
        cs = self._contract_size(pair)
        contracts = coin_size / cs if cs else coin_size
        amount = float(self.ex.amount_to_precision(pair, contracts))
        px = float(self.ex.price_to_precision(pair, sl_price))
        close_side = "buy" if position_side in ("short", "sell") else "sell"
        # Delta enum is stop_loss_order / take_profit_order (NOT *_market); use
        # snake_case reduce_only. Verified on testnet.
        params = {
            "type": "future",
            "stop_price": px,
            "stop_order_type": "stop_loss_order",
            "reduce_only": True,
        }
        try:
            order = self.ex.create_order(pair, "market", close_side, amount, price=None, params=params)
        except Exception as e:
            raise DeltaError(f"place_stop_loss: {e}")
        return {"delta_order_id": str(order.get("id", "")), "trigger_px": px}

    def close_all(self, base: str) -> dict:
        """Reduce-only close of the entire position in `base`."""
        self._load()
        pair = self.pair(base)
        try:
            positions = self.ex.fetch_positions([pair], params={"type": "future"})
            results = []
            for pos in positions:
                amount = abs(float(pos.get("contracts", 0) or pos.get("amount", 0) or 0))
                if amount <= 0:
                    continue
                pos_side = (pos.get("side") or "").lower()
                close_side = "sell" if pos_side in ("long", "buy") else "buy"
                amount = float(self.ex.amount_to_precision(pair, amount))
                results.append(
                    self.ex.create_market_order(
                        pair, close_side, amount, params={"type": "future", "reduce_only": True}
                    )
                )
        except Exception as e:
            raise DeltaError(f"close_all: {e}")
        return {"closed": len(results)}


def _extract_fee(order: dict) -> float:
    fee = order.get("fee") or {}
    try:
        if fee and fee.get("cost") is not None:
            return abs(float(fee["cost"]))
        fees = order.get("fees") or []
        return abs(sum(float(f.get("cost", 0) or 0) for f in fees))
    except (TypeError, ValueError):
        return 0.0
