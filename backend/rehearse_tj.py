"""Weekend rehearsal of the Tradejini composite path (run on the VPS).

Without --inject: INSPECT only. Lists every connected Tradejini account with its
live equity, subscription state, and the lots a sample NIFTY signal WOULD size to
— and prints a SAFETY verdict (is it impossible for any connected account to
place a real order?).

With --inject: only if the safety check passes (no connected+subscribed account
could size >=1 lot), publishes ONE real venue=tradejini NIFTY signal to the bus so
the running worker exercises the full chain end-to-end. The owner's underfunded
account floors to 0 lots -> skip(insufficient) -> no order. Refuses to inject if
any account could actually trade.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)


def _load_env(path):
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(os.path.join(_HERE, ".env"))

from app import fno_instruments, kite_data, signal_bus, tradejini   # noqa: E402
from app.crypto import decrypt_secret                               # noqa: E402
from app.db import session_scope                                    # noqa: E402
from app.models import Subscription, TradejiniConnection, User      # noqa: E402
from app.nifty_brain import TIMEFRAME_MIN, _fetch_count, build_signal  # noqa: E402
from app.sizing import fno_lots                                     # noqa: E402

INJECT = "--inject" in sys.argv

# Sample NIFTY future signal (live price + ATR stop) — what Monday will look like.
nm = fno_instruments.resolve_near_month(fno_instruments._BY_KEY["NIFTY"])
full = kite_data.bars_for_token(nm.kite_token, TIMEFRAME_MIN, _fetch_count())
ref = full[-1][4]
atr = build_signal(full)["atr"]
sample_sl = round(ref - 1.5 * atr, 1)
dist = ref - sample_sl
print(f"sample signal: {nm.tj_symbol} buy ref={ref:.1f} sl={sample_sl:.1f} dist={dist:.1f} lot={nm.lot_size}\n")

print("=== connected Tradejini accounts ===")
could_trade = []
with session_scope() as db:
    rows = (db.query(TradejiniConnection, User)
            .join(User, User.id == TradejiniConnection.user_id)
            .filter(TradejiniConnection.status == "connected").all())
    if not rows:
        print("(none connected)")
    for conn, user in rows:
        sub = db.query(Subscription).filter(Subscription.user_id == user.id).one_or_none()
        sub_active = bool(sub and sub.is_active)
        try:
            cli = tradejini.TradejiniClient(decrypt_secret(conn.access_token_encrypted))
            equity = cli.equity_inr()
        except Exception as e:
            equity = -1.0
            print(f"  {user.email:32s} equity=ERR({str(e)[:40]}) sub_active={sub_active}")
            continue
        lots, qty = fno_lots(equity, ref, sample_sl, nm.lot_size)
        flag = "  <-- COULD PLACE A REAL ORDER" if (sub_active and lots >= 1) else ""
        print(f"  {user.email:32s} equity=Rs{equity:,.0f} paused={conn.paused} "
              f"sub_active={sub_active} -> would_size={lots}lot/{qty}{flag}")
        if sub_active and lots >= 1 and not conn.paused:
            could_trade.append(user.email)

safe = not could_trade
print(f"\nSAFETY: {'SAFE to inject (no connected account can place an order)' if safe else 'UNSAFE — ' + ', '.join(could_trade) + ' could trade; NOT injecting'}")

if INJECT:
    if not safe:
        print("REFUSING to inject — a connected account could place a real order.")
        sys.exit(1)
    with session_scope() as db:
        sig = signal_bus.publish_signal(db, nm.tj_symbol, "buy", sl_price=sample_sl,
                                        ref_price=ref, source="rehearsal", venue="tradejini")
        print(f"\nINJECTED signal id={sig.id} ({nm.tj_symbol} buy) — watch the worker consume it.")
else:
    print("\n(inspect only — pass --inject to drive one signal through the real worker path)")
