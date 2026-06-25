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


# ── Portfolio page generator ───────────────────────────────────────────────────
def generate_portfolio_html(account, positions, stops):
    equity    = float(account["equity"])
    cash      = float(account["cash"])
    initial   = 50000.0
    pl        = equity - initial
    pl_pct    = pl / initial * 100
    pl_color  = "#00E599" if pl >= 0 else "#FF3B5C"
    pl_sign   = "+" if pl >= 0 else ""
    now_str   = datetime.now().strftime("%d %b %Y · %H:%M CT")
    n_pos     = len(positions)

    # ── Position rows ──────────────────────────────────────────────────────────
    pos_rows = ""
    if positions:
        bars_map = get_bars_batch(list(positions.keys()), limit=ATR_PERIOD + 5)
        for sym, pos in positions.items():
            price  = float(pos["current_price"])
            entry  = float(pos["avg_entry_price"])
            qty    = int(float(pos["qty"]))
            pnl    = (price - entry) / entry * 100
            stop   = stops.get(sym, 0)
            atr    = calc_atr(bars_map.get(sym, [])) or 0
            mkt    = price * qty
            rc     = "#00E599" if pnl >= 0 else "#FF3B5C"
            sg     = "+" if pnl >= 0 else ""
            pos_rows += f"""
            <tr>
              <td style="font-weight:700;color:#E8ECF4;font-family:'DM Mono',monospace">{sym}</td>
              <td style="font-family:'DM Mono',monospace;color:#6B7A99">{qty}</td>
              <td style="font-family:'DM Mono',monospace">${entry:.2f}</td>
              <td style="font-family:'DM Mono',monospace">${price:.2f}</td>
              <td style="font-family:'DM Mono',monospace;color:{rc};font-weight:700">{sg}{pnl:.2f}%</td>
              <td style="font-family:'DM Mono',monospace;color:#FFB800">${stop:.2f}</td>
              <td style="font-family:'DM Mono',monospace;color:#4D8BFF">${atr:.2f}</td>
              <td style="font-family:'DM Mono',monospace;color:#6B7A99">${mkt:,.0f}</td>
            </tr>"""
        pos_section = f"""
        <div class="card">
          <div class="card-label">Posiciones abiertas ({n_pos}/{MAX_POS})</div>
          <div style="overflow-x:auto;">
          <table style="width:100%;border-collapse:collapse;font-size:12px;">
            <thead>
              <tr style="border-bottom:1px solid rgba(255,255,255,0.07);">
                <th style="text-align:left;padding:8px 10px;font-size:10px;letter-spacing:1.5px;color:#6B7A99;font-weight:600;text-transform:uppercase">Símbolo</th>
                <th style="text-align:left;padding:8px 10px;font-size:10px;letter-spacing:1.5px;color:#6B7A99;font-weight:600;text-transform:uppercase">Qty</th>
                <th style="text-align:left;padding:8px 10px;font-size:10px;letter-spacing:1.5px;color:#6B7A99;font-weight:600;text-transform:uppercase">Entrada</th>
                <th style="text-align:left;padding:8px 10px;font-size:10px;letter-spacing:1.5px;color:#6B7A99;font-weight:600;text-transform:uppercase">Precio</th>
                <th style="text-align:left;padding:8px 10px;font-size:10px;letter-spacing:1.5px;color:#6B7A99;font-weight:600;text-transform:uppercase">P&L</th>
                <th style="text-align:left;padding:8px 10px;font-size:10px;letter-spacing:1.5px;color:#6B7A99;font-weight:600;text-transform:uppercase">Stop</th>
                <th style="text-align:left;padding:8px 10px;font-size:10px;letter-spacing:1.5px;color:#6B7A99;font-weight:600;text-transform:uppercase">ATR</th>
                <th style="text-align:left;padding:8px 10px;font-size:10px;letter-spacing:1.5px;color:#6B7A99;font-weight:600;text-transform:uppercase">Valor</th>
              </tr>
            </thead>
            <tbody>{pos_rows}</tbody>
          </table>
          </div>
        </div>"""
    else:
        pos_section = """
        <div class="card">
          <div style="text-align:center;padding:32px 20px;">
            <div style="font-size:40px;margin-bottom:12px;">⏳</div>
            <div style="font-family:'DM Serif Display',serif;font-size:20px;color:#fff;margin-bottom:8px;">Sin posiciones abiertas</div>
            <div style="font-size:13px;color:#6B7A99;line-height:1.7;">
              El bot escanea 60 tickers en búsqueda de breakouts.<br>
              Se requiere: cierre &gt; máximo 20d + volumen ≥ 1.5× + precio &gt; MA50.
            </div>
          </div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Auto BOT · Chandelier Exit</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Mono:wght@400;500&family=Syne:wght@400;600;700;800&display=swap');
:root {{
  --bg:#0A0C10; --surface:#111520; --surface2:#181D2B;
  --border:rgba(255,255,255,0.07); --text:#E8ECF4; --muted:#6B7A99;
  --green:#00E599; --red:#FF3B5C; --yellow:#FFB800; --blue:#4D8BFF;
}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Syne',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}}
body::before{{content:'';position:fixed;inset:0;
  background-image:linear-gradient(rgba(77,139,255,0.03) 1px,transparent 1px),linear-gradient(90deg,rgba(77,139,255,0.03) 1px,transparent 1px);
  background-size:40px 40px;pointer-events:none;z-index:0}}
.nav{{position:fixed;top:0;left:0;right:0;z-index:1000;background:rgba(10,12,16,0.92);
  backdrop-filter:blur(12px);border-bottom:1px solid var(--border);
  display:flex;align-items:center;justify-content:space-between;padding:10px 24px}}
.nav-label{{font-family:'DM Mono',monospace;font-size:11px;letter-spacing:2px;color:var(--muted);text-transform:uppercase}}
.nav-links{{display:flex;gap:8px}}
.nav-links a{{text-decoration:none;padding:7px 16px;border-radius:7px;font-family:'Syne',sans-serif;
  font-size:12px;font-weight:700;letter-spacing:0.5px}}
.nav-links a.active{{background:var(--blue);color:#fff}}
.nav-links a.inactive{{background:rgba(255,255,255,0.07);color:var(--muted)}}
.wrap{{position:relative;z-index:10;padding:72px 24px 60px;max-width:860px;margin:0 auto}}
.eyebrow{{font-family:'DM Mono',monospace;font-size:11px;letter-spacing:3px;text-transform:uppercase;
  color:var(--blue);margin-bottom:8px;display:flex;align-items:center;gap:8px}}
.pulse{{width:6px;height:6px;background:var(--blue);border-radius:50%;animation:pulse 1.5s ease-in-out infinite}}
@keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:0.4;transform:scale(0.7)}}}}
h1{{font-family:'DM Serif Display',serif;font-size:clamp(28px,6vw,44px);line-height:1.1;color:#fff;margin-bottom:4px}}
h1 span{{color:var(--blue);font-style:italic}}
.subtitle{{font-family:'DM Mono',monospace;font-size:12px;color:var(--muted);margin-bottom:28px}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-bottom:16px}}
.metric{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px 18px}}
.metric-label{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:6px}}
.metric-val{{font-family:'DM Mono',monospace;font-size:22px;font-weight:500;color:#fff}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:20px 22px;margin-bottom:14px}}
.card-label{{font-family:'DM Mono',monospace;font-size:10px;letter-spacing:2.5px;text-transform:uppercase;
  color:var(--muted);margin-bottom:14px;display:flex;align-items:center;gap:8px}}
.card-label::before{{content:'';width:14px;height:2px;background:var(--blue);border-radius:2px}}
tbody tr{{border-bottom:1px solid rgba(255,255,255,0.05)}}
tbody tr:last-child{{border-bottom:none}}
tbody td{{padding:10px 10px;vertical-align:middle}}
.sched-row{{display:flex;align-items:center;gap:12px;padding:10px 14px;background:var(--surface2);border-radius:8px;margin-bottom:8px}}
.sched-time{{font-family:'DM Mono',monospace;font-size:12px;color:var(--blue);min-width:80px}}
.sched-desc{{font-size:12px;color:var(--muted)}}
footer{{position:relative;z-index:10;text-align:center;padding:20px;
  font-family:'DM Mono',monospace;font-size:11px;color:var(--muted);
  border-top:1px solid var(--border);margin-top:20px}}
</style>
</head>
<body>

<nav class="nav">
  <span class="nav-label">Market Intelligence</span>
  <div class="nav-links">
    <a href="/" class="inactive">📉 Caídas</a>
    <a href="/portfolio" class="active">🤖 Auto BOT</a>
    <a href="/historico" class="inactive">📅 Historial</a>
  </div>
</nav>

<div class="wrap">
  <div class="eyebrow"><div class="pulse"></div> Chandelier Exit ATR · Paper Trading</div>
  <h1>Auto <span>BOT</span></h1>
  <div class="subtitle">📅 {now_str} &nbsp;·&nbsp; Cuenta Paper PA3005WTJRYE</div>

  <div class="metrics">
    <div class="metric">
      <div class="metric-label">Capital Inicial</div>
      <div class="metric-val">$50,000</div>
    </div>
    <div class="metric">
      <div class="metric-label">Equity Actual</div>
      <div class="metric-val">${equity:,.0f}</div>
    </div>
    <div class="metric">
      <div class="metric-label">Ganancia / Pérdida</div>
      <div class="metric-val" style="color:{pl_color}">{pl_sign}${pl:,.0f}</div>
    </div>
    <div class="metric">
      <div class="metric-label">Retorno Total</div>
      <div class="metric-val" style="color:{pl_color}">{pl_sign}{pl_pct:.2f}%</div>
    </div>
    <div class="metric">
      <div class="metric-label">Efectivo</div>
      <div class="metric-val">${cash:,.0f}</div>
    </div>
    <div class="metric">
      <div class="metric-label">Posiciones</div>
      <div class="metric-val">{n_pos} / {MAX_POS}</div>
    </div>
  </div>

  {pos_section}

  <div class="card">
    <div class="card-label">Estrategia</div>
    <div style="font-family:'DM Serif Display',serif;font-size:20px;color:#fff;margin-bottom:10px;">
      Chandelier Exit ATR Trailing Stop
    </div>
    <div style="font-size:13px;color:var(--muted);line-height:1.8;margin-bottom:16px;">
      Breakout de <b style="color:var(--text)">20 días</b> con confirmación de volumen
      (<b style="color:var(--text)">≥ 1.5×</b> promedio) y filtro de tendencia
      (<b style="color:var(--text)">MA50</b>). Stop dinámico: máximo desde entrada
      menos <b style="color:var(--text)">2.5 × ATR(14)</b> — solo sube.
      Riesgo máximo <b style="color:var(--text)">2%</b> del portafolio por operación.
    </div>
    <div class="sched-row"><span class="sched-time">9:35am CT</span><span class="sched-desc">Escanea breakouts + revisa stops (apertura)</span></div>
    <div class="sched-row"><span class="sched-time">3:45pm CT</span><span class="sched-desc">Revisión antes del cierre + actualiza esta página</span></div>
  </div>
</div>

<footer>Chandelier Exit Bot · ATR(14) × 2.5 · Alpaca Paper Trading · Solo fines educativos</footer>
</body>
</html>"""


def save_portfolio_page(account, positions, stops):
    html = generate_portfolio_html(account, positions, stops)
    out_dir = os.path.join(os.path.dirname(_HERE), "public", "portfolio")
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, "index.html")
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"Portfolio page → {out_path}")


# ── Entry point (single run for GitHub Actions) ────────────────────────────────
if __name__ == "__main__":
    log("Chandelier Exit Bot — single run")
    log(f"ATR({ATR_PERIOD}) × {ATR_MULT} | Risk {RISK_PCT*100:.0f}%/trade | "
        f"Max {MAX_POS} positions | {BKOUT_DAYS}d breakout + MA{MA_DAYS} filter")
    run_scan()
    # Generate portfolio page after scan
    try:
        account   = get_account()
        positions = get_positions()
        stops     = load_stops()
        save_portfolio_page(account, positions, stops)
    except Exception as e:
        log(f"Portfolio page generation failed: {e}")
