"""
TSLA Strategy Monitor — versión one-shot para GitHub Actions.
Lee estado de tsla_state.json, hace un check y guarda el estado actualizado.
El workflow lo llama cada hora entre 2pm-8pm ET lunes a viernes.
"""
import requests
import json
import os
from datetime import datetime

SYMBOL = "TSLA"
BASE = "https://paper-api.alpaca.markets/v2"
HEADS = {
    "APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"],
    "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"],
}
TRAIL_PERCENT = 5.0
LADDERS = [(10.0, 5), (20.0, 15), (35.0, 25)]
STATE_FILE = "tsla_state.json"


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def alpaca_get(path):
    r = requests.get(BASE + path, headers=HEADS, timeout=10)
    r.raise_for_status()
    return r.json()


def alpaca_post(path, body):
    r = requests.post(BASE + path, headers=HEADS, json=body, timeout=10)
    r.raise_for_status()
    return r.json()


def latest_price():
    r = requests.get(
        f"https://data.alpaca.markets/v2/stocks/{SYMBOL}/trades/latest",
        headers=HEADS, timeout=10,
    )
    r.raise_for_status()
    return r.json()["trade"]["p"]


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"entry_price": None, "high_water_mark": None, "ladders_hit": []}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def run():
    log("TSLA Strategy Monitor — check iniciando...")

    try:
        pos = alpaca_get(f"/positions/{SYMBOL}")
        total_qty = int(float(pos["qty"]))
    except Exception:
        log("Sin posición en TSLA — nada que monitorear")
        return

    state = load_state()
    entry_price = state.get("entry_price") or float(pos["avg_entry_price"])
    high_water_mark = state.get("high_water_mark") or entry_price
    ladders_hit = set(state.get("ladders_hit", []))

    price = latest_price()
    drop_pct = (entry_price - price) / entry_price * 100
    gain_pct = (price - entry_price) / entry_price * 100

    if price > high_water_mark:
        high_water_mark = price

    floor = high_water_mark * (1 - TRAIL_PERCENT / 100)
    sign = "▲" if gain_pct >= 0 else "▼"
    log(
        f"TSLA ${price:.2f} | Entry ${entry_price:.2f} | {sign}{abs(gain_pct):.1f}% | "
        f"HWM ${high_water_mark:.2f} | Floor ${floor:.2f} | Qty {total_qty}"
    )

    for level_pct, buy_qty in LADDERS:
        if level_pct not in ladders_hit and drop_pct >= level_pct:
            log(f"LADDER -{level_pct}%: drop {drop_pct:.1f}% alcanzado — comprando {buy_qty} acciones")
            order = alpaca_post("/orders", {
                "symbol": SYMBOL, "qty": buy_qty,
                "side": "buy", "type": "market", "time_in_force": "day",
            })
            log(f"  Orden: {order['id'][:8]}")
            ladders_hit.add(level_pct)

    save_state({
        "entry_price": entry_price,
        "high_water_mark": high_water_mark,
        "ladders_hit": list(ladders_hit),
    })
    log("Check completo.")


if __name__ == "__main__":
    run()
