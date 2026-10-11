"""
portfolio_page
Genera /portfolio/index.html del Chandelier Exit Bot a partir de datos ya
calculados (sin llamadas a Alpaca), para poder probarla con datos de ejemplo.

Entrada (dict):
  account   : {"equity", "cash", "created_at"}            — de /v2/account
  positions : [{"symbol","qty","entry","price","stop","atr"}]
  history   : [("YYYY-MM-DD", equity), ...]               — de /v2/account/portfolio/history
  trades    : [{"symbol","qty","entry","exit","pnl","pnl_pct","opened","closed"}]
              operaciones cerradas (compra → venta), calculadas FIFO desde los fills
  first_trade: fecha ISO de la primera compra (opcional)
  initial   : capital inicial
  max_positions, updated_at
"""
import json
from datetime import date, datetime
from html import escape

GREEN, RED, BLUE, YELLOW, MUTED = "#00E599", "#FF3B5C", "#4D8BFF", "#FFB800", "#6B7A99"
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _money(v, sign=False):
    s = f"${abs(v):,.0f}"
    if v < 0:
        return "−" + s
    return ("+" + s) if sign else s


def _pct(v):
    return ("+" if v >= 0 else "−") + f"{abs(v):.2f}%"


def _fecha(iso):
    d = date.fromisoformat(iso[:10])
    return f"{d.day} {MESES[d.month - 1]} {d.year}"


def _fecha_corta(iso):
    d = date.fromisoformat(iso[:10])
    return f"{d.day} {MESES[d.month - 1]}"


def _nice_ticks(lo, hi, n=4):
    span = hi - lo or 1
    raw = span / n
    mag = 10 ** len(str(int(raw))) / 10
    step = min((m * mag for m in (1, 2, 2.5, 5, 10) if m * mag >= raw), default=raw)
    start = int(lo // step) * step
    ticks, v = [], start
    while v <= hi + step * 0.01:
        if v >= lo - step * 0.01:
            ticks.append(v)
        v += step
    return ticks


def _equity_chart(history, trades, initial):
    """Línea de equity (SVG) con marcas de cada operación cerrada y tooltip."""
    if len(history) < 2:
        return ('<div class="empty">La línea de tiempo aparecerá cuando haya al menos '
                'dos días de historial.</div>')
    W, H, L, R, T, B = 680, 260, 56, 14, 16, 32
    vals = [v for _, v in history] + [initial]
    lo, hi = min(vals), max(vals)
    pad = (hi - lo) * 0.08 or initial * 0.01
    lo, hi = lo - pad, hi + pad
    n = len(history)

    def x(i):
        return L + (W - L - R) * i / (n - 1)

    def y(v):
        return T + (H - T - B) * (hi - v) / (hi - lo)

    pts = [(x(i), y(v)) for i, (_, v) in enumerate(history)]
    line = "M" + " L".join(f"{px:.1f},{py:.1f}" for px, py in pts)
    area = line + f" L{pts[-1][0]:.1f},{H - B} L{pts[0][0]:.1f},{H - B} Z"

    grid = ""
    for t in _nice_ticks(lo, hi):
        grid += (f'<line x1="{L}" x2="{W - R}" y1="{y(t):.1f}" y2="{y(t):.1f}" class="grid"/>'
                 f'<text x="{L - 8}" y="{y(t) + 4:.1f}" class="axis" text-anchor="end">'
                 f'${t / 1000:,.0f}k</text>')
    base_y = y(initial)
    # Sin etiqueta dentro de la gráfica (se encimaría con la línea); la leyenda la nombra.
    grid += f'<line x1="{L}" x2="{W - R}" y1="{base_y:.1f}" y2="{base_y:.1f}" class="base"/>'


    xlabels = ""
    k = min(5, n)
    for j in range(k):
        i = round(j * (n - 1) / (k - 1)) if k > 1 else 0
        anchor = "start" if j == 0 else "end" if j == k - 1 else "middle"
        xlabels += (f'<text x="{x(i):.1f}" y="{H - 10}" class="axis" text-anchor="{anchor}">'
                    f'{_fecha_corta(history[i][0])}</text>')

    # Marca cada operación cerrada sobre la línea, en su fecha de cierre.
    idx = {d: i for i, (d, _) in enumerate(history)}
    dates = [d for d, _ in history]
    marks = ""
    for t in trades:
        d = t["closed"][:10]
        i = idx.get(d)
        if i is None:
            i = next((j for j, dd in enumerate(dates) if dd >= d), n - 1)
        cx, cy = pts[i]
        win = t["pnl"] >= 0
        # Triángulo arriba = ganadora, abajo = perdedora: no depende solo del color.
        tri = (f"{cx:.1f},{cy - 7:.1f} {cx - 6:.1f},{cy + 4:.1f} {cx + 6:.1f},{cy + 4:.1f}" if win
               else f"{cx:.1f},{cy + 7:.1f} {cx - 6:.1f},{cy - 4:.1f} {cx + 6:.1f},{cy - 4:.1f}")
        marks += (f'<polygon points="{tri}" fill="{GREEN if win else RED}" class="mark">'
                  f'<title>{escape(t["symbol"])} · {_money(t["pnl"], True)} '
                  f'({_pct(t["pnl_pct"])}) · {_fecha(t["closed"])}</title></polygon>')

    last = pts[-1]
    data = json.dumps([[d, round(v, 2)] for d, v in history])
    return f"""
<div class="chart" id="eqchart" data-l="{L}" data-r="{W - R}" data-w="{W}">
  <svg viewBox="0 0 {W} {H}" role="img" aria-label="Evolución del saldo de la cuenta desde el inicio">
    <defs><linearGradient id="eqfill" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="{BLUE}" stop-opacity="0.28"/>
      <stop offset="1" stop-color="{BLUE}" stop-opacity="0"/></linearGradient></defs>
    {grid}{xlabels}
    <path d="{area}" fill="url(#eqfill)"/>
    <path d="{line}" fill="none" stroke="{BLUE}" stroke-width="2" stroke-linejoin="round"/>
    {marks}
    <circle cx="{last[0]:.1f}" cy="{last[1]:.1f}" r="5" fill="{BLUE}" stroke="var(--surface)" stroke-width="2"/>
    <line id="eqx" y1="{T}" y2="{H - B}" class="cross" style="display:none"/>
    <circle id="eqdot" r="5" fill="{BLUE}" stroke="var(--surface)" stroke-width="2" style="display:none"/>
  </svg>
  <div id="eqtip" class="tip" style="display:none"></div>
  <script type="application/json" id="eqdata">{data}</script>
</div>
<div class="legend"><span><i class="ln"></i>Saldo de la cuenta</span>
  <span><b style="color:{GREEN}">▲</b> venta con ganancia</span>
  <span><b style="color:{RED}">▼</b> venta con pérdida</span>
  <span><i class="dash"></i>capital inicial ({_money(initial)})</span></div>"""


def _stop_bar(p):
    """Qué tan lejos está el precio del stop: 0% = en el stop."""
    dist = (p["price"] - p["stop"]) / p["price"] * 100 if p["price"] and p["stop"] else 0
    width = max(3, min(100, dist / 15 * 100))  # 15% o más de colchón = barra llena
    color = RED if dist < 3 else YELLOW if dist < 7 else GREEN
    estado = "muy cerca del stop" if dist < 3 else "cerca del stop" if dist < 7 else "con margen"
    return dist, width, color, estado


def _position_cards(positions):
    if not positions:
        return ('<div class="empty">Sin posiciones abiertas. El bot busca breakouts '
                'en cada ejecución.</div>')
    cards = ""
    for p in sorted(positions, key=lambda p: (p["price"] - p["stop"]) / p["price"] if p["price"] else 0):
        pnl_pct = (p["price"] - p["entry"]) / p["entry"] * 100 if p["entry"] else 0
        pnl = (p["price"] - p["entry"]) * p["qty"]
        dist, width, color, estado = _stop_bar(p)
        c = GREEN if pnl >= 0 else RED
        atr = f'${p["atr"]:.2f}' if p.get("atr") else "—"
        cards += f"""
    <div class="pos">
      <div class="pos-head"><span class="sym">{escape(p["symbol"])}</span>
        <span class="pnl" style="color:{c}">{_pct(pnl_pct)}</span></div>
      <div class="pos-sub">{p["qty"]} acciones · {_money(p["price"] * p["qty"])}
        <span style="color:{c}">({_money(pnl, True)})</span></div>
      <div class="pos-grid">
        <div><span>Entrada</span>${p["entry"]:.2f}</div>
        <div><span>Precio</span>${p["price"]:.2f}</div>
        <div><span>Stop</span>${p["stop"]:.2f}</div>
        <div><span>ATR</span>{atr}</div>
      </div>
      <div class="bar"><div style="width:{width:.0f}%;background:{color}"></div></div>
      <div class="bar-label">{dist:.1f}% sobre el stop · {estado}</div>
    </div>"""
    return f'<div class="pos-wrap">{cards}</div>'


def _trade_timeline(trades, limit=15):
    if not trades:
        return '<div class="empty">Todavía no hay operaciones cerradas.</div>'
    rows = ""
    for t in sorted(trades, key=lambda t: t["closed"], reverse=True)[:limit]:
        win = t["pnl"] >= 0
        c = GREEN if win else RED
        dias = (date.fromisoformat(t["closed"][:10]) - date.fromisoformat(t["opened"][:10])).days
        rows += f"""
    <div class="ev">
      <div class="ev-dot" style="background:{c}">{"▲" if win else "▼"}</div>
      <div class="ev-body">
        <div class="ev-top"><b>{escape(t["symbol"])}</b>
          <span style="color:{c}">{_money(t["pnl"], True)} · {_pct(t["pnl_pct"])}</span></div>
        <div class="ev-sub">{_fecha_corta(t["opened"])} → {_fecha(t["closed"])} · {dias} días ·
          {t["qty"]} acciones · ${t["entry"]:.2f} → ${t["exit"]:.2f}</div>
      </div>
    </div>"""
    more = (f'<div class="ev-more">Mostrando las {limit} más recientes de {len(trades)}.</div>'
            if len(trades) > limit else "")
    return f'<div class="timeline">{rows}</div>{more}'


def render_portfolio_page(d):
    acc, initial = d["account"], d["initial"]
    equity, cash = acc["equity"], acc["cash"]
    trades, positions, history = d["trades"], d["positions"], d["history"]
    pl, pl_pct = equity - initial, (equity - initial) / initial * 100
    plc = GREEN if pl >= 0 else RED

    # "Operando desde" = primera compra del bot; si no hay, la apertura de la cuenta.
    start = d.get("first_trade") or acc.get("created_at") or (history[0][0] if history else None)
    if start:
        # La línea arranca el día hábil anterior a la primera operación.
        prev = [h for h in history if h[0] < start[:10]]
        history = prev[-1:] + [h for h in history if h[0] >= start[:10]]
    dias = (date.today() - date.fromisoformat(start[:10])).days if start else 0
    desde = f"Operando desde el {_fecha(start)} · {dias} días" if start else ""

    wins = [t for t in trades if t["pnl"] >= 0]
    losses = [t for t in trades if t["pnl"] < 0]
    realized = sum(t["pnl"] for t in trades)
    unrealized = sum((p["price"] - p["entry"]) * p["qty"] for p in positions)
    invested = sum(p["price"] * p["qty"] for p in positions)
    win_rate = len(wins) / len(trades) * 100 if trades else 0
    best = max(trades, key=lambda t: t["pnl"]) if trades else None
    worst = min(trades, key=lambda t: t["pnl"]) if trades else None
    avg_win = sum(t["pnl"] for t in wins) / len(wins) if wins else 0
    avg_loss = sum(t["pnl"] for t in losses) / len(losses) if losses else 0
    peak = max((v for _, v in history), default=equity)
    drawdown = (equity - peak) / peak * 100 if peak else 0

    cash_note = ('<div class="note">⚠️ Efectivo negativo: la cuenta está usando margen.</div>'
                 if cash < 0 else "")
    best_worst = ""
    if best:
        best_worst = f"""
      <div class="kv"><span>Mejor operación</span><b style="color:{GREEN}">{escape(best["symbol"])} {_money(best["pnl"], True)}</b></div>
      <div class="kv"><span>Peor operación</span><b style="color:{RED if worst["pnl"] < 0 else GREEN}">{escape(worst["symbol"])} {_money(worst["pnl"], True)}</b></div>
      <div class="kv"><span>Ganancia promedio</span><b>{_money(avg_win, True)}</b></div>
      <div class="kv"><span>Pérdida promedio</span><b>{_money(avg_loss, True)}</b></div>"""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Auto BOT · Chandelier Exit</title>
<style>
@import url('https://fonts.googleapis.com/css2?family=DM+Serif+Display:ital@0;1&family=DM+Mono:wght@400;500&family=Syne:wght@400;600;700;800&display=swap');
:root{{--bg:#0A0C10;--surface:#111520;--surface2:#181D2B;--border:rgba(255,255,255,0.07);
  --text:#E8ECF4;--muted:{MUTED};--green:{GREEN};--red:{RED};--blue:{BLUE};--yellow:{YELLOW}}}
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:'Syne',sans-serif;background:var(--bg);color:var(--text);min-height:100vh}}
body::before{{content:'';position:fixed;inset:0;pointer-events:none;z-index:0;background-size:40px 40px;
  background-image:linear-gradient(rgba(77,139,255,0.03) 1px,transparent 1px),linear-gradient(90deg,rgba(77,139,255,0.03) 1px,transparent 1px)}}
.nav{{position:fixed;top:0;left:0;right:0;z-index:1000;background:rgba(10,12,16,0.92);backdrop-filter:blur(12px);
  border-bottom:1px solid var(--border);display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 24px;overflow-x:auto}}
.nav-label{{font-family:'DM Mono',monospace;font-size:11px;letter-spacing:2px;color:var(--muted);text-transform:uppercase;white-space:nowrap}}
.nav-links{{display:flex;gap:8px}}
.nav-links a{{text-decoration:none;padding:7px 16px;border-radius:7px;font-size:12px;font-weight:700;white-space:nowrap}}
.nav-links a.active{{background:var(--blue);color:#fff}}
.nav-links a.inactive{{background:rgba(255,255,255,0.07);color:var(--muted)}}
.wrap{{position:relative;z-index:10;padding:76px 20px 48px;max-width:960px;margin:0 auto}}
.eyebrow{{font-family:'DM Mono',monospace;font-size:11px;letter-spacing:3px;text-transform:uppercase;color:var(--blue);
  display:flex;align-items:center;gap:8px;margin-bottom:8px}}
.pulse{{width:6px;height:6px;background:var(--blue);border-radius:50%;animation:pulse 1.5s ease-in-out infinite}}
@keyframes pulse{{0%,100%{{opacity:1}}50%{{opacity:.4}}}}
h1{{font-family:'DM Serif Display',serif;font-size:clamp(28px,6vw,42px);color:#fff;line-height:1.1}}
h1 span{{color:var(--blue);font-style:italic}}
.subtitle{{font-family:'DM Mono',monospace;font-size:12px;color:var(--muted);margin:6px 0 22px}}
.hero{{background:var(--surface);border:1px solid var(--border);border-radius:16px;padding:22px 24px;margin-bottom:14px;
  display:grid;grid-template-columns:1.3fr 1fr;gap:20px}}
.hero-label{{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:1.5px}}
.hero-val{{font-family:'DM Mono',monospace;font-size:clamp(34px,8vw,48px);color:#fff;margin:4px 0}}
.hero-pl{{font-family:'DM Mono',monospace;font-size:16px}}
.hero-since{{margin-top:10px;font-size:13px;color:var(--muted)}}
.kv{{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-bottom:1px solid var(--border);font-size:13px}}
.kv:last-child{{border-bottom:none}}
.kv span{{color:var(--muted)}} .kv b{{font-family:'DM Mono',monospace;font-weight:500}}
.note{{margin-top:8px;font-size:12px;color:var(--yellow)}}
.tiles{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:14px}}
.tile{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:14px 16px}}
.tile-label{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:6px}}
.tile-val{{font-family:'DM Mono',monospace;font-size:26px;color:#fff}}
.tile-sub{{font-size:11px;color:var(--muted);margin-top:4px}}
.winbar{{display:flex;height:8px;border-radius:4px;overflow:hidden;gap:2px;margin-top:10px;background:var(--surface2)}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:20px 22px;margin-bottom:14px}}
.card-label{{font-family:'DM Mono',monospace;font-size:10px;letter-spacing:2.5px;text-transform:uppercase;color:var(--muted);
  margin-bottom:14px;display:flex;align-items:center;gap:8px}}
.card-label::before{{content:'';width:14px;height:2px;background:var(--blue);border-radius:2px}}
.chart{{position:relative;overflow-x:auto}} .chart svg{{width:100%;min-width:520px;height:auto;display:block}}
.grid{{stroke:rgba(255,255,255,0.06);stroke-width:1}}
.base{{stroke:var(--muted);stroke-width:1;stroke-dasharray:4 4}}
.axis{{fill:var(--muted);font-family:'DM Mono',monospace;font-size:11px}}
.cross{{stroke:rgba(255,255,255,0.35);stroke-width:1}}
.mark{{stroke:var(--surface);stroke-width:2;cursor:pointer}}
.tip{{position:absolute;top:0;pointer-events:none;background:var(--surface2);border:1px solid var(--border);border-radius:8px;
  padding:8px 10px;font-family:'DM Mono',monospace;font-size:12px;white-space:nowrap;transform:translateX(-50%)}}
.tip small{{display:block;color:var(--muted);font-size:10px}}
.legend{{display:flex;flex-wrap:wrap;gap:16px;margin-top:10px;font-size:12px;color:var(--muted)}}
.legend i{{display:inline-block;vertical-align:middle;margin-right:6px}}
.legend .ln{{width:16px;height:2px;background:var(--blue)}}
.legend .dash{{width:16px;border-top:1px dashed var(--muted)}}
.pos-wrap{{display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:10px}}
.pos{{background:var(--surface2);border-radius:12px;padding:14px 16px}}
.pos-head{{display:flex;justify-content:space-between;align-items:baseline}}
.sym{{font-family:'DM Mono',monospace;font-size:18px;font-weight:500;color:#fff}}
.pnl{{font-family:'DM Mono',monospace;font-size:16px}}
.pos-sub{{font-size:12px;color:var(--muted);margin:2px 0 10px}}
.pos-grid{{display:grid;grid-template-columns:1fr 1fr;gap:6px 12px;font-family:'DM Mono',monospace;font-size:13px}}
.pos-grid span{{display:block;font-family:'Syne',sans-serif;font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1px}}
.bar{{height:6px;border-radius:3px;background:rgba(255,255,255,0.06);margin-top:12px;overflow:hidden}}
.bar div{{height:100%;border-radius:3px}}
.bar-label{{font-size:11px;color:var(--muted);margin-top:5px}}
.timeline{{position:relative;padding-left:4px}}
.ev{{display:flex;gap:12px;position:relative;padding-bottom:14px}}
.ev:not(:last-child)::before{{content:'';position:absolute;left:11px;top:24px;bottom:0;width:2px;background:var(--border)}}
.ev-dot{{flex:none;width:24px;height:24px;border-radius:50%;display:flex;align-items:center;justify-content:center;
  font-size:10px;color:var(--bg)}}
.ev-top{{display:flex;justify-content:space-between;gap:10px;font-family:'DM Mono',monospace;font-size:14px}}
.ev-top b{{color:#fff;font-weight:500}}
.ev-sub{{font-size:12px;color:var(--muted);margin-top:2px}}
.ev-body{{flex:1;min-width:0}}
.ev-more{{font-size:12px;color:var(--muted)}}
.empty{{padding:24px;text-align:center;color:var(--muted);font-size:13px}}
.strategy{{font-size:13px;color:var(--muted);line-height:1.8}}
.strategy b{{color:var(--text)}}
footer{{position:relative;z-index:10;text-align:center;padding:20px;font-family:'DM Mono',monospace;font-size:11px;
  color:var(--muted);border-top:1px solid var(--border)}}
@media (max-width:720px){{
  .hero{{grid-template-columns:1fr}} .tiles{{grid-template-columns:repeat(2,1fr)}}
  .wrap{{padding:72px 16px 40px}} .nav{{padding:10px 16px}}
}}
</style>
</head>
<body>
<nav class="nav">
  <span class="nav-label">Market Intelligence</span>
  <div class="nav-links">
    <a href="/" class="inactive">📉 Caídas</a>
    <a href="/portfolio" class="active">🤖 Auto BOT</a>
    <a href="/historico" class="inactive">📅 Historial</a>
    <a href="/japan-fares" class="inactive">✈️ Japan Fares</a>
  </div>
</nav>

<div class="wrap">
  <div class="eyebrow"><div class="pulse"></div> Chandelier Exit ATR · Paper Trading</div>
  <h1>Auto <span>BOT</span></h1>
  <div class="subtitle">Actualizado {escape(d["updated_at"])} · Cuenta paper {escape(d.get("account_id", ""))}</div>

  <div class="hero">
    <div>
      <div class="hero-label">Saldo actual de la cuenta</div>
      <div class="hero-val">{_money(equity)}</div>
      <div class="hero-pl" style="color:{plc}">{_money(pl, True)} · {_pct(pl_pct)} desde el inicio</div>
      <div class="hero-since">📅 {desde}</div>
    </div>
    <div>
      <div class="kv"><span>Capital inicial</span><b>{_money(initial)}</b></div>
      <div class="kv"><span>Invertido en acciones</span><b>{_money(invested)}</b></div>
      <div class="kv"><span>Efectivo</span><b style="color:{RED if cash < 0 else 'inherit'}">{_money(cash)}</b></div>
      <div class="kv"><span>Posiciones abiertas</span><b>{len(positions)} / {d["max_positions"]}</b></div>
      {cash_note}
    </div>
  </div>

  <div class="tiles">
    <div class="tile"><div class="tile-label">Operaciones cerradas</div>
      <div class="tile-val">{len(trades)}</div>
      <div class="tile-sub">+ {len(positions)} abiertas ahora</div></div>
    <div class="tile"><div class="tile-label">Ganadoras</div>
      <div class="tile-val" style="color:{GREEN}">▲ {len(wins)}</div>
      <div class="tile-sub">{_money(sum(t["pnl"] for t in wins), True)}</div></div>
    <div class="tile"><div class="tile-label">Perdedoras</div>
      <div class="tile-val" style="color:{RED}">▼ {len(losses)}</div>
      <div class="tile-sub">{_money(sum(t["pnl"] for t in losses), True)}</div></div>
    <div class="tile"><div class="tile-label">Tasa de acierto</div>
      <div class="tile-val">{win_rate:.0f}%</div>
      <div class="winbar" title="{len(wins)} ganadoras / {len(losses)} perdedoras">
        <div style="flex:{len(wins) or 0};background:{GREEN}"></div>
        <div style="flex:{len(losses) or 0};background:{RED}"></div></div></div>
  </div>

  <div class="card">
    <div class="card-label">Línea de tiempo del saldo</div>
    {_equity_chart(history, trades, initial)}
  </div>

  <div class="tiles">
    <div class="tile"><div class="tile-label">Ganancia realizada</div>
      <div class="tile-val" style="font-size:20px;color:{GREEN if realized >= 0 else RED}">{_money(realized, True)}</div>
      <div class="tile-sub">de operaciones cerradas</div></div>
    <div class="tile"><div class="tile-label">Ganancia no realizada</div>
      <div class="tile-val" style="font-size:20px;color:{GREEN if unrealized >= 0 else RED}">{_money(unrealized, True)}</div>
      <div class="tile-sub">de posiciones abiertas</div></div>
    <div class="tile"><div class="tile-label">Saldo máximo</div>
      <div class="tile-val" style="font-size:20px">{_money(peak)}</div>
      <div class="tile-sub">hoy {drawdown:+.1f}% vs. máximo</div></div>
    <div class="tile"><div class="tile-label">Días operando</div>
      <div class="tile-val" style="font-size:20px">{dias}</div>
      <div class="tile-sub">desde {_fecha_corta(start) if start else "—"}</div></div>
  </div>

  <div class="card">
    <div class="card-label">Posiciones abiertas · ordenadas por cercanía al stop</div>
    {_position_cards(positions)}
  </div>

  <div class="card">
    <div class="card-label">Historial de operaciones</div>
    {('<div style="margin-bottom:14px">' + best_worst + '</div>') if best_worst else ''}
    {_trade_timeline(trades)}
  </div>

  <div class="card">
    <div class="card-label">Cómo opera el bot</div>
    <div class="strategy">
      <b>Compra</b> cuando una acción rompe su máximo de los últimos días con <b>volumen ≥ 1.5×</b> el promedio
      y está por encima de su <b>media de 50 días</b>. <b>Vende</b> cuando el precio cae por debajo de su
      <b>stop dinámico</b>: el máximo desde la compra menos <b>2.5 × ATR(14)</b>, que solo sube.
      Arriesga como máximo el <b>2%</b> del portafolio por operación y tiene hasta <b>{d["max_positions"]}</b> posiciones.
    </div>
  </div>
</div>

<footer>Chandelier Exit Bot · Alpaca Paper Trading · Solo fines educativos</footer>
<script>
(function(){{
  var box=document.getElementById('eqchart'); if(!box) return;
  var data=JSON.parse(document.getElementById('eqdata').textContent);
  var svg=box.querySelector('svg'), tip=document.getElementById('eqtip'),
      vx=document.getElementById('eqx'), dot=document.getElementById('eqdot');
  var L=+box.dataset.l, R=+box.dataset.r, W=+box.dataset.w;
  var pts=[].slice.call(svg.querySelectorAll('path'))[1].getAttribute('d').slice(1).split(' L').map(function(p){{return p.split(',').map(Number);}});
  var meses=['ene','feb','mar','abr','may','jun','jul','ago','sep','oct','nov','dic'];
  function fecha(s){{var p=s.split('-');return (+p[2])+' '+meses[+p[1]-1]+' '+p[0];}}
  function show(e){{
    var r=svg.getBoundingClientRect(), cx=(e.touches?e.touches[0].clientX:e.clientX)-r.left;
    var vxp=cx*W/r.width, i=Math.round((vxp-L)/(R-L)*(data.length-1));
    i=Math.max(0,Math.min(data.length-1,i));
    var p=pts[i]; vx.setAttribute('x1',p[0]); vx.setAttribute('x2',p[0]); vx.style.display='';
    dot.setAttribute('cx',p[0]); dot.setAttribute('cy',p[1]); dot.style.display='';
    var diff=data[i][1]-{initial};
    tip.innerHTML='$'+Math.round(data[i][1]).toLocaleString('en-US')+
      '<small>'+fecha(data[i][0])+' · '+(diff>=0?'+':'−')+'$'+Math.abs(Math.round(diff)).toLocaleString('en-US')+'</small>';
    tip.style.display=''; tip.style.left=Math.max(60,Math.min(r.width-60,p[0]*r.width/W))+'px';
  }}
  function hide(){{tip.style.display='none';vx.style.display='none';dot.style.display='none';}}
  svg.addEventListener('mousemove',show); svg.addEventListener('touchmove',show,{{passive:true}});
  svg.addEventListener('mouseleave',hide); svg.addEventListener('touchend',hide);
}})();
</script>
</body>
</html>"""


def closed_trades_from_fills(fills):
    """Empareja compras y ventas (FIFO por símbolo) y devuelve las operaciones cerradas.

    fills: actividades FILL de Alpaca en orden cronológico
           (symbol, side, qty, price, transaction_time).
    """
    lots, trades = {}, []
    for f in fills:
        sym, side = f["symbol"], f["side"]
        qty, price, when = float(f["qty"]), float(f["price"]), f["transaction_time"]
        if side == "buy":
            lots.setdefault(sym, []).append([qty, price, when])
            continue
        # sell / sell_short: cierra lotes abiertos en orden de compra
        remaining, cost, opened = qty, 0.0, None
        queue = lots.get(sym, [])
        while remaining > 1e-9 and queue:
            lot = queue[0]
            take = min(lot[0], remaining)
            cost += take * lot[1]
            opened = opened or lot[2]
            lot[0] -= take
            remaining -= take
            if lot[0] <= 1e-9:
                queue.pop(0)
        matched = qty - remaining
        if matched <= 0:
            continue  # venta sin compra registrada (fuera del historial)
        entry = cost / matched
        pnl = (price - entry) * matched
        trades.append({"symbol": sym, "qty": round(matched), "entry": entry, "exit": price,
                       "pnl": pnl, "pnl_pct": (price - entry) / entry * 100,
                       "opened": opened, "closed": when})

    # Las ventas del mismo símbolo el mismo día (fills parciales, o una posición comprada en
    # varias partes) son una sola operación.
    merged = {}
    for t in trades:
        key = (t["symbol"], t["closed"][:10])
        m = merged.get(key)
        if not m:
            merged[key] = dict(t)
            continue
        total = m["qty"] + t["qty"]
        m["entry"] = (m["entry"] * m["qty"] + t["entry"] * t["qty"]) / total
        m["exit"] = (m["exit"] * m["qty"] + t["exit"] * t["qty"]) / total
        m["qty"], m["pnl"] = total, m["pnl"] + t["pnl"]
        m["pnl_pct"] = (m["exit"] - m["entry"]) / m["entry"] * 100
        m["opened"] = min(m["opened"], t["opened"])
    return list(merged.values())
