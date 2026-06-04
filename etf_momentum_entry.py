"""
AutoBOT-ETF-Momentum-Entry
Revisa los 5 ETFs y compra el de mejor retorno del último mes.
Si el líder cambió, vende el anterior y compra el nuevo.
Estado persistido en etf_state.json (commiteado por el workflow).
"""
import yfinance as yf
import requests
import json
import os
from datetime import datetime, timedelta

ETFS = ["SPY", "QQQ", "IWM", "GLD", "TLT"]
STATE_FILE = "etf_state.json"

BASE = "https://paper-api.alpaca.markets/v2"
HEADS = {
    "APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"].encode("ascii", "ignore").decode("ascii").strip(),
    "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"].encode("ascii", "ignore").decode("ascii").strip(),
}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def get_1month_return(ticker):
    end = datetime.now()
    start = end - timedelta(days=35)
    data = yf.download(ticker, start=start, end=end, progress=False)
    if len(data) < 2:
        return None
    close = data["Close"].squeeze()  # handle multi-level columns in newer yfinance
    first = float(close.iloc[0])
    last = float(close.iloc[-1])
    return (last - first) / first * 100 if first > 0 else None


def alpaca_get(path):
    r = requests.get(BASE + path, headers=HEADS, timeout=10)
    r.raise_for_status()
    return r.json()


def alpaca_post(path, body):
    r = requests.post(BASE + path, headers=HEADS, json=body, timeout=10)
    r.raise_for_status()
    return r.json()


def get_positions():
    return {p["symbol"]: p for p in alpaca_get("/positions")}


def place_order(symbol, side, qty):
    order = alpaca_post("/orders", {
        "symbol": symbol, "qty": qty,
        "side": side, "type": "market", "time_in_force": "day",
    })
    log(f"  {side.upper()} {qty} {symbol} | ID: {order['id'][:8]}")
    return order


def load_state():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except FileNotFoundError:
        return {"winner": None, "date": None}


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def run():
    log("AutoBOT-ETF-Momentum-Entry iniciando...")

    returns = {}
    for etf in ETFS:
        ret = get_1month_return(etf)
        if ret is not None:
            returns[etf] = ret
            log(f"  {etf}: {ret:+.2f}%")

    if not returns:
        log("ERROR: No se pudo obtener datos de ETFs")
        return

    winner = max(returns, key=returns.get)
    log(f"Líder: {winner} ({returns[winner]:+.2f}%)")

    state = load_state()
    prev_winner = state.get("winner")

    if prev_winner == winner:
        log(f"Sin cambio — sigue {winner}. Nada que hacer.")
        return

    log(f"¡Cambio! {prev_winner} → {winner}")
    positions = get_positions()

    if prev_winner and prev_winner in positions:
        qty = int(float(positions[prev_winner]["qty"]))
        log(f"Vendiendo {qty} {prev_winner}...")
        place_order(prev_winner, "sell", qty)

    account = alpaca_get("/account")
    bp = float(account["buying_power"])
    ticker_data = yf.Ticker(winner).history(period="1d")
    price = float(ticker_data["Close"].iloc[-1])
    qty = max(1, int(bp * 0.95 / price))
    log(f"Comprando {qty} {winner} @ ~${price:.2f}...")
    place_order(winner, "buy", qty)

    save_state({"winner": winner, "date": datetime.now().isoformat()})
    log("Listo.")


if __name__ == "__main__":
    run()
