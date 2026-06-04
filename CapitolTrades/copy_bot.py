import requests
import json
import time
from datetime import datetime, timedelta, timezone

# ── Config ────────────────────────────────────────────────────────────────────
with open("config.json") as f:
    cfg = json.load(f)

ALPACA_BASE  = cfg["alpaca"]["endpoint"]
ALPACA_HEADS = {
    "APCA-API-KEY-ID":     cfg["alpaca"]["api_key"],
    "APCA-API-SECRET-KEY": cfg["alpaca"]["api_secret"],
}
FMP_KEY      = cfg["congress_data"]["fmp_api_key"]
POLITICIAN   = cfg["congress_data"]["politician"]
TRADE_SIZE   = cfg["strategy"]["trade_size_usd"]
MAX_POS      = cfg["strategy"]["max_position_usd"]
MAX_AGE_DAYS = cfg["strategy"]["max_days_since_disclosure"]

SEEN_FILE = "seen_trades.json"


def log(msg):
    print(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}")


# ── Persistence: track trades we've already copied ───────────────────────────
def load_seen():
    try:
        with open(SEEN_FILE) as f:
            return set(json.load(f))
    except FileNotFoundError:
        return set()

def save_seen(seen):
    with open(SEEN_FILE, "w") as f:
        json.dump(list(seen), f)


# ── FMP: fetch recent politician trades ───────────────────────────────────────
def fetch_politician_trades():
    """Returns list of recent trades for the configured politician."""
    url = f"https://financialmodelingprep.com/api/v4/senate-trading"
    params = {"apikey": FMP_KEY}
    r = requests.get(url, params=params, timeout=10)
    r.raise_for_status()
    all_trades = r.json()

    # Also fetch house trades
    url2 = f"https://financialmodelingprep.com/api/v4/house-trading"
    r2 = requests.get(url2, params={"apikey": FMP_KEY}, timeout=10)
    if r2.ok:
        all_trades += r2.json()

    # Filter to our politician
    name = POLITICIAN.lower()
    matches = [t for t in all_trades if name in t.get("representative", "").lower()]

    # Filter to recent disclosures only
    cutoff = datetime.now(timezone.utc) - timedelta(days=MAX_AGE_DAYS)
    fresh = []
    for t in matches:
        date_str = t.get("dateRecieved") or t.get("disclosureDate") or ""
        try:
            d = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            if d >= cutoff:
                fresh.append(t)
        except Exception:
            pass

    return fresh


# ── Alpaca helpers ─────────────────────────────────────────────────────────────
def get_price(symbol):
    r = requests.get(
        f"https://data.alpaca.markets/v2/stocks/{symbol}/trades/latest",
        headers=ALPACA_HEADS, timeout=10
    )
    if r.ok:
        return r.json()["trade"]["p"]
    return None


def get_position(symbol):
    r = requests.get(f"{ALPACA_BASE}/positions/{symbol}", headers=ALPACA_HEADS, timeout=10)
    if r.status_code == 200:
        return r.json()
    return None


def get_account():
    r = requests.get(f"{ALPACA_BASE}/account", headers=ALPACA_HEADS, timeout=10)
    r.raise_for_status()
    return r.json()


def place_market_order(symbol, side, qty):
    body = {
        "symbol": symbol,
        "qty": qty,
        "side": side,
        "type": "market",
        "time_in_force": "day",
    }
    r = requests.post(f"{ALPACA_BASE}/orders", headers=ALPACA_HEADS, json=body, timeout=10)
    if r.ok:
        o = r.json()
        log(f"  ORDER PLACED: {side.upper()} {qty} {symbol} | ID: {o['id']}")
        return o
    else:
        log(f"  ORDER FAILED: {r.text}")
        return None


# ── Core logic: decide what to do with each trade ────────────────────────────
def process_trade(trade, seen):
    symbol = (trade.get("ticker") or trade.get("asset") or "").strip().upper()
    action = (trade.get("type") or trade.get("transactionType") or "").lower()
    amount_str = trade.get("amount") or "$1,001 - $15,000"
    trade_id = trade.get("id") or f"{symbol}-{action}-{trade.get('transactionDate','')}"

    if not symbol or symbol in ("--", "N/A", ""):
        return
    if trade_id in seen:
        return

    # Skip options for now (config: copy_options = false)
    asset_type = (trade.get("assetType") or "").lower()
    if "option" in asset_type and not cfg["strategy"]["copy_options"]:
        log(f"  SKIP {symbol}: options not enabled")
        seen.add(trade_id)
        return

    # Determine side
    if "purchase" in action or "buy" in action:
        side = "buy"
    elif "sale" in action or "sell" in action:
        side = "sell"
    else:
        log(f"  SKIP {symbol}: unknown action '{action}'")
        seen.add(trade_id)
        return

    log(f"New trade: {POLITICIAN} {side.upper()} {symbol}")

    # Get current price
    price = get_price(symbol)
    if not price:
        log(f"  SKIP: could not get price for {symbol}")
        seen.add(trade_id)
        return

    if side == "buy":
        # Check buying power
        account = get_account()
        bp = float(account.get("buying_power", 0))
        if bp < TRADE_SIZE:
            log(f"  SKIP: insufficient buying power (${bp:.0f})")
            seen.add(trade_id)
            return

        # Check existing position doesn't exceed max
        pos = get_position(symbol)
        current_value = float(pos["market_value"]) if pos else 0
        if current_value >= MAX_POS:
            log(f"  SKIP: position already at max (${current_value:.0f})")
            seen.add(trade_id)
            return

        qty = max(1, int(TRADE_SIZE / price))
        place_market_order(symbol, "buy", qty)

    elif side == "sell":
        pos = get_position(symbol)
        if not pos:
            log(f"  SKIP: no position in {symbol} to sell")
            seen.add(trade_id)
            return

        qty = int(float(pos["qty"]))
        if qty <= 0:
            seen.add(trade_id)
            return

        place_market_order(symbol, "sell", qty)

    seen.add(trade_id)


# ── Main loop ─────────────────────────────────────────────────────────────────
def run():
    if FMP_KEY == "YOUR_FMP_KEY_HERE":
        print("=" * 60)
        print("ACTION NEEDED: Add your FMP API key to config.json")
        print("Get a free key at: https://financialmodelingprep.com/register")
        print("=" * 60)
        return

    log(f"Capitol Trades Copy Bot started — following: {POLITICIAN}")
    log(f"Trade size: ${TRADE_SIZE} | Max position: ${MAX_POS}")

    seen = load_seen()

    while True:
        try:
            log("Checking for new trades...")
            trades = fetch_politician_trades()
            log(f"Found {len(trades)} recent trades from {POLITICIAN}")

            for trade in trades:
                process_trade(trade, seen)

            save_seen(seen)

        except Exception as e:
            log(f"Error: {e}")

        log("Sleeping 30 minutes...")
        time.sleep(1800)


if __name__ == "__main__":
    run()
