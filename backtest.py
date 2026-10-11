"""
Chandelier Exit ATR — backtest comparativo
-------------------------------------------
Simula varias configuraciones de la estrategia sobre los mismos datos diarios
de Alpaca y genera public/backtest/index.html con la comparación.

Supuestos (iguales para todas las variantes):
  - Las señales se calculan al cierre del día y se ejecutan a la APERTURA del día
    siguiente (compras y ventas).
  - Stop Chandelier = cierre más alto DESDE LA COMPRA − k × ATR(14); solo sube.
    El stop inicial es cierre de la señal − k × ATR.
  - Tamaño: riesgo 2% del capital / distancia al stop, con tope por posición.
    El efectivo nunca queda negativo (sin margen). Sin comisiones.
  - Universo: el de ChandelierBot/config.json. Filtro de mercado con QQQ.
"""
import json
import os
from datetime import date, datetime, timedelta
from html import escape

import requests

DATA = "https://data.alpaca.markets/v2"
START_DATE = date(2025, 6, 26)
FETCH_FROM = START_DATE - timedelta(days=330)   # calentamiento para MA200 y breakout 55d
END_DATE = date.today()
INITIAL_CAP = 50_000.0
ATR_PERIOD = 14
MA_DAYS = 50
VOL_MULT = 1.5
RISK_PCT = 0.02
MAX_POS = 8
MARKET = "QQQ"

with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "ChandelierBot", "config.json")) as f:
    TICKERS = json.load(f)["universe"]

# Ronda 2: combinaciones sobre el breakout de 20 días (el mejor de la ronda 1), contra las
# dos configuraciones que operaban hasta ahora.
VARIANTS = [
    {"key": "repo", "name": "Bot del repo (anterior)", "desc": "Breakout 55d · stop 2.5 ATR · 20% por posición",
     "bkout": 55, "atr_mult": 2.5, "max_pos_pct": 0.20, "cooldown": 0, "regime": None},
    {"key": "rutina", "name": "Rutina de Claude (anterior)", "desc": "Breakout 20d · stop 2.5 ATR · 20% por posición",
     "bkout": 20, "atr_mult": 2.5, "max_pos_pct": 0.20, "cooldown": 0, "regime": None},
    {"key": "a", "name": "20d · 12.5% · stop 2.5", "desc": "Rutina sin margen (12.5% por posición)",
     "bkout": 20, "atr_mult": 2.5, "max_pos_pct": 0.125, "cooldown": 0, "regime": None},
    {"key": "b", "name": "20d · 12.5% · stop 2.5 · QQQ", "desc": "+ solo compra si QQQ > MA200",
     "bkout": 20, "atr_mult": 2.5, "max_pos_pct": 0.125, "cooldown": 0, "regime": 200},
    {"key": "c", "name": "20d · 12.5% · stop 3.0", "desc": "Sin margen, stop más amplio",
     "bkout": 20, "atr_mult": 3.0, "max_pos_pct": 0.125, "cooldown": 0, "regime": None},
    {"key": "d", "name": "20d · 12.5% · stop 3.0 · QQQ", "desc": "Stop 3.0 + filtro QQQ > MA200",
     "bkout": 20, "atr_mult": 3.0, "max_pos_pct": 0.125, "cooldown": 0, "regime": 200},
]
COLORS = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300"]  # validados sobre #111520


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ── Datos ──────────────────────────────────────────────────────────────────────
def fetch_all_bars(symbols, heads):
    log(f"Descargando barras {FETCH_FROM} → {END_DATE} para {len(symbols)} símbolos...")
    out = {}
    for i in range(0, len(symbols), 10):
        params = {"symbols": ",".join(symbols[i:i + 10]), "timeframe": "1Day",
                  "start": FETCH_FROM.isoformat(), "end": END_DATE.isoformat(),
                  "limit": 10000, "feed": "iex", "adjustment": "all"}
        while True:
            r = requests.get(f"{DATA}/stocks/bars", headers=heads, params=params, timeout=30)
            r.raise_for_status()
            body = r.json()
            for sym, bars in (body.get("bars") or {}).items():
                out.setdefault(sym, []).extend(bars)
            if not body.get("next_page_token"):
                break
            params["page_token"] = body["next_page_token"]
    log(f"  {len(out)} símbolos con datos")
    return out


# ── Indicadores ────────────────────────────────────────────────────────────────
def atr_at(bars, i):
    if i < ATR_PERIOD:
        return None
    trs = [max(bars[j]["h"] - bars[j]["l"], abs(bars[j]["h"] - bars[j - 1]["c"]),
               abs(bars[j]["l"] - bars[j - 1]["c"])) for j in range(i - ATR_PERIOD + 1, i + 1)]
    return sum(trs) / ATR_PERIOD


def signal_at(bars, i, bkout):
    """Breakout de `bkout` días + volumen ≥ 1.5× + cierre > MA50, evaluado al cierre de i."""
    if i < max(bkout, MA_DAYS) + ATR_PERIOD:
        return None
    c, v = bars[i]["c"], bars[i]["v"]
    prior = bars[i - bkout:i]
    if c <= max(b["c"] for b in prior):
        return None
    avg_vol = sum(b["v"] for b in prior) / bkout
    if v < avg_vol * VOL_MULT:
        return None
    if c <= sum(b["c"] for b in bars[i - MA_DAYS + 1:i + 1]) / MA_DAYS:
        return None
    atr = atr_at(bars, i)
    return (c, atr, v / avg_vol) if atr else None


# ── Motor ──────────────────────────────────────────────────────────────────────
def simulate(p, bars, idx, days, market):
    cash, positions, trades, curve = INITIAL_CAP, {}, [], []
    last_exit = {}
    pend_sell, pend_buy = set(), []
    for day in days:
        d = date.fromisoformat(day)
        # 1) Ejecutar a la apertura las órdenes decididas ayer.
        for sym in sorted(pend_sell):
            pos, i = positions.get(sym), idx[sym].get(day)
            if pos is None or i is None:
                continue
            px = bars[sym][i]["o"]
            cash += pos["qty"] * px
            pnl = (px - pos["entry"]) * pos["qty"]
            trades.append({"sym": sym, "entry_date": pos["date"], "exit_date": day, "qty": pos["qty"],
                           "entry": pos["entry"], "exit": px, "pnl": pnl,
                           "pnl_pct": (px - pos["entry"]) / pos["entry"] * 100, "open": False})
            del positions[sym]
            last_exit[sym] = d
        pend_sell = {s for s in pend_sell if s in positions}
        for sym, qty, stop in pend_buy:
            i = idx[sym].get(day)
            if i is None or sym in positions or len(positions) >= MAX_POS:
                continue
            px = bars[sym][i]["o"]
            qty = min(qty, int(cash / px))
            if qty < 1:
                continue
            cash -= qty * px
            positions[sym] = {"qty": qty, "entry": px, "stop": stop, "hi": px, "date": day}
        pend_buy = []

        # 2) Al cierre: actualizar stops y decidir ventas.
        value = 0.0
        for sym, pos in positions.items():
            i = idx[sym].get(day)
            if i is None:
                value += pos["qty"] * pos.get("last", pos["entry"])
                continue
            c = bars[sym][i]["c"]
            pos["last"] = c
            pos["hi"] = max(pos["hi"], c)
            atr = atr_at(bars[sym], i)
            if atr:
                pos["stop"] = max(pos["stop"], pos["hi"] - p["atr_mult"] * atr)
            value += pos["qty"] * c
            if c <= pos["stop"]:
                pend_sell.add(sym)
        equity = cash + value
        curve.append((day, equity, len(positions)))

        # 3) Al cierre: buscar entradas para ejecutar mañana.
        if p["regime"]:
            mi = idx[MARKET].get(day)
            mb = bars[MARKET]
            if mi is None or mi < p["regime"]:
                continue
            ma = sum(b["c"] for b in mb[mi - p["regime"] + 1:mi + 1]) / p["regime"]
            if mb[mi]["c"] <= ma:
                continue
        slots = MAX_POS - (len(positions) - len(pend_sell))
        if slots <= 0:
            continue
        cands = []
        for sym in TICKERS:
            if sym in positions or sym not in idx:
                continue
            if p["cooldown"] and sym in last_exit and (d - last_exit[sym]).days < p["cooldown"]:
                continue
            i = idx[sym].get(day)
            if i is None:
                continue
            sig = signal_at(bars[sym], i, p["bkout"])
            if sig:
                cands.append((sig[2], sym, sig[0], sig[1]))
        for _, sym, c, atr in sorted(cands, reverse=True)[:slots]:
            stop = c - p["atr_mult"] * atr
            by_risk = int(equity * RISK_PCT / (c - stop))
            by_cap = int(equity * p["max_pos_pct"] / c)
            qty = min(by_risk, by_cap)
            if qty >= 1:
                pend_buy.append((sym, qty, stop))

    last = days[-1]
    for sym, pos in positions.items():
        px = pos.get("last", pos["entry"])
        trades.append({"sym": sym, "entry_date": pos["date"], "exit_date": last, "qty": pos["qty"],
                       "entry": pos["entry"], "exit": px, "pnl": (px - pos["entry"]) * pos["qty"],
                       "pnl_pct": (px - pos["entry"]) / pos["entry"] * 100, "open": True})
    return curve, trades


def stats(curve, trades):
    eq = [e for _, e, _ in curve]
    peak, mdd = INITIAL_CAP, 0.0
    for e in eq:
        peak = max(peak, e)
        mdd = max(mdd, (peak - e) / peak * 100)
    rets = [eq[i] / eq[i - 1] - 1 for i in range(1, len(eq))]
    mean = sum(rets) / len(rets) if rets else 0
    sd = (sum((r - mean) ** 2 for r in rets) / len(rets)) ** 0.5 if rets else 0
    closed = [t for t in trades if not t["open"]]
    wins = [t for t in closed if t["pnl"] > 0]
    losses = [t for t in closed if t["pnl"] <= 0]
    gw, gl = sum(t["pnl"] for t in wins), -sum(t["pnl"] for t in losses)
    half = len(curve) // 2
    h1 = curve[half][1] / INITIAL_CAP - 1 if curve else 0
    h2 = curve[-1][1] / curve[half][1] - 1 if curve else 0
    return {
        "ret": (eq[-1] / INITIAL_CAP - 1) * 100, "final": eq[-1], "mdd": mdd,
        "sharpe": mean / sd * 252 ** 0.5 if sd else 0,
        "n": len(closed), "open": len(trades) - len(closed),
        "win_rate": len(wins) / len(closed) * 100 if closed else 0,
        "avg_win": gw / len(wins) if wins else 0, "avg_loss": -gl / len(losses) if losses else 0,
        "pf": gw / gl if gl else 0,
        "expectancy": (gw - gl) / len(closed) if closed else 0,
        "exposure": sum(n for _, _, n in curve) / len(curve) / MAX_POS * 100 if curve else 0,
        "h1": h1 * 100, "h2": h2 * 100, "half_date": curve[half][0] if curve else "",
    }


# ── Página ─────────────────────────────────────────────────────────────────────
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def fecha(iso, year=True):
    d = date.fromisoformat(iso)
    return f"{d.day} {MESES[d.month - 1]}" + (f" {d.year}" if year else "")


def money(v, sign=False):
    s = f"${abs(v):,.0f}"
    return ("−" + s) if v < 0 else (("+" + s) if sign else s)


def pct(v, sign=True):
    return ("−" if v < 0 else ("+" if sign else "")) + f"{abs(v):.1f}%"


def chart(results, days, benchmark):
    W, H, L, R, T, B = 760, 300, 58, 150, 14, 30
    allv = [e for r in results for _, e, _ in r["curve"]] + [v for _, v in benchmark] + [INITIAL_CAP]
    lo, hi = min(allv), max(allv)
    pad = (hi - lo) * 0.06
    lo, hi = lo - pad, hi + pad
    n = len(days)

    def x(i): return L + (W - L - R) * i / (n - 1)
    def y(v): return T + (H - T - B) * (hi - v) / (hi - lo)

    span = hi - lo
    step = next(s for s in (1000, 2000, 2500, 5000, 10000, 20000, 25000, 50000) if span / s <= 6)
    g = ""
    t = (int(lo // step) + 1) * step
    while t < hi:
        g += (f'<line x1="{L}" x2="{W - R}" y1="{y(t):.1f}" y2="{y(t):.1f}" class="grid"/>'
              f'<text x="{L - 8}" y="{y(t) + 4:.1f}" class="axis" text-anchor="end">${t / 1000:,.0f}k</text>')
        t += step
    g += f'<line x1="{L}" x2="{W - R}" y1="{y(INITIAL_CAP):.1f}" y2="{y(INITIAL_CAP):.1f}" class="base"/>'
    for j in range(5):
        i = round(j * (n - 1) / 4)
        a = "start" if j == 0 else "end" if j == 4 else "middle"
        g += f'<text x="{x(i):.1f}" y="{H - 8}" class="axis" text-anchor="{a}">{fecha(days[i], False)} {days[i][2:4]}</text>'

    pos = {d: i for i, d in enumerate(days)}
    bm = [(pos[d], v) for d, v in benchmark if d in pos]
    if bm:
        g += ('<path d="M' + " L".join(f"{x(i):.1f},{y(v):.1f}" for i, v in bm) +
              '" fill="none" stroke="#6B7A99" stroke-width="1.5" stroke-dasharray="3 3"/>')
    ends = []
    for r, col in zip(results, COLORS):
        pts = [(x(i), y(e)) for i, (_, e, _) in enumerate(r["curve"])]
        g += (f'<path d="M' + " L".join(f"{a:.1f},{b:.1f}" for a, b in pts) +
              f'" fill="none" stroke="{col}" stroke-width="2" stroke-linejoin="round" data-k="{r["key"]}"/>')
        ends.append([pts[-1][1], r, col])
    # Etiquetas directas al final de cada línea, separadas para que no se encimen.
    ends.sort(key=lambda e: e[0])
    for k in range(1, len(ends)):
        ends[k][0] = max(ends[k][0], ends[k - 1][0] + 14)
    for ly, r, col in ends:
        g += (f'<circle cx="{W - R:.1f}" cy="{y(r["curve"][-1][1]):.1f}" r="3.5" fill="{col}"/>'
              f'<text x="{W - R + 8}" y="{ly + 4:.1f}" class="lbl" fill="{col}">{escape(r["short"])} '
              f'<tspan class="lblv">{pct(r["s"]["ret"])}</tspan></text>')
    data = json.dumps({"days": days, "series": [{"name": r["name"], "color": c, "eq": [round(e) for _, e, _ in r["curve"]]}
                                                for r, c in zip(results, COLORS)],
                       "bench": [round(v) for _, v in benchmark] if len(benchmark) == len(days) else None})
    return f"""<div class="chart" id="bt" data-l="{L}" data-r="{W - R}" data-w="{W}" data-t="{T}" data-b="{H - B}">
<svg viewBox="0 0 {W} {H}" role="img" aria-label="Evolución del capital de cada configuración">{g}
<line id="btx" y1="{T}" y2="{H - B}" class="cross" style="display:none"/></svg>
<div id="bttip" class="tip" style="display:none"></div>
<script type="application/json" id="btdata">{data}</script></div>"""


def generate_html(results, days, benchmark, bench_ret, n_bars):
    base = results[0]["s"]
    rows = ""
    best = {k: max(results, key=lambda r: r["s"][k])["key"] for k in ("ret", "sharpe", "win_rate", "pf")}
    best["mdd"] = min(results, key=lambda r: r["s"]["mdd"])["key"]
    for r, col in zip(results, COLORS):
        s = r["s"]

        def cell(k, txt):
            return f'<td class="{"best" if best.get(k) == r["key"] else ""}">{txt}</td>'
        rows += f"""<tr><td><i class="sw" style="background:{col}"></i><b>{escape(r["name"])}</b>
          <div class="desc">{escape(r["desc"])}</div></td>
          {cell("ret", pct(s["ret"]))}{cell("mdd", "−" + f'{s["mdd"]:.1f}%')}{cell("sharpe", f'{s["sharpe"]:.2f}')}
          <td>{s["n"]}</td>{cell("win_rate", f'{s["win_rate"]:.0f}%')}
          <td style="white-space:nowrap">{money(s["avg_win"], True)} / {money(s["avg_loss"])}</td>{cell("pf", f'{s["pf"]:.2f}')}
          <td>{money(s["expectancy"], True)}</td><td>{pct(s["h1"])} · {pct(s["h2"])}</td>
          <td>{s["exposure"]:.0f}%</td></tr>"""

    half = fecha(base["half_date"])
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Backtest · Chandelier Exit</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Mono:wght@400;500&family=Syne:wght@400;600;700;800&display=swap');
:root{{--bg:#0A0C10;--surface:#111520;--surface2:#181D2B;--border:rgba(255,255,255,0.07);--text:#E8ECF4;--muted:#8A97B3;--blue:#4D8BFF}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Syne',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}}
.nav{{position:fixed;top:0;left:0;right:0;z-index:1000;background:rgba(10,12,16,0.92);backdrop-filter:blur(12px);border-bottom:1px solid var(--border);
  display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 24px;overflow-x:auto}}
.nav-label{{font-family:'DM Mono',monospace;font-size:11px;letter-spacing:2px;color:#6B7A99;text-transform:uppercase;white-space:nowrap}}
.nav-links{{display:flex;gap:8px}}
.nav-links a{{text-decoration:none;padding:7px 16px;border-radius:7px;font-size:12px;font-weight:700;white-space:nowrap;background:rgba(255,255,255,0.07);color:#6B7A99}}
.nav-links a.active{{background:var(--blue);color:#fff}}
.wrap{{padding:76px 20px 48px;max-width:1100px;margin:0 auto}}
.eyebrow{{font-family:'DM Mono',monospace;font-size:11px;letter-spacing:3px;text-transform:uppercase;color:var(--blue);margin-bottom:8px}}
h1{{font-family:'DM Serif Display',serif;font-size:clamp(28px,6vw,42px);color:#fff}}
h1 span{{color:var(--blue);font-style:italic}}
.subtitle{{font-family:'DM Mono',monospace;font-size:12px;color:var(--muted);margin:6px 0 22px}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:20px 22px;margin-bottom:14px}}
.card-label{{font-family:'DM Mono',monospace;font-size:10px;letter-spacing:2.5px;text-transform:uppercase;color:var(--muted);margin-bottom:14px}}
.chart{{position:relative;overflow-x:auto}} .chart svg{{width:100%;min-width:620px;height:auto;display:block}}
.grid{{stroke:rgba(255,255,255,0.06)}} .base{{stroke:#6B7A99;stroke-dasharray:4 4}}
.axis{{fill:var(--muted);font-family:'DM Mono',monospace;font-size:11px}}
.lbl{{font-family:'Syne',sans-serif;font-size:11px;font-weight:700}} .lblv{{fill:var(--text);font-family:'DM Mono',monospace;font-weight:400}}
.cross{{stroke:rgba(255,255,255,0.35)}}
.tip{{position:absolute;top:0;pointer-events:none;background:var(--surface2);border:1px solid var(--border);border-radius:8px;padding:8px 10px;
  font-family:'DM Mono',monospace;font-size:12px;white-space:nowrap}}
.tip i{{display:inline-block;width:8px;height:8px;border-radius:2px;margin-right:6px}}
.legend{{display:flex;flex-wrap:wrap;gap:14px;margin-top:10px;font-size:12px;color:var(--muted)}}
.legend i{{display:inline-block;width:14px;height:3px;border-radius:2px;margin-right:6px;vertical-align:middle}}
.tbl{{overflow-x:auto}}
table{{width:100%;border-collapse:collapse;font-size:13px;min-width:900px}}
th{{text-align:left;font-size:10px;letter-spacing:1px;text-transform:uppercase;color:var(--muted);font-weight:600;padding:8px;border-bottom:1px solid var(--border)}}
td{{padding:10px 8px;border-bottom:1px solid rgba(255,255,255,0.05);font-family:'DM Mono',monospace;vertical-align:top}}
td:first-child{{font-family:'Syne',sans-serif}} td b{{font-weight:700}}
td.best{{color:#fff;font-weight:500;background:rgba(77,139,255,0.12)}}
.desc{{font-size:11px;color:var(--muted);margin-top:2px}}
.sw{{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px;vertical-align:middle}}
.notes{{font-size:13px;color:var(--muted);line-height:1.8}} .notes b{{color:var(--text)}}
.notes li{{margin-left:18px}}
@media (max-width:720px){{.wrap{{padding:72px 16px 40px}}}}
</style></head><body>
<nav class="nav"><span class="nav-label">Market Intelligence</span><div class="nav-links">
  <a href="/">📉 Caídas</a><a href="/portfolio">🤖 Auto BOT</a><a href="/historico">📅 Historial</a><a href="/japan-fares">✈️ Japan Fares</a>
</div></nav>
<div class="wrap">
  <div class="eyebrow">Chandelier Exit ATR · Backtest comparativo</div>
  <h1>Backtest <span>de ajustes</span></h1>
  <div class="subtitle">{fecha(days[0])} → {fecha(days[-1])} · {len(days)} días de mercado · {n_bars} acciones del universo ·
    capital inicial {money(INITIAL_CAP)} · generado {datetime.utcnow().strftime("%d %b %Y %H:%M")} UTC</div>

  <div class="card"><div class="card-label">Capital de cada configuración</div>
    {chart(results, days, benchmark)}
    <div class="legend">{"".join(f'<span><i style="background:{c}"></i>{escape(r["name"])}</span>' for r, c in zip(results, COLORS))}
      <span><i style="background:repeating-linear-gradient(90deg,#6B7A99 0 3px,transparent 3px 6px)"></i>QQQ (comprar y mantener) {pct(bench_ret) if bench_ret is not None else ""}</span></div>
  </div>

  <div class="card"><div class="card-label">Comparación · resaltado = mejor de la columna</div>
    <div class="tbl"><table><thead><tr>
      <th>Configuración</th><th>Retorno</th><th>Caída máx.</th><th>Sharpe</th><th>Operaciones</th><th>Acierto</th>
      <th>Gan. / pérd. prom.</th><th>Factor de beneficio</th><th>Esperado por op.</th><th>1ª mitad · 2ª mitad</th><th>Exposición</th>
    </tr></thead><tbody>{rows}</tbody></table></div>
    <div class="notes" style="margin-top:12px">"1ª mitad · 2ª mitad" = retorno antes y después del {half}: un ajuste que solo
      mejora una mitad probablemente se ajustó al ruido. "Factor de beneficio" = ganancias ÷ pérdidas de operaciones cerradas (&gt; 1 gana dinero).
      "Exposición" = % promedio de los {MAX_POS} lugares ocupados.</div>
  </div>

  <div class="card"><div class="card-label">Cómo se simuló</div>
    <ul class="notes">
      <li>Mismos datos diarios de Alpaca (feed IEX, ajustados por splits y dividendos) para todas las configuraciones.</li>
      <li>Señal al <b>cierre</b>; compra o venta a la <b>apertura del día siguiente</b>. Sin comisiones ni deslizamiento.</li>
      <li>Stop = cierre más alto <b>desde la compra</b> − k × ATR(14), solo sube. Riesgo 2% por operación, máximo {MAX_POS} posiciones, sin margen.</li>
      <li>Las dos primeras filas son las configuraciones que operaban; las demás combinan breakout de 20 días, 12.5% por posición (sin margen), stop 2.5 o 3.0 ATR y filtro QQQ &gt; MA200.</li>
      <li>Un backtest de un periodo corto no garantiza resultados futuros. Solo fines educativos.</li>
    </ul></div>
</div>
<script>
(function(){{
  var box=document.getElementById('bt'); if(!box) return;
  var D=JSON.parse(document.getElementById('btdata').textContent), svg=box.querySelector('svg'),
      tip=document.getElementById('bttip'), vx=document.getElementById('btx');
  var L=+box.dataset.l, R=+box.dataset.r, W=+box.dataset.w, n=D.days.length;
  function move(e){{
    var r=svg.getBoundingClientRect(), cx=(e.touches?e.touches[0].clientX:e.clientX)-r.left;
    var i=Math.max(0,Math.min(n-1,Math.round((cx*W/r.width-L)/(R-L)*(n-1)))), px=L+(R-L)*i/(n-1);
    vx.setAttribute('x1',px); vx.setAttribute('x2',px); vx.style.display='';
    var rows=D.series.map(function(s){{return {{n:s.name,c:s.color,v:s.eq[i]}};}}).sort(function(a,b){{return b.v-a.v;}});
    tip.innerHTML='<div style="color:#8A97B3;margin-bottom:4px">'+D.days[i]+'</div>'+rows.map(function(o){{
      return '<div><i style="background:'+o.c+'"></i>'+o.n+' · $'+o.v.toLocaleString('en-US')+'</div>';}}).join('')+
      (D.bench?'<div style="color:#8A97B3">QQQ · $'+D.bench[i].toLocaleString('en-US')+'</div>':'');
    tip.style.display=''; var left=px*r.width/W+12; if(left>r.width-260) left=px*r.width/W-272; tip.style.left=Math.max(0,left)+'px';
  }}
  function hide(){{tip.style.display='none';vx.style.display='none';}}
  svg.addEventListener('mousemove',move); svg.addEventListener('touchmove',move,{{passive:true}});
  svg.addEventListener('mouseleave',hide); svg.addEventListener('touchend',hide);
}})();
</script>
</body></html>"""


SHORT = {"repo": "Repo 55d", "rutina": "Rutina 20d", "a": "20d·2.5", "b": "20d·2.5·QQQ", "c": "20d·3.0", "d": "20d·3.0·QQQ"}


def run(bars):
    for sym in bars:
        bars[sym].sort(key=lambda b: b["t"])
    idx = {s: {b["t"][:10]: i for i, b in enumerate(bs)} for s, bs in bars.items()}
    days = sorted({b["t"][:10] for s, bs in bars.items() if s != MARKET for b in bs
                   if START_DATE.isoformat() <= b["t"][:10] <= END_DATE.isoformat()})
    results = []
    for p in VARIANTS:
        curve, trades = simulate(p, bars, idx, days, MARKET)
        s = stats(curve, trades)
        log(f"{p['name']:<26} ret {s['ret']:+6.1f}%  dd {s['mdd']:5.1f}%  ops {s['n']:3d}  "
            f"acierto {s['win_rate']:4.0f}%  PF {s['pf']:.2f}  sharpe {s['sharpe']:.2f}")
        results.append({**p, "short": SHORT[p["key"]], "curve": curve, "trades": trades, "s": s})
    bench, bench_ret = [], None
    if MARKET in bars:
        mb = {b["t"][:10]: b["c"] for b in bars[MARKET]}
        first = next((mb[d] for d in days if d in mb), None)
        if first:
            last_v = INITIAL_CAP
            for d in days:
                last_v = INITIAL_CAP * mb[d] / first if d in mb else last_v
                bench.append((d, last_v))
            bench_ret = (bench[-1][1] / INITIAL_CAP - 1) * 100
    n_bars = len([s for s in bars if s != MARKET])
    return results, days, bench, bench_ret, n_bars


def main():
    heads = {"APCA-API-KEY-ID": os.environ["ALPACA_API_KEY"],
             "APCA-API-SECRET-KEY": os.environ["ALPACA_API_SECRET"]}
    bars = fetch_all_bars(TICKERS + [MARKET], heads)
    results, days, bench, bench_ret, n_bars = run(bars)
    html = generate_html(results, days, bench, bench_ret, n_bars)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "public", "backtest", "index.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    summary = {r["key"]: {k: round(v, 3) if isinstance(v, float) else v for k, v in r["s"].items()} for r in results}
    summary["_qqq_ret"] = bench_ret
    print("SUMMARY_JSON " + json.dumps(summary))
    log(f"Reporte → {out}")


if __name__ == "__main__":
    main()
