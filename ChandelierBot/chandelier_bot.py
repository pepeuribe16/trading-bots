"""
Chandelier Exit ATR Trailing Stop Bot
--------------------------------------
Entry  : 20-day high breakout + volume > 1.5x avg + price > 50-day MA
Stop   : highest_close_since_entry - 2.5 * ATR(14)  [only moves up]
Size   : risk 2% of portfolio per trade = $risk / stop_distance shares
Exit   : price closes below chandelier stop
"""
import os
import requests
import json
from datetime import datetime

# ── Config ─────────────────────────────────────────────────────────────────────
# Credentials come from GitHub Secrets (env vars); strategy/universe from config.json
_HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(_HERE, "config.json")) as f:
    cfg = json.load(f)

BASE    = cfg["alpaca"]["endpoint"]
DATA    = "https://data.alpaca.markets/v2"
HEADS   = {
    "APCA-API-KEY-ID":     os.environ["ALPACA_API_KEY"],
    "APCA-API-SECRET-KEY": os.environ["ALPACA_API_SECRET"],
}
S            = cfg["strategy"]
TICKERS      = cfg["universe"]
ATR_PERIOD   = S["atr_period"]
ATR_MULT     = S["atr_multiplier"]
BKOUT_DAYS   = S["breakout_days"]
MA_DAYS      = S["ma_trend_days"]
VOL_MULT     = S["volume_multiplier"]
RISK_PCT     = S["risk_per_trade_pct"] / 100
MAX_POS_PCT  = S["max_position_pct"] / 100
MAX_POS      = S["max_positions"]

STOPS_FILE   = os.path.join(_HERE, "chandelier_stops.json")
BARS_NEEDED  = max(MA_DAYS, BKOUT_DAYS) + ATR_PERIOD + 5  # ~70 bars


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ── State ─────────────────────────────────────────────────────────────────────
def load_stops():
    try:
        with open(STOPS_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_stops(stops):
    with open(STOPS_FILE, "w") as f:
        json.dump(stops, f, indent=2)


# ── Alpaca helpers ─────────────────────────────────────────────────────────────
def get_account():
    r = requests.get(f"{BASE}/account", headers=HEADS, timeout=10)
    r.raise_for_status()
    return r.json()

def get_positions():
    r = requests.get(f"{BASE}/positions", headers=HEADS, timeout=10)
    r.raise_for_status()
    return {p["symbol"]: p for p in r.json()}

def place_buy(symbol, qty):
    body = {"symbol": symbol, "qty": qty, "side": "buy",
            "type": "market", "time_in_force": "day"}
    r = requests.post(f"{BASE}/orders", headers=HEADS, json=body, timeout=10)
    if r.ok:
        log(f"  BUY  {qty:>4} {symbol:<6}")
        return r.json()
    log(f"  BUY FAILED {symbol}: {r.text[:80]}")
    return None

def place_sell(symbol, qty):
    body = {"symbol": symbol, "qty": qty, "side": "sell",
            "type": "market", "time_in_force": "day"}
    r = requests.post(f"{BASE}/orders", headers=HEADS, json=body, timeout=10)
    if r.ok:
        log(f"  SELL {qty:>4} {symbol:<6}")
        return r.json()
    log(f"  SELL FAILED {symbol}: {r.text[:80]}")
    return None


# ── Market data ───────────────────────────────────────────────────────────────
def get_bars_batch(symbols, limit=None):
    limit = limit or BARS_NEEDED
    results = {}
    for i in range(0, len(symbols), 15):
        batch = symbols[i:i+15]
        params = {"symbols": ",".join(batch), "timeframe": "1Day",
                  "limit": limit, "feed": "iex"}
        r = requests.get(f"{DATA}/stocks/bars", headers=HEADS,
                         params=params, timeout=20)
        if r.ok:
            for sym, bars in r.json().get("bars", {}).items():
                results[sym] = bars
    return results


# ── Indicators ────────────────────────────────────────────────────────────────
def calc_atr(bars, period=14):
    """Average True Range over `period` bars."""
    trs = []
    for i in range(1, len(bars)):
        h = bars[i]["h"]
        l = bars[i]["l"]
        pc = bars[i-1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < period:
        return None
    return sum(trs[-period:]) / period


def chandelier_stop(bars, atr):
    """Highest close over all bars minus ATR_MULT * ATR."""
    highest = max(b["c"] for b in bars)
    return highest - ATR_MULT * atr


def entry_signal(bars):
    """
    Returns (True, entry_price, stop, atr) if breakout conditions met, else None.
    Conditions:
      - Today's close > max close of prior 20 days
      - Today's volume >= 1.5x avg volume of prior 20 days
      - Today's close > 50-day simple MA
    """
    if len(bars) < BARS_NEEDED:
        return None

    today   = bars[-1]
    prior20 = bars[-(BKOUT_DAYS+1):-1]
    prior50 = bars[-MA_DAYS:]

    close   = today["c"]
    volume  = today["v"]

    high20  = max(b["c"] for b in prior20)
    avgvol  = sum(b["v"] for b in prior20) / len(prior20)
    ma50    = sum(b["c"] for b in prior50) / len(prior50)

    breakout     = close > high20
    vol_confirm  = volume >= avgvol * VOL_MULT
    trend_filter = close > ma50

    if not (breakout and vol_confirm and trend_filter):
        return None

    atr  = calc_atr(bars)
    if atr is None:
        return None

    stop = close - ATR_MULT * atr
    return close, stop, atr, volume / avgvol


# ── Position sizing ───────────────────────────────────────────────────────────
def calc_qty(portfolio_value, entry_price, stop_price):
    """Risk 2% of portfolio. Never exceed 20% of portfolio in one position."""
    risk_dollars  = portfolio_value * RISK_PCT
    stop_distance = entry_price - stop_price
    if stop_distance <= 0:
        return 0
    qty_by_risk   = int(risk_dollars / stop_distance)
    qty_by_cap    = int((portfolio_value * MAX_POS_PCT) / entry_price)
    return max(1, min(qty_by_risk, qty_by_cap))


# ── Main scan ──────────────────────────────────────────────────────────────────
def run_scan():
    log("=" * 58)
    log(f"CHANDELIER EXIT SCAN — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    stops     = load_stops()
    account   = get_account()
    positions = get_positions()
    portfolio = float(account["equity"])
    cash      = float(account["cash"])

    log(f"Portfolio: ${portfolio:,.0f} | Cash: ${cash:,.0f} | "
        f"Positions: {len(positions)}/{MAX_POS}")

    sells_done = 0
    buys_done  = 0

    # ── PART 1: Manage open positions ─────────────────────────────────────────
    if positions:
        log(f"\nManaging {len(positions)} positions...")
        bars_map = get_bars_batch(list(positions.keys()), limit=ATR_PERIOD + 5)

        for sym, pos in positions.items():
            qty    = int(float(pos["qty"]))
            price  = float(pos["current_price"])
            entry  = float(pos["avg_entry_price"])
            pnl    = (price - entry) / entry * 100

            bars = bars_map.get(sym, [])
            atr  = calc_atr(bars) if bars else None

            if atr:
                new_stop = chandelier_stop(bars, atr)
                old_stop = stops.get(sym, entry - ATR_MULT * atr)
                stops[sym] = max(old_stop, new_stop)  # only moves up
            else:
                stops[sym] = stops.get(sym, entry * 0.95)

            sign = "▲" if pnl >= 0 else "▼"
            log(f"  {sym:<6} ${price:.2f} {sign}{abs(pnl):.1f}% | "
                f"stop ${stops[sym]:.2f} | ATR {atr:.2f if atr else 'N/A'}")

            if price <= stops[sym]:
                log(f"  → CHANDELIER STOP HIT — selling {sym}")
                place_sell(sym, qty)
                stops.pop(sym, None)
                sells_done += 1

    save_stops(stops)

    # ── PART 2: Scan for entries ───────────────────────────────────────────────
    positions = get_positions()
    slots = MAX_POS - len(positions)

    if slots <= 0:
        log(f"\nNo slots available ({len(positions)}/{MAX_POS}). Skipping scan.")
        _print_summary(sells_done, buys_done)
        return

    if cash < 500:
        log(f"\nInsufficient cash (${cash:,.0f}). Skipping scan.")
        _print_summary(sells_done, buys_done)
        return

    log(f"\nScanning {len(TICKERS)} tickers ({slots} slots open)...")
    candidates = []
    bars_map   = get_bars_batch(TICKERS)

    for sym in TICKERS:
        if sym in positions:
            continue
        bars = bars_map.get(sym)
        if not bars:
            continue
        result = entry_signal(bars)
        if result:
            price, stop, atr, vol_ratio = result
            candidates.append((sym, price, stop, atr, vol_ratio))

    if not candidates:
        log("No breakouts found this scan.")
    else:
        candidates.sort(key=lambda x: x[4], reverse=True)
        log(f"Found {len(candidates)} breakout(s):")

        for sym, price, stop, atr, vol_ratio in candidates:
            if buys_done >= slots or cash < price:
                break
            qty = calc_qty(portfolio, price, stop)
            cost = qty * price
            if cost > cash:
                qty = max(1, int(cash / price))
                cost = qty * price
            risk = qty * (price - stop)
            log(f"  ★ {sym:<6} ${price:.2f} | ATR {atr:.2f} | "
                f"stop ${stop:.2f} | vol {vol_ratio:.1f}x | "
                f"qty {qty} | risk ${risk:.0f}")
            if place_buy(sym, qty):
                stops[sym] = stop
                cash -= cost
                buys_done += 1

        save_stops(stops)

    _print_summary(sells_done, buys_done)


def _print_summary(sells, buys):
    log(f"\nDone — Sells: {sells} | Buys: {buys}")


# ── Entry point (single run for GitHub Actions) ────────────────────────────────
if __name__ == "__main__":
    log("Chandelier Exit Bot — single run")
    log(f"ATR({ATR_PERIOD}) × {ATR_MULT} | Risk {RISK_PCT*100:.0f}%/trade | "
        f"Max {MAX_POS} positions | {BKOUT_DAYS}d breakout + MA{MA_DAYS} filter")
    run_scan()
