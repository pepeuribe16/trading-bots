import requests
import time
import json
from datetime import datetime

# --- Config ---
with open("alpaca_config.json") as f:
    cfg = json.load(f)

BASE = cfg["endpoint"]
HEADERS = {
    "APCA-API-KEY-ID": cfg["api_key"],
    "APCA-API-SECRET-KEY": cfg["api_secret"],
}

SYMBOL         = "TSLA"
BUY_ORDER_ID   = "3edbb852-2d46-42cd-97c4-5b87982aee90"
TRAIL_PERCENT  = 5.0
CHECK_INTERVAL = 30  # seconds

# Multi-level ladder: (drop_pct_threshold, shares_to_buy)
# Buys more shares the lower the price goes — lowers avg cost each time.
LADDERS = [
    (10.0,  5),   # -10% → buy  5 shares
    (20.0, 15),   # -20% → buy 15 shares
    (35.0, 25),   # -35% → buy 25 shares
]

# --- State ---
entry_price      = None
high_water_mark  = None
trailing_stop_id = None
ladders_hit      = set()  # tracks which ladder levels have fired


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def get(path):
    r = requests.get(BASE + path, headers=HEADERS)
    r.raise_for_status()
    return r.json()


def post(path, body):
    r = requests.post(BASE + path, headers=HEADERS, json=body)
    r.raise_for_status()
    return r.json()


def latest_price():
    r = requests.get(
        f"https://data.alpaca.markets/v2/stocks/{SYMBOL}/trades/latest",
        headers=HEADERS
    )
    r.raise_for_status()
    return r.json()["trade"]["p"]


def place_trailing_stop(qty):
    order = post("/orders", {
        "symbol": SYMBOL,
        "qty": qty,
        "side": "sell",
        "type": "trailing_stop",
        "time_in_force": "gtc",
        "trail_percent": TRAIL_PERCENT,
    })
    log(f"Trailing stop placed: {qty} shares @ trail {TRAIL_PERCENT}% | ID: {order['id']}")
    return order["id"]


def place_ladder_buy(qty, level_pct):
    order = post("/orders", {
        "symbol": SYMBOL,
        "qty": qty,
        "side": "buy",
        "type": "market",
        "time_in_force": "day",
    })
    log(f"LADDER -{level_pct}%: bought {qty} shares at market | ID: {order['id']}")
    return order["id"]


def print_status(price, drop_pct, gain_pct, total_qty):
    sign = "▲" if gain_pct >= 0 else "▼"
    next_ladders = [f"-{p}% → {q}sh" for p, q in LADDERS if p not in ladders_hit]
    log(
        f"TSLA ${price:.2f} | Entry ${entry_price:.2f} | {sign}{abs(gain_pct):.1f}% | "
        f"HWM ${high_water_mark:.2f} | Floor ${high_water_mark * (1 - TRAIL_PERCENT/100):.2f} | "
        f"Qty {total_qty} | Next ladders: {', '.join(next_ladders) if next_ladders else 'none'}"
    )


def run():
    global entry_price, high_water_mark, trailing_stop_id

    log("Strategy started. Waiting for TSLA buy to fill...")
    log(f"Ladder plan: " + " | ".join(f"-{p}% → {q}sh" for p, q in LADDERS))

    while True:
        try:
            # --- Step 1: Wait for entry fill ---
            if entry_price is None:
                order = get(f"/orders/{BUY_ORDER_ID}")
                status = order["status"]

                if status == "filled":
                    entry_price = float(order["filled_avg_price"])
                    high_water_mark = entry_price
                    log(f"Entry filled at ${entry_price:.2f}")
                    log(f"Initial floor: ${entry_price * (1 - TRAIL_PERCENT/100):.2f}")
                    for pct, qty in LADDERS:
                        log(f"  Ladder -{pct}%: trigger ${entry_price * (1 - pct/100):.2f} → buy {qty} shares")
                    trailing_stop_id = place_trailing_stop(int(order["filled_qty"]))

                elif status in ("canceled", "expired", "rejected"):
                    log(f"Buy order {status}. Exiting.")
                    break
                else:
                    log(f"Waiting for fill... ({status})")

            # --- Step 2: Monitor price ---
            else:
                price = latest_price()
                drop_pct = (entry_price - price) / entry_price * 100
                gain_pct = (price - entry_price) / entry_price * 100

                if price > high_water_mark:
                    high_water_mark = price

                # Get current position qty
                try:
                    pos = get(f"/positions/{SYMBOL}")
                    total_qty = int(float(pos["qty"]))
                except Exception:
                    log("Position closed — strategy complete.")
                    break

                print_status(price, drop_pct, gain_pct, total_qty)

                # Check each ladder level
                for level_pct, buy_qty in LADDERS:
                    if level_pct not in ladders_hit and drop_pct >= level_pct:
                        log(f"DROP {drop_pct:.1f}% hit ladder -{level_pct}%!")
                        place_ladder_buy(buy_qty, level_pct)
                        ladders_hit.add(level_pct)
                        # Update trailing stop to cover new total qty
                        new_qty = total_qty + buy_qty
                        trailing_stop_id = place_trailing_stop(new_qty)

        except Exception as e:
            log(f"Error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
