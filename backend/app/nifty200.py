"""NIFTY 200 constituents — the AAA scanner's universe.

Source of truth is NSE's official index-constituent CSV (niftyindices.com), fetched
fresh on each scan so the universe self-updates on the semi-annual rebalances. If
that fetch ever fails, we fall back to the baked-in snapshot below so the scan
never goes dark. The fetched/fallback symbols are resolved to Kite instrument
tokens in kite_data.nifty200_equities().
"""
from __future__ import annotations

import csv
import io
import logging
import ssl
import urllib.request

log = logging.getLogger("nifty200")

NIFTY200_CSV_URL = "https://niftyindices.com/IndexConstituent/ind_nifty200list.csv"
# niftyindices.com WAFs non-browser agents — present as a browser.
_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/120.0.0.0 Safari/537.36")

# Baked-in snapshot of the NIFTY 200 (NSE trading symbols), used ONLY when the live
# CSV is unreachable. Captured 2026-06-22. The live fetch is preferred so this never
# silently goes stale.
NIFTY200_FALLBACK = [
    "360ONE", "ABB", "ABCAPITAL", "ADANIENSOL", "ADANIENT", "ADANIGREEN", "ADANIPORTS",
    "ADANIPOWER", "ALKEM", "AMBUJACEM", "APLAPOLLO", "APOLLOHOSP", "ASHOKLEY", "ASIANPAINT",
    "ASTRAL", "ATGL", "AUBANK", "AUROPHARMA", "AXISBANK", "BAJAJ-AUTO", "BAJAJFINSV",
    "BAJAJHLDNG", "BAJFINANCE", "BANKBARODA", "BANKINDIA", "BDL", "BEL", "BHARATFORG",
    "BHARTIARTL", "BHEL", "BIOCON", "BLUESTARCO", "BOSCHLTD", "BPCL", "BRITANNIA", "BSE",
    "CANBK", "CGPOWER", "CHOLAFIN", "CIPLA", "COALINDIA", "COCHINSHIP", "COFORGE", "COLPAL",
    "CONCOR", "COROMANDEL", "CUMMINSIND", "DABUR", "DIVISLAB", "DIXON", "DLF", "DMART",
    "DRREDDY", "EICHERMOT", "ENRIN", "ETERNAL", "EXIDEIND", "FEDERALBNK", "FORTIS", "GAIL",
    "GLENMARK", "GMRAIRPORT", "GODFRYPHLP", "GODREJCP", "GODREJPROP", "GRASIM", "GROWW",
    "GVT&D", "HAL", "HAVELLS", "HCLTECH", "HDFCAMC", "HDFCBANK", "HDFCLIFE", "HEROMOTOCO",
    "HINDALCO", "HINDPETRO", "HINDUNILVR", "HINDZINC", "HUDCO", "HYUNDAI", "ICICIAMC",
    "ICICIBANK", "ICICIGI", "IDEA", "IDFCFIRSTB", "INDHOTEL", "INDIANB", "INDIGO",
    "INDUSINDBK", "INDUSTOWER", "INFY", "IOC", "IRCTC", "IREDA", "IRFC", "ITC", "JINDALSTEL",
    "JIOFIN", "JSWENERGY", "JSWSTEEL", "JUBLFOOD", "KALYANKJIL", "KEI", "KOTAKBANK",
    "KPITTECH", "LAURUSLABS", "LENSKART", "LGEINDIA", "LICHSGFIN", "LODHA", "LT", "LTF",
    "LTM", "LUPIN", "M&M", "M&MFIN", "MANKIND", "MARICO", "MARUTI", "MAXHEALTH", "MAZDOCK",
    "MCX", "MFSL", "MOTHERSON", "MOTILALOFS", "MPHASIS", "MRF", "MUTHOOTFIN", "NATIONALUM",
    "NAUKRI", "NESTLEIND", "NHPC", "NMDC", "NTPC", "NYKAA", "OBEROIRLTY", "OFSS", "OIL",
    "ONGC", "PAGEIND", "PATANJALI", "PAYTM", "PERSISTENT", "PFC", "PHOENIXLTD", "PIDILITIND",
    "PIIND", "PNB", "POLICYBZR", "POLYCAB", "POWERGRID", "POWERINDIA", "PREMIERENE",
    "PRESTIGE", "RADICO", "RECLTD", "RELIANCE", "RVNL", "SAIL", "SBICARD", "SBILIFE", "SBIN",
    "SHREECEM", "SHRIRAMFIN", "SIEMENS", "SOLARINDS", "SRF", "SUNPHARMA", "SUPREMEIND",
    "SUZLON", "SWIGGY", "TATACAP", "TATACOMM", "TATACONSUM", "TATAELXSI", "TATAINVEST",
    "TATAPOWER", "TATASTEEL", "TCS", "TECHM", "TIINDIA", "TITAN", "TMCV", "TMPV",
    "TORNTPHARM", "TRENT", "TVSMOTOR", "ULTRACEMCO", "UNIONBANK", "UNITDSPR", "UPL", "VBL",
    "VEDL", "VMM", "VOLTAS", "WAAREEENER", "WIPRO", "YESBANK", "ZYDUSLIFE",
]


def _clean(sym: str) -> str:
    return (sym or "").strip().upper()


def fetch_symbols(timeout: float = 15.0) -> list[str]:
    """Current NIFTY 200 NSE symbols. Live CSV first; baked-in fallback on failure.
    Keeps only the EQ series. Never raises — always returns a usable list."""
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(NIFTY200_CSV_URL, headers={"User-Agent": _UA})
        raw = urllib.request.urlopen(req, timeout=timeout, context=ctx).read()
        rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8", "replace"))))
        syms = [_clean(r.get("Symbol")) for r in rows
                if (r.get("Series") or "").strip().upper() in ("EQ", "")]
        syms = [s for s in syms if s]
        if len(syms) >= 150:  # sanity floor — a truncated CSV shouldn't shrink the universe
            log.info("nifty200: fetched %d symbols from niftyindices", len(syms))
            return syms
        log.warning("nifty200: live CSV had only %d symbols; using baked-in fallback", len(syms))
    except Exception as e:
        log.warning("nifty200: live CSV fetch failed (%s); using baked-in fallback", e)
    return [_clean(s) for s in NIFTY200_FALLBACK]
