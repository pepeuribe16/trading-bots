"""
Chandelier Exit ATR Backtest
-----------------------------
Simula la estrategia día a día desde START_DATE hasta hoy.
Usa datos históricos reales de Alpaca Data API.
Genera public/backtest/index.html con el reporte completo.
"""
import os
import json
import requests
from datetime import datetime, date, timedelta
from collections import defaultdict

# ── Config ─────────────────────────────────────────────────────────────────────
API_KEY    = os.environ["ALPACA_API_KEY"]
API_SECRET = os.environ["ALPACA_API_SECRET"]
DATA       = "https://data.alpaca.markets/v2"
HEADS      = {"APCA-API-KEY-ID": API_KEY, "APCA-API-SECRET-KEY": API_SECRET}

START_DATE   = date(2025, 6, 26)
FETCH_FROM   = date(2024, 12, 1)  # ~120 barras de warmup para breakout 55d
END_DATE     = date.today()

INITIAL_CAP  = 50_000.0
ATR_PERIOD   = 14
ATR_MULT     = 2.5
BKOUT_DAYS   = 55
MA_DAYS      = 50
VOL_MULT     = 1.5
RISK_PCT     = 0.02
MAX_POS_PCT  = 0.20
MAX_POS      = 8
BARS_NEEDED  = max(MA_DAYS, BKOUT_DAYS) + ATR_PERIOD + 5

with open(os.path.join(os.path.dirname(__file__), "ChandelierBot", "config.json")) as f:
    cfg = json.load(f)
TICKERS = cfg["universe"]


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ── Data fetch ─────────────────────────────────────────────────────────────────
def fetch_all_bars():
    """Descarga todas las barras diarias para todos los tickers de una vez."""
    log(f"Descargando barras históricas {FETCH_FROM} → {END_DATE} para {len(TICKERS)} tickers...")
    all_bars = {}
    for i in range(0, len(TICKERS), 10):
        batch = TICKERS[i:i+10]
        params = {
            "symbols": ",".join(batch),
            "timeframe": "1Day",
            "start": FETCH_FROM.isoformat(),
            "end": END_DATE.isoformat(),
            "limit": 10000,
            "feed": "iex",
            "adjustment": "all",
        }
        r = requests.get(f"{DATA}/stocks/bars", headers=HEADS, params=params, timeout=30)
        if r.ok:
            for sym, bars in r.json().get("bars", {}).items():
                all_bars[sym] = bars
        log(f"  batch {i//10+1}/{(len(TICKERS)+9)//10}: {len(batch)} tickers OK")
    return all_bars


def build_daily_index(all_bars):
    """Devuelve {sym: {date_str: bar_index}} para lookup rápido."""
    index = {}
    for sym, bars in all_bars.items():
        index[sym] = {}
        for i, bar in enumerate(bars):
            d = bar["t"][:10]
            index[sym][d] = i
    return index


def get_trading_days(all_bars):
    """Todas las fechas de mercado abierto en el rango de simulación."""
    days = set()
    for bars in all_bars.values():
        for bar in bars:
            d = bar["t"][:10]
            if d >= START_DATE.isoformat() and d <= END_DATE.isoformat():
                days.add(d)
    return sorted(days)


# ── Indicators ─────────────────────────────────────────────────────────────────
def calc_atr(bars):
    trs = []
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i-1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if len(trs) < ATR_PERIOD:
        return None
    return sum(trs[-ATR_PERIOD:]) / ATR_PERIOD


def chandelier_stop_val(bars, atr):
    return max(b["c"] for b in bars) - ATR_MULT * atr


def entry_signal(bars):
    if len(bars) < BARS_NEEDED:
        return None
    today   = bars[-1]
    prior20 = bars[-(BKOUT_DAYS+1):-1]
    prior50 = bars[-MA_DAYS:]
    close, volume = today["c"], today["v"]
    high20  = max(b["c"] for b in prior20)
    avgvol  = sum(b["v"] for b in prior20) / len(prior20)
    ma50    = sum(b["c"] for b in prior50) / len(prior50)
    if not (close > high20 and volume >= avgvol * VOL_MULT and close > ma50):
        return None
    atr = calc_atr(bars)
    if not atr:
        return None
    return close, close - ATR_MULT * atr, atr, volume / avgvol


def calc_qty(portfolio, entry, stop):
    dist = entry - stop
    if dist <= 0:
        return 0
    by_risk = int(portfolio * RISK_PCT / dist)
    by_cap  = int(portfolio * MAX_POS_PCT / entry)
    return max(1, min(by_risk, by_cap))


# ── Backtest engine ────────────────────────────────────────────────────────────
def run_backtest(all_bars, daily_index, trading_days):
    cash       = INITIAL_CAP
    positions  = {}   # sym -> {qty, entry, stop}
    equity_curve = []  # [{date, equity, cash, n_pos}]
    trades     = []    # closed trades log

    log(f"Simulando {len(trading_days)} días de mercado ({trading_days[0]} → {trading_days[-1]})...")

    for day_str in trading_days:
        # ── Valorar posiciones abiertas ────────────────────────────────────────
        mkt_value = 0.0
        to_close  = []

        for sym, pos in positions.items():
            idx = daily_index.get(sym, {}).get(day_str)
            if idx is None:
                mkt_value += pos["qty"] * pos["entry"]
                continue
            bars_up_to = all_bars[sym][:idx+1]
            price = bars_up_to[-1]["c"]
            atr   = calc_atr(bars_up_to)

            if atr:
                new_stop = chandelier_stop_val(bars_up_to, atr)
                pos["stop"] = max(pos["stop"], new_stop)

            mkt_value += pos["qty"] * price

            if price <= pos["stop"]:
                pnl = (price - pos["entry"]) * pos["qty"]
                trades.append({
                    "sym": sym, "entry_date": pos["entry_date"], "exit_date": day_str,
                    "entry": pos["entry"], "exit": price, "qty": pos["qty"],
                    "pnl": pnl, "pnl_pct": (price - pos["entry"]) / pos["entry"] * 100,
                    "result": "WIN" if pnl > 0 else "LOSS",
                })
                cash += pos["qty"] * price
                mkt_value -= pos["qty"] * price
                to_close.append(sym)

        for sym in to_close:
            del positions[sym]

        equity = cash + mkt_value
        equity_curve.append({"date": day_str, "equity": round(equity, 2),
                              "cash": round(cash, 2), "n_pos": len(positions)})

        # ── Escanear entradas ──────────────────────────────────────────────────
        slots = MAX_POS - len(positions)
        if slots <= 0 or cash < 500:
            continue

        candidates = []
        for sym in TICKERS:
            if sym in positions:
                continue
            idx = daily_index.get(sym, {}).get(day_str)
            if idx is None or idx < BARS_NEEDED:
                continue
            bars_up_to = all_bars[sym][:idx+1]
            result = entry_signal(bars_up_to)
            if result:
                price, stop, atr, vol_ratio = result
                candidates.append((sym, price, stop, atr, vol_ratio))

        candidates.sort(key=lambda x: x[4], reverse=True)
        bought = 0
        for sym, price, stop, atr, vol_ratio in candidates:
            if bought >= slots or cash < price:
                break
            qty  = calc_qty(equity, price, stop)
            cost = qty * price
            if cost > cash:
                qty  = max(1, int(cash / price))
                cost = qty * price
            if qty < 1:
                continue
            positions[sym] = {
                "qty": qty, "entry": price, "stop": stop,
                "entry_date": day_str
            }
            cash -= cost
            bought += 1

    # Cerrar posiciones abiertas al precio del último día
    last_day = trading_days[-1]
    for sym, pos in positions.items():
        idx = daily_index.get(sym, {}).get(last_day)
        if idx is not None:
            price = all_bars[sym][idx]["c"]
        else:
            price = pos["entry"]
        pnl = (price - pos["entry"]) * pos["qty"]
        trades.append({
            "sym": sym, "entry_date": pos["entry_date"], "exit_date": last_day,
            "entry": pos["entry"], "exit": price, "qty": pos["qty"],
            "pnl": pnl, "pnl_pct": (price - pos["entry"]) / pos["entry"] * 100,
            "result": "OPEN",
        })

    return equity_curve, trades


# ── Stats ──────────────────────────────────────────────────────────────────────
def calc_stats(equity_curve, trades):
    equities = [e["equity"] for e in equity_curve]
    final    = equities[-1]
    total_ret = (final - INITIAL_CAP) / INITIAL_CAP * 100

    # Max drawdown
    peak = INITIAL_CAP
    max_dd = 0.0
    for eq in equities:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Daily returns for Sharpe
    daily_rets = []
    for i in range(1, len(equities)):
        daily_rets.append((equities[i] - equities[i-1]) / equities[i-1])
    avg_ret = sum(daily_rets) / len(daily_rets) if daily_rets else 0
    std_ret = (sum((r - avg_ret)**2 for r in daily_rets) / len(daily_rets))**0.5 if daily_rets else 0
    sharpe  = (avg_ret / std_ret * (252**0.5)) if std_ret > 0 else 0

    closed = [t for t in trades if t["result"] != "OPEN"]
    wins   = [t for t in closed if t["result"] == "WIN"]
    win_rate = len(wins) / len(closed) * 100 if closed else 0
    avg_win  = sum(t["pnl_pct"] for t in wins) / len(wins) if wins else 0
    losses_t = [t for t in closed if t["result"] == "LOSS"]
    avg_loss = sum(t["pnl_pct"] for t in losses_t) / len(losses_t) if losses_t else 0

    return {
        "final_equity": final,
        "total_return": total_ret,
        "max_drawdown": max_dd,
        "sharpe": sharpe,
        "total_trades": len(closed),
        "win_rate": win_rate,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "best_trade": max((t["pnl_pct"] for t in closed), default=0),
        "worst_trade": min((t["pnl_pct"] for t in closed), default=0),
    }


# ── HTML generator ─────────────────────────────────────────────────────────────
def generate_html(equity_curve, trades, stats):
    now_str  = datetime.now().strftime("%d %b %Y · %H:%M CT")
    ret_color = "#00E599" if stats["total_return"] >= 0 else "#FF3B5C"
    ret_sign  = "+" if stats["total_return"] >= 0 else ""

    # SVG equity curve
    equities = [e["equity"] for e in equity_curve]
    dates    = [e["date"] for e in equity_curve]
    n = len(equities)
    W, H = 620, 220
    PAD_L, PAD_R, PAD_T, PAD_B = 64, 20, 20, 36

    mn, mx = min(equities), max(equities)
    rng    = mx - mn if mx != mn else 1

    def sx(i):
        return PAD_L + (i / (n - 1)) * (W - PAD_L - PAD_R)

    def sy(v):
        return PAD_T + (1 - (v - mn) / rng) * (H - PAD_T - PAD_B)

    # Polyline points
    pts  = " ".join(f"{sx(i):.1f},{sy(v):.1f}" for i, v in enumerate(equities))
    # Fill polygon (close at bottom)
    fill = pts + f" {sx(n-1):.1f},{H-PAD_B} {sx(0):.1f},{H-PAD_B}"

    # Baseline (initial capital)
    base_y = sy(INITIAL_CAP)

    # Y-axis labels
    y_labels = []
    for frac in [0, 0.25, 0.5, 0.75, 1.0]:
        val = mn + frac * rng
        y   = PAD_T + (1 - frac) * (H - PAD_T - PAD_B)
        y_labels.append((val, y))

    # X-axis labels (monthly)
    x_labels = []
    months_seen = set()
    for i, d in enumerate(dates):
        ym = d[:7]
        if ym not in months_seen:
            months_seen.add(ym)
            label = datetime.strptime(d, "%Y-%m-%d").strftime("%b %y")
            x_labels.append((sx(i), label))

    y_axis_svg = "".join(
        f'<text x="{PAD_L-6}" y="{y+4}" fill="#6B7A99" font-size="9" text-anchor="end">${val/1000:.0f}k</text>'
        f'<line x1="{PAD_L}" y1="{y}" x2="{W-PAD_R}" y2="{y}" stroke="#1E2535" stroke-width="1"/>'
        for val, y in y_labels
    )
    x_axis_svg = "".join(
        f'<text x="{x}" y="{H-PAD_B+14}" fill="#6B7A99" font-size="9" text-anchor="middle">{label}</text>'
        for x, label in x_labels
    )

    line_color  = "#00E599" if stats["total_return"] >= 0 else "#FF3B5C"
    fill_color1 = "rgba(0,229,153,0.18)" if stats["total_return"] >= 0 else "rgba(255,59,92,0.18)"
    fill_color2 = "rgba(0,229,153,0)" if stats["total_return"] >= 0 else "rgba(255,59,92,0)"

    svg = f"""<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:auto;">
  <defs>
    <linearGradient id="eq_grad" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{fill_color1}"/>
      <stop offset="100%" stop-color="{fill_color2}"/>
    </linearGradient>
  </defs>
  <rect width="{W}" height="{H}" fill="#111520" rx="8"/>
  {y_axis_svg}
  {x_axis_svg}
  <line x1="{PAD_L}" y1="{base_y:.1f}" x2="{W-PAD_R}" y2="{base_y:.1f}" stroke="#334155" stroke-width="1" stroke-dasharray="4,3"/>
  <text x="{PAD_L+4}" y="{base_y-4}" fill="#4A5568" font-size="8">$50k base</text>
  <polygon points="{fill}" fill="url(#eq_grad)"/>
  <polyline points="{pts}" fill="none" stroke="{line_color}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
  <circle cx="{sx(n-1):.1f}" cy="{sy(equities[-1]):.1f}" r="4" fill="{line_color}" stroke="#111520" stroke-width="2"/>
</svg>"""

    # Trade rows (last 30 closed)
    closed_trades = [t for t in trades if t["result"] != "OPEN"][-30:]
    open_trades   = [t for t in trades if t["result"] == "OPEN"]
    trade_rows = ""
    for t in reversed(closed_trades):
        clr = "#00E599" if t["result"] == "WIN" else "#FF3B5C"
        sgn = "+" if t["pnl"] >= 0 else ""
        trade_rows += f"""<tr>
          <td style="font-weight:700;color:#E8ECF4;font-family:'DM Mono',monospace">{t['sym']}</td>
          <td style="font-family:'DM Mono',monospace;color:#6B7A99;font-size:11px">{t['entry_date']}</td>
          <td style="font-family:'DM Mono',monospace;color:#6B7A99;font-size:11px">{t['exit_date']}</td>
          <td style="font-family:'DM Mono',monospace">${t['entry']:.2f}</td>
          <td style="font-family:'DM Mono',monospace">${t['exit']:.2f}</td>
          <td style="font-family:'DM Mono',monospace;color:{clr};font-weight:700">{sgn}{t['pnl_pct']:.1f}%</td>
          <td style="font-family:'DM Mono',monospace;color:{clr}">{sgn}${t['pnl']:,.0f}</td>
          <td><span style="background:{'rgba(0,229,153,0.12)' if t['result']=='WIN' else 'rgba(255,59,92,0.12)'};color:{clr};padding:2px 8px;border-radius:4px;font-size:10px;font-family:'DM Mono',monospace">{t['result']}</span></td>
        </tr>"""

    # Open positions
    open_rows = ""
    for t in open_trades:
        open_rows += f"""<tr>
          <td style="font-weight:700;color:#E8ECF4;font-family:'DM Mono',monospace">{t['sym']}</td>
          <td style="font-family:'DM Mono',monospace;color:#6B7A99;font-size:11px">{t['entry_date']}</td>
          <td style="font-family:'DM Mono',monospace;color:#6B7A99;font-size:11px">{t['exit_date']}</td>
          <td style="font-family:'DM Mono',monospace">${t['entry']:.2f}</td>
          <td style="font-family:'DM Mono',monospace">${t['exit']:.2f}</td>
          <td style="font-family:'DM Mono',monospace;color:#FFB800;font-weight:700">{('+' if t['pnl_pct']>=0 else '')}{t['pnl_pct']:.1f}%</td>
          <td style="font-family:'DM Mono',monospace;color:#FFB800">{('+' if t['pnl']>=0 else '')}${t['pnl']:,.0f}</td>
          <td><span style="background:rgba(255,184,0,0.12);color:#FFB800;padding:2px 8px;border-radius:4px;font-size:10px;font-family:'DM Mono',monospace">OPEN</span></td>
        </tr>"""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Backtest · Chandelier Exit 55d · 1 año (Jun 2025–Jun 2026)</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Mono:wght@400;500&family=Syne:wght@400;600;700;800&display=swap');
:root{{--bg:#0A0C10;--surface:#111520;--surface2:#181D2B;--border:rgba(255,255,255,0.07);--text:#E8ECF4;--muted:#6B7A99;--green:#00E599;--red:#FF3B5C;--yellow:#FFB800;--blue:#4D8BFF}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Syne',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}}
body::before{{content:'';position:fixed;inset:0;background-image:linear-gradient(rgba(77,139,255,0.03) 1px,transparent 1px),linear-gradient(90deg,rgba(77,139,255,0.03) 1px,transparent 1px);background-size:40px 40px;pointer-events:none;z-index:0}}
.nav{{position:fixed;top:0;left:0;right:0;z-index:1000;background:rgba(10,12,16,0.92);backdrop-filter:blur(12px);border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;padding:10px 24px}}
.nav-label{{font-family:'DM Mono',monospace;font-size:11px;letter-spacing:2px;color:var(--muted);text-transform:uppercase}}
.nav-links{{display:flex;gap:8px}}
.nav-links a{{text-decoration:none;padding:7px 16px;border-radius:7px;font-family:'Syne',sans-serif;font-size:12px;font-weight:700;letter-spacing:0.5px}}
.nav-links a.active{{background:var(--green);color:#000}}
.nav-links a.inactive{{background:rgba(255,255,255,0.07);color:var(--muted)}}
.wrap{{position:relative;z-index:10;padding:72px 24px 60px;max-width:960px;margin:0 auto}}
.eyebrow{{font-family:'DM Mono',monospace;font-size:11px;letter-spacing:3px;text-transform:uppercase;color:var(--green);margin-bottom:8px}}
h1{{font-family:'DM Serif Display',serif;font-size:clamp(28px,5vw,42px);line-height:1.1;color:#fff;margin-bottom:4px}}
h1 span{{color:var(--green);font-style:italic}}
.subtitle{{font-family:'DM Mono',monospace;font-size:12px;color:var(--muted);margin-bottom:28px}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px;margin-bottom:16px}}
.metric{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px 18px}}
.metric-label{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:6px}}
.metric-val{{font-family:'DM Mono',monospace;font-size:20px;font-weight:500;color:#fff}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:20px 22px;margin-bottom:14px}}
.card-label{{font-family:'DM Mono',monospace;font-size:10px;letter-spacing:2.5px;text-transform:uppercase;color:var(--muted);margin-bottom:14px;display:flex;align-items:center;gap:8px}}
.card-label::before{{content:'';width:14px;height:2px;background:var(--green);border-radius:2px}}
table{{width:100%;border-collapse:collapse;font-size:12px}}
th{{text-align:left;padding:8px 10px;font-size:10px;letter-spacing:1.5px;color:var(--muted);font-weight:600;text-transform:uppercase;border-bottom:1px solid var(--border)}}
tbody tr{{border-bottom:1px solid rgba(255,255,255,0.04)}}
tbody tr:last-child{{border-bottom:none}}
tbody td{{padding:9px 10px;vertical-align:middle}}
.disclaimer{{background:rgba(255,184,0,0.06);border:1px solid rgba(255,184,0,0.2);border-radius:10px;padding:14px 18px;margin-bottom:14px;font-size:12px;color:#a08040;line-height:1.6}}
footer{{position:relative;z-index:10;text-align:center;padding:20px;font-family:'DM Mono',monospace;font-size:11px;color:var(--muted);border-top:1px solid var(--border);margin-top:20px}}
</style>
</head>
<body>
<nav class="nav">
  <span class="nav-label">Market Intelligence</span>
  <div class="nav-links">
    <a href="/" class="inactive">📉 Caídas</a>
    <a href="/portfolio" class="inactive">🤖 Auto BOT</a>
    <a href="/historico" class="inactive">📅 Historial</a>
    <a href="/backtest" class="active">📊 Backtest</a>
  </div>
</nav>

<div class="wrap">
  <div class="eyebrow">📊 Simulación histórica</div>
  <h1>Backtest <span>Chandelier Exit</span></h1>
  <div class="subtitle">01 Ene 2025 → {END_DATE.strftime('%d %b %Y')} &nbsp;·&nbsp; Capital inicial $50,000 &nbsp;·&nbsp; NYSE / NASDAQ &nbsp;·&nbsp; {now_str}</div>
  <div style="display:inline-flex;align-items:center;gap:10px;background:rgba(0,229,153,0.08);border:1px solid rgba(0,229,153,0.25);border-radius:8px;padding:8px 16px;margin-bottom:20px;font-family:'DM Mono',monospace;font-size:12px;color:#00E599;">
    📅 Período: <b>26 Jun 2025 → 26 Jun 2026</b> &nbsp;·&nbsp; Capital inicial: $50,000 &nbsp;·&nbsp; Breakout: 55 días
  </div>

  <div class="disclaimer">
    ⚠️ <b>Solo fines educativos.</b> El backtest usa precios de cierre reales pero no incluye comisiones, slippage, ni impacto de mercado.
    Los resultados pasados no garantizan resultados futuros.
  </div>

  <div class="metrics">
    <div class="metric">
      <div class="metric-label">Capital Inicial</div>
      <div class="metric-val">$50,000</div>
    </div>
    <div class="metric">
      <div class="metric-label">Capital Final</div>
      <div class="metric-val" style="color:{ret_color}">${stats['final_equity']:,.0f}</div>
    </div>
    <div class="metric">
      <div class="metric-label">Retorno Total</div>
      <div class="metric-val" style="color:{ret_color}">{ret_sign}{stats['total_return']:.1f}%</div>
    </div>
    <div class="metric">
      <div class="metric-label">Max Drawdown</div>
      <div class="metric-val" style="color:#FF3B5C">-{stats['max_drawdown']:.1f}%</div>
    </div>
    <div class="metric">
      <div class="metric-label">Sharpe Ratio</div>
      <div class="metric-val" style="color:{'#00E599' if stats['sharpe']>1 else '#FFB800' if stats['sharpe']>0 else '#FF3B5C'}">{stats['sharpe']:.2f}</div>
    </div>
    <div class="metric">
      <div class="metric-label">Operaciones</div>
      <div class="metric-val">{stats['total_trades']}</div>
    </div>
    <div class="metric">
      <div class="metric-label">Win Rate</div>
      <div class="metric-val" style="color:{'#00E599' if stats['win_rate']>=50 else '#FF3B5C'}">{stats['win_rate']:.0f}%</div>
    </div>
    <div class="metric">
      <div class="metric-label">Avg Ganancia</div>
      <div class="metric-val" style="color:#00E599">+{stats['avg_win']:.1f}%</div>
    </div>
    <div class="metric">
      <div class="metric-label">Avg Pérdida</div>
      <div class="metric-val" style="color:#FF3B5C">{stats['avg_loss']:.1f}%</div>
    </div>
    <div class="metric">
      <div class="metric-label">Mejor Trade</div>
      <div class="metric-val" style="color:#00E599">+{stats['best_trade']:.1f}%</div>
    </div>
    <div class="metric">
      <div class="metric-label">Peor Trade</div>
      <div class="metric-val" style="color:#FF3B5C">{stats['worst_trade']:.1f}%</div>
    </div>
  </div>

  <div class="card">
    <div class="card-label">Curva de Equity vs $50,000 base</div>
    {svg}
  </div>

  {'<div class="card"><div class="card-label">Posiciones abiertas al cierre del backtest</div><div style="overflow-x:auto"><table><thead><tr><th>Símbolo</th><th>Entrada</th><th>Cierre BT</th><th>Precio Entrada</th><th>Último Precio</th><th>P&L %</th><th>P&L $</th><th>Estado</th></tr></thead><tbody>' + open_rows + '</tbody></table></div></div>' if open_rows else ''}

  <div class="card">
    <div class="card-label">Log de operaciones cerradas (últimas 30)</div>
    <div style="overflow-x:auto">
    <table>
      <thead>
        <tr>
          <th>Símbolo</th><th>Entrada</th><th>Salida</th>
          <th>Precio Entrada</th><th>Precio Salida</th>
          <th>P&L %</th><th>P&L $</th><th>Resultado</th>
        </tr>
      </thead>
      <tbody>{trade_rows}</tbody>
    </table>
    </div>
  </div>

  <div class="card">
    <div class="card-label">Parámetros del backtest</div>
    <div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px;font-size:13px;">
      <div style="color:var(--muted)">Período: <b style="color:var(--text)">01 Ene 2025 → {END_DATE.strftime('%d %b %Y')}</b></div>
      <div style="color:var(--muted)">Universo: <b style="color:var(--text)">60 tickers NASDAQ/NYSE</b></div>
      <div style="color:var(--muted)">ATR: <b style="color:var(--text)">período 14, multiplicador 2.5×</b></div>
      <div style="color:var(--muted)">Breakout: <b style="color:#00E599">máximo 55 días ⚡</b></div>
      <div style="color:var(--muted)">Filtro tendencia: <b style="color:var(--text)">MA 50 días</b></div>
      <div style="color:var(--muted)">Volumen: <b style="color:var(--text)">≥ 1.5× promedio 20d</b></div>
      <div style="color:var(--muted)">Riesgo/operación: <b style="color:var(--text)">2% del portafolio</b></div>
      <div style="color:var(--muted)">Máx posiciones: <b style="color:var(--text)">8 simultáneas</b></div>
      <div style="color:var(--muted)">Máx por posición: <b style="color:var(--text)">20% del portafolio</b></div>
      <div style="color:var(--muted)">Comisiones: <b style="color:#FF3B5C">No incluidas</b></div>
    </div>
  </div>
</div>

<footer>Chandelier Exit Backtest · Datos históricos Alpaca IEX · Solo fines educativos · {now_str}</footer>
</body>
</html>"""


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    log(f"Chandelier Exit Backtest — {START_DATE} → {END_DATE}")

    all_bars    = fetch_all_bars()
    daily_index = build_daily_index(all_bars)
    trading_days = get_trading_days(all_bars)

    log(f"Días de mercado en rango de simulación: {len(trading_days)}")

    equity_curve, trades = run_backtest(all_bars, daily_index, trading_days)
    stats = calc_stats(equity_curve, trades)

    log(f"Resultado: ${stats['final_equity']:,.0f} ({'+' if stats['total_return']>=0 else ''}{stats['total_return']:.1f}%) "
        f"| Trades: {stats['total_trades']} | Win rate: {stats['win_rate']:.0f}% | Sharpe: {stats['sharpe']:.2f}")

    html = generate_html(equity_curve, trades, stats)
    out_dir = os.path.join(os.path.dirname(__file__), "public", "backtest")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "index.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"Reporte generado → {out}")


if __name__ == "__main__":
    main()
