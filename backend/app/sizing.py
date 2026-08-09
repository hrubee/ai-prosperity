"""Per-client position sizing. The brain decides direction + SL; each client's
size is computed HERE from their own equity (2% risk, global)."""
from .config import settings


def position_size_coins(equity_usd: float, entry_px: float, sl_px: float) -> float:
    """size = (equity * risk%) / |entry - sl|, in coin units.

    Returns 0 if inputs are unusable (caller skips with 'insufficient data').
    """
    if equity_usd <= 0 or entry_px <= 0 or sl_px <= 0:
        return 0.0
    sl_distance = abs(entry_px - sl_px)
    if sl_distance <= 0:
        return 0.0
    risk_amount = equity_usd * settings.risk_per_trade
    return risk_amount / sl_distance


def fno_lots(equity_inr: float, entry_px: float, sl_px: float, lot_size: int) -> tuple[int, int]:
    """Indian F&O 2% risk sizing, rounded DOWN to whole lots.

    For index/stock futures, 1 point of move = ₹1 per unit, so risk per lot =
    lot_size * |entry - sl|. Returns (lots, qty) where qty = lots * lot_size.
    Returns (0, 0) when a single lot would exceed the 2% risk budget.
    """
    if equity_inr <= 0 or entry_px <= 0 or sl_px <= 0 or lot_size <= 0:
        return 0, 0
    sl_distance = abs(entry_px - sl_px)
    if sl_distance <= 0:
        return 0, 0
    risk_amount = equity_inr * settings.risk_per_trade
    units = risk_amount / sl_distance
    lots = int(units // lot_size)
    return lots, lots * lot_size
