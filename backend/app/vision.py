"""AI Vision congestion filter for the F&O brain.

Mirrors the crypto brain's vision stage (shared_scripts/ai_market_gate.py): on a
fresh EMA crossover, render a candlestick chart with the EMAs and a bright red
dot at every crossover, then ask an NVIDIA NIM Llama-3.2-vision model whether the
cross is a genuine breakout or a choppy whipsaw. We use it as a VETO only.

Deliberate divergence from the crypto brain: the crypto path also wires the
model's *visual* stop-loss into the live order. We do NOT do that here. F&O sizes
in whole lots off high per-lot margin, so a hallucinated-tight or wrong-side
visual SL would either inflate the lot count into a margin rejection or stop the
client out instantly. The ATR stop (bounded and sane by construction) sets the
SL; Vision only blocks congested entries. (Revisit once we trust the model on
Indian index futures.)
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import ssl
import urllib.request

log = logging.getLogger("fno-vision")

_NVIDIA_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
_MODEL = "meta/llama-3.2-11b-vision-instruct"


def _ctx() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        try:
            return ssl.create_default_context()
        except Exception:
            return ssl._create_unverified_context()


_CTX = _ctx()


def _matplotlib_ok() -> bool:
    try:
        import matplotlib  # noqa: F401
        return True
    except Exception:
        return False


def vision_available() -> bool:
    """True when both the API key and the chart toolkit are present."""
    return bool(os.environ.get("NVIDIA_API_KEY")) and _matplotlib_ok()


# ── indicators ─────────────────────────────────────────────────
def _ema_full(closes: list[float], period: int) -> list:
    """EMA aligned 1:1 to `closes` (None during warmup), SMA-seeded."""
    n = len(closes)
    out: list = [None] * n
    if n < period:
        return out
    k = 2.0 / (period + 1)
    ema = sum(closes[:period]) / period
    out[period - 1] = ema
    for i in range(period, n):
        ema = closes[i] * k + ema * (1 - k)
        out[i] = ema
    return out


# ── chart ──────────────────────────────────────────────────────
def render_crossover_chart(bars: list[list], ema_fast: int, ema_slow: int,
                           filename: str, symbol: str, n_bars: int, tf_min: int) -> int:
    """Render the last ~n_bars candles of `bars` ([ts,o,h,l,c]) with EMA_fast
    (orange), EMA_slow (blue) and a red dot at every EMA crossover. EMAs are
    computed over the FULL series so they're defined across the visible window.
    Returns the number of red dots (crossovers) drawn. Mirrors the crypto brain's
    chart style for model consistency."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as patches

    closes = [b[4] for b in bars]
    ema_f = _ema_full(closes, ema_fast)
    ema_s = _ema_full(closes, ema_slow)

    n = min(n_bars, len(bars))
    bslice = bars[-n:]
    fslice = ema_f[-n:]
    sslice = ema_s[-n:]

    fig, ax = plt.subplots(figsize=(16, 8), dpi=100)
    ax.set_facecolor("#111217")
    fig.patch.set_facecolor("#111217")
    ax.grid(True, color="#2c2d35", linestyle="--", linewidth=0.5)

    # Candles are intentionally DE-EMPHASISED: hollow (no fill) and faint, so the
    # model weights the EMA lines and the crossover dots far above raw price bars.
    for i, (_, o, h, l, c) in enumerate(bslice):
        hue = "#2e7d5b" if c >= o else "#8a3a55"  # dim green / dim rose
        ax.vlines(i, l, h, color=hue, linewidth=0.5, alpha=0.28, zorder=1)
        body_bottom = min(o, c)
        body_height = max(abs(c - o), 0.0001)
        ax.add_patch(patches.Rectangle((i - 0.3, body_bottom), 0.6, body_height,
                                       facecolor="none", edgecolor=hue,
                                       linewidth=0.7, alpha=0.40, zorder=1))

    # EMA lines PROMINENT: a soft wide glow underneath + a thick bright line on top.
    xs = list(range(n))
    fx = [(i, v) for i, v in zip(xs, fslice) if v is not None]
    sx = [(i, v) for i, v in zip(xs, sslice) if v is not None]
    if fx:
        fxx, fyy = [p[0] for p in fx], [p[1] for p in fx]
        ax.plot(fxx, fyy, color="#f0a30a", linewidth=7, alpha=0.16, zorder=4, solid_capstyle="round")
        ax.plot(fxx, fyy, color="#ffb52e", linewidth=2.8, zorder=6, solid_capstyle="round",
                label=f"EMA {ema_fast}")
    if sx:
        sxx, syy = [p[0] for p in sx], [p[1] for p in sx]
        ax.plot(sxx, syy, color="#1ba1e2", linewidth=7, alpha=0.16, zorder=4, solid_capstyle="round")
        ax.plot(sxx, syy, color="#41b6ff", linewidth=3.4, zorder=6, solid_capstyle="round",
                label=f"EMA {ema_slow}")

    # Crossover dots VERY PROMINENT: a large translucent red halo + a big solid dot
    # with a white ring, drawn above everything.
    dots = 0
    labelled = False
    for i in range(1, n):
        pf, ps, cf, cs = fslice[i - 1], sslice[i - 1], fslice[i], sslice[i]
        if None in (pf, ps, cf, cs):
            continue
        if (pf <= ps and cf > cs) or (pf >= ps and cf < cs):
            y = (cf + cs) / 2
            ax.scatter(i, y, s=1500, color="#ff073a", alpha=0.20, zorder=8, edgecolors="none")
            ax.scatter(i, y, s=580, color="#ff2b4d", zorder=11, edgecolors="white", linewidths=2.6,
                       label=("EMA Crossover" if not labelled else ""))
            labelled = True
            dots += 1

    sessions = n / max(1, (375 // max(1, tf_min)))
    ax.legend(facecolor="#111217", edgecolor="#2c2d35", labelcolor="white")
    ax.set_title(f"{symbol} — last {n} {tf_min}m bars (~{sessions:.1f} sessions)",
                 color="white", fontsize=12, fontweight="bold")
    for spine in ax.spines.values():
        spine.set_color("#2c2d35")
    ax.tick_params(colors="white")
    plt.tight_layout()
    plt.savefig(filename, facecolor=fig.get_facecolor(), edgecolor="none", bbox_inches="tight")
    plt.close(fig)
    return dots


# ── model call (veto only) ─────────────────────────────────────
def vision_veto(symbol: str, image_path: str, side: str, current_price: float,
                n_bars: int, tf_min: int) -> tuple[bool, str, float]:
    """Ask Llama-vision whether this fresh cross is congestion. Returns
    (congested, reason, visual_sl). Only `congested` is acted on; visual_sl is
    returned for logging/telemetry, never placed."""
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError("NVIDIA_API_KEY missing")
    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("utf-8")

    sessions = n_bars / max(1, (375 // max(1, tf_min)))
    prompt = f"""
You are a senior professional technical analyst reviewing an Indian index-future
candlestick chart for {symbol}. The chart shows the last {n_bars} {tf_min}-minute
bars — roughly {sessions:.1f} Indian trading sessions (each session runs
09:15-15:30 IST). NOTE: the market is closed overnight and on weekends, so the
visible vertical price jumps BETWEEN sessions are normal overnight gaps, NOT
crossovers — judge trend only from the EMA lines and the candle structure.

We are evaluating entry for a {side.upper()} position at the current price {current_price}.

CRITICAL INTERSECTION RULE (RED DOT DETECTION):
- Every crossover between the orange (EMA fast) and blue (EMA slow) lines has been
  programmatically marked with a bright RED DOT.
- Scan the whole chart left to right and COUNT the RED DOTS.
- If there are 3 or more RED DOTS, the market is in a choppy sideways whipsaw
  range: you MUST set "congestion": true and veto the entry, no matter how clean
  the most recent breakout at the far right looks.

Output a clean JSON object deciding:
1. Is this active crossover a CONGESTION CROSS (false whipsaw inside a flat choppy
   range, "congestion": true) or a GENUINE TREND CROSS (high-probability breakout
   where the EMAs fan out cleanly, "congestion": false)?
2. If genuine ("congestion": false), give the optimal visual stop-loss:
   - BUY: just below the nearest major swing low / consolidation base.
   - SELL: just above the nearest major swing high / consolidation peak.
   - It must be a realistic level readable on the chart's Y-axis price scale.

Genuine Trend Crossover: fewer than 3 RED DOTS, EMAs separating/fanning out,
strong directional candles, clear higher-highs/lows (long) or lower-highs/lows
(short).
Congestion Crossover: 3+ RED DOTS, EMAs flat/overlapping/intertwined, dense
recent whipsaws.

Output ONLY a raw JSON block, no prose, starting with "{{":
{{"congestion": true|false, "stop_loss_price": float, "reason": "short explanation counting the red dots and EMA flatness"}}
"""
    payload = {
        "model": _MODEL,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]}],
        "temperature": 0.1,
        "max_tokens": 500,
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(_NVIDIA_URL, data=body, method="POST", headers={
        "Authorization": f"Bearer {key}", "Content-Type": "application/json",
        "Accept": "application/json"})
    try:
        resp = json.loads(urllib.request.urlopen(req, timeout=60, context=_CTX).read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"NVIDIA HTTP {e.code}: {e.read().decode()[:160]}")
    except Exception as e:
        raise RuntimeError(f"NVIDIA call error: {e}")

    content = (resp.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    congested, visual_sl, reason = False, 0.0, "no reason parsed"
    m = re.search(r"\{.*\}", content, re.DOTALL)
    block = m.group(0) if m else content
    try:
        a = json.loads(block)
        c = a.get("congestion", False)
        congested = (c.lower() == "true") if isinstance(c, str) else bool(c)
        visual_sl = float(a.get("stop_loss_price", 0.0) or 0.0)
        reason = str(a.get("reason", "")).replace("\n", " ").strip() or "no reason"
    except Exception:
        cm = re.search(r'"congestion"\s*:\s*(true|false)', block, re.IGNORECASE)
        if cm:
            congested = cm.group(1).lower() == "true"
        sm = re.search(r'"stop_loss_price"\s*:\s*([0-9.]+)', block)
        if sm:
            try:
                visual_sl = float(sm.group(1))
            except ValueError:
                pass
        rm = re.search(r'"reason"\s*:\s*"(.*?)"', block, re.DOTALL)
        reason = (rm.group(1).replace("\n", " ").strip() if rm
                  else f"unparsed: {content[:120]}")
    return congested, reason, visual_sl
