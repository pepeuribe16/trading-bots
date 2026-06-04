import requests
import json
import time
from datetime import datetime, timezone

# ── Config ────────────────────────────────────────────────────────────────────
with open("config.json") as f:
    cfg = json.load(f)

BASE    = cfg["alpaca"]["endpoint"]
DATA    = "https://data.alpaca.markets/v2"
HEADS   = {
    "APCA-API-KEY-ID":     cfg["alpaca"]["api_key"],
    "APCA-API-SECRET-KEY": cfg["alpaca"]["api_secret"],
}
S       = cfg["strategy"]
TICKERS = cfg["universe"]

TRADE_SIZE  = S["trade_size_usd"]
MAX_POS     = S["max_positions"]
STOP_PCT    = S["stop_loss_pct"] / 100
LOOKBACK    = S["breakout_lookback_days"]
VOL_MULT    = S["volume_multiplier"]
EXIT_MA     = S["exit_ma_days"]

STOPS_FILE  = "stop_levels.json"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ── Persistence: trailing stop levels per symbol ──────────────────────────────
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

def get_open_orders():
    r = requests.get(f"{BASE}/orders?status=open", headers=HEADS, timeout=10)
    r.raise_for_status()
    return r.json()

def place_market_buy(symbol, qty):
    body = {"symbol": symbol, "qty": qty, "side": "buy",
            "type": "market", "time_in_force": "day"}
    r = requests.post(f"{BASE}/orders", headers=HEADS, json=body, timeout=10)
    if r.ok:
        o = r.json()
        log(f"  BUY  {qty:>4} {symbol:<6} | ID: {o['id'][:8]}")
        return o
    log(f"  BUY FAILED {symbol}: {r.text[:100]}")
    return None

def place_market_sell(symbol, qty):
    body = {"symbol": symbol, "qty": qty, "side": "sell",
            "type": "market", "time_in_force": "day"}
    r = requests.post(f"{BASE}/orders", headers=HEADS, json=body, timeout=10)
    if r.ok:
        o = r.json()
        log(f"  SELL {qty:>4} {symbol:<6} | ID: {o['id'][:8]}")
        return o
    log(f"  SELL FAILED {symbol}: {r.text[:100]}")
    return None


# ── Market data ───────────────────────────────────────────────────────────────
def get_bars(symbols, days=25):
    """Returns {symbol: [bar, ...]} sorted oldest→newest."""
    results = {}
    # Batch in groups of 20 to avoid URL length limits
    for i in range(0, len(symbols), 20):
        batch = symbols[i:i+20]
        params = {
            "symbols": ",".join(batch),
            "timeframe": "1Day",
            "limit": days,
            "feed": "iex",
        }
        r = requests.get(f"{DATA}/stocks/bars", headers=HEADS, params=params, timeout=15)
        if r.ok:
            for sym, bars in r.json().get("bars", {}).items():
                results[sym] = bars
    return results


# ── Signal logic ──────────────────────────────────────────────────────────────
def compute_signal(bars):
    """
    Returns (is_breakout, current_price, stop_level) or None if insufficient data.
    Breakout = today's close > 20-day high AND today's volume > 1.5x avg volume.
    """
    if len(bars) < LOOKBACK + 1:
        return None

    closes  = [b["c"] for b in bars]
    volumes = [b["v"] for b in bars]

    current_close  = closes[-1]
    current_volume = volumes[-1]

    prior_closes   = closes[-(LOOKBACK+1):-1]   # 20 bars before today
    prior_volumes  = volumes[-(LOOKBACK+1):-1]

    prior_high     = max(prior_closes)
    avg_volume     = sum(prior_volumes) / len(prior_volumes)

    breakout = (current_close > prior_high) and (current_volume >= avg_volume * VOL_MULT)
    stop     = current_close * (1 - STOP_PCT)

    return breakout, current_close, stop, prior_high, avg_volume


def compute_exit_signal(bars):
    """Exit if price falls below 10-day moving average."""
    if len(bars) < EXIT_MA:
        return False
    ma = sum(b["c"] for b in bars[-EXIT_MA:]) / EXIT_MA
    current = bars[-1]["c"]
    return current < ma


# ── Main scan ──────────────────────────────────────────────────────────────────
def run_scan():
    log("=" * 55)
    log(f"MOMENTUM SCAN — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    stops     = load_stops()
    account   = get_account()
    positions = get_positions()
    bp        = float(account["buying_power"])
    cash      = float(account["cash"])

    log(f"Cash: ${cash:,.0f} | Buying power: ${bp:,.0f} | Open positions: {len(positions)}/{MAX_POS}")

    # ── Step 1: Manage existing positions ─────────────────────────────────────
    if positions:
        log(f"\nChecking {len(positions)} open positions...")
        symbols_held = list(positions.keys())
        bars_map     = get_bars(symbols_held, days=EXIT_MA + 2)

        for sym, pos in positions.items():
            qty         = int(float(pos["qty"]))
            price       = float(pos["current_price"])
            entry       = float(pos["avg_entry_price"])
            market_val  = float(pos["market_value"])
            unrealized  = float(pos["unrealized_plpc"]) * 100

            # Update trailing stop (only moves up)
            new_stop = price * (1 - STOP_PCT)
            old_stop = stops.get(sym, entry * (1 - STOP_PCT))
            stops[sym] = max(old_stop, new_stop)

            bars = bars_map.get(sym, [])
            below_ma = compute_exit_signal(bars)

            sign = "▲" if unrealized >= 0 else "▼"
            log(f"  {sym:<6} ${price:.2f} | {sign}{abs(unrealized):.1f}% | "
                f"Stop ${stops[sym]:.2f} | MA exit: {'YES' if below_ma else 'no'}")

            # Exit conditions
            if price <= stops[sym]:
                log(f"  → STOP HIT on {sym} — selling")
                place_market_sell(sym, qty)
                stops.pop(sym, None)
            elif below_ma:
                log(f"  → BELOW 10D MA on {sym} — selling")
                place_market_sell(sym, qty)
                stops.pop(sym, None)

    save_stops(stops)

    # ── Step 2: Scan for new breakouts ────────────────────────────────────────
    positions = get_positions()  # refresh after any sells
    num_open  = len(positions)
    slots     = MAX_POS - num_open

    if slots <= 0:
        log(f"\nNo open slots ({num_open}/{MAX_POS} positions). Skipping scan.")
        return

    if bp < TRADE_SIZE:
        log(f"\nInsufficient buying power (${bp:,.0f} < ${TRADE_SIZE:,}). Skipping scan.")
        return

    log(f"\nScanning {len(TICKERS)} tickers for breakouts ({slots} slots open)...")

    candidates = []
    bars_map   = get_bars(TICKERS, days=LOOKBACK + 5)

    for sym in TICKERS:
        if sym in positions:
            continue
        bars = bars_map.get(sym)
        if not bars:
            continue
        result = compute_signal(bars)
        if result is None:
            continue
        breakout, price, stop, prior_high, avg_vol = result
        if breakout:
            vol_ratio = bars[-1]["v"] / avg_vol
            candidates.append((sym, price, stop, prior_high, vol_ratio))

    if not candidates:
        log("No breakouts found this scan.")
    else:
        log(f"Found {len(candidates)} breakout(s):")
        # Sort by volume ratio (strongest breakouts first)
        candidates.sort(key=lambda x: x[4], reverse=True)

        bought = 0
        for sym, price, stop, prior_high, vol_ratio in candidates:
            if bought >= slots:
                break
            if bp < TRADE_SIZE:
                break

            qty = max(1, int(TRADE_SIZE / price))
            log(f"  ★ {sym:<6} ${price:.2f} | broke ${prior_high:.2f} high | "
                f"vol {vol_ratio:.1f}x avg | buying {qty} shares")
            order = place_market_buy(sym, qty)
            if order:
                stops[sym] = stop
                bp -= qty * price
                bought += 1

        save_stops(stops)

    # ── Summary ────────────────────────────────────────────────────────────────
    log(f"\nScan complete. Bought: {bought if candidates else 0} | "
        f"Positions now: {len(positions) + (bought if candidates else 0)}/{MAX_POS}")


# ── Entry point ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    log("Momentum Bot started — NASDAQ 100 Breakout Strategy")
    log(f"Settings: ${TRADE_SIZE}/trade | {MAX_POS} max positions | {STOP_PCT*100:.0f}% stop | "
        f"{LOOKBACK}d breakout | {VOL_MULT}x volume")

    while True:
        try:
            run_scan()
        except Exception as e:
            log(f"ERROR: {e}")
        log("Sleeping 30 minutes...\n")
        time.sleep(1800)
