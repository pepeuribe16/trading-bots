"""
market-losers-dashboard
Top 5 perdedores del día en NYSE/NASDAQ con análisis fundamental automático.
No requiere Anthropic API — usa yfinance para P/E, RSI y rango 52 semanas.
"""
import yfinance as yf
import os
from datetime import datetime

UNIVERSE = [
    "AAPL","MSFT","GOOGL","AMZN","NVDA","META","TSLA","JPM","JNJ","V",
    "PG","UNH","HD","MA","BAC","ABBV","PFE","KO","PEP","COST",
    "MRK","AVGO","CVX","TMO","CSCO","ACN","MCD","ABT","CRM","WMT",
    "NFLX","AMD","INTC","QCOM","TXN","RTX","PM","NEE","UPS","AMGN",
    "IBM","GS","MS","BLK","SYK","GILD","MDLZ","ADI","ISRG","NOW",
]


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def compute_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0))
        losses.append(max(-d, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - 100 / (1 + rs), 1)


def analyze(ticker_obj, change_pct):
    """Genera razón, valuación y veredicto sin IA externa."""
    info = ticker_obj.info or {}
    hist = ticker_obj.history(period="1y")

    price = info.get("currentPrice") or info.get("regularMarketPrice", 0)
    pe = info.get("trailingPE")
    fwd_pe = info.get("forwardPE")
    week52_low = info.get("fiftyTwoWeekLow")
    week52_high = info.get("fiftyTwoWeekHigh")
    sector = info.get("sector", "")
    short_ratio = info.get("shortRatio")
    beta = info.get("beta")
    name = info.get("shortName") or info.get("longName", "")

    closes = list(hist["Close"]) if not hist.empty else []
    rsi = compute_rsi(closes) if closes else None

    # Posición en rango 52 semanas (0% = mínimo, 100% = máximo)
    pos_52w = None
    if week52_low and week52_high and week52_high > week52_low and price:
        pos_52w = (price - week52_low) / (week52_high - week52_low) * 100

    # --- Razón de la caída ---
    reasons = []
    if abs(change_pct) > 8:
        reasons.append("caída brusca posiblemente por resultados o noticia negativa")
    elif abs(change_pct) > 4:
        reasons.append("corrección significativa intraday")
    else:
        reasons.append("presión vendedora moderada")

    if beta and beta > 1.5:
        reasons.append(f"acción de alta volatilidad (β={beta:.1f})")
    if short_ratio and short_ratio > 5:
        reasons.append(f"alto interés corto (short ratio {short_ratio:.1f}x)")
    if rsi and rsi < 35:
        reasons.append(f"RSI sobrevendido ({rsi})")
    reason = f"{name} ({sector}): {', '.join(reasons)}." if name else ", ".join(reasons) + "."

    # --- Valuación ---
    if pe:
        sector_avg = {"Technology": 28, "Health Care": 22, "Financials": 13,
                      "Consumer Discretionary": 25, "Energy": 12}.get(sector, 20)
        rel = "caro" if pe > sector_avg * 1.3 else "barato" if pe < sector_avg * 0.7 else "justo"
        valuation = f"P/E trailing {pe:.1f}x ({rel} vs sector ~{sector_avg}x)"
        if fwd_pe:
            valuation += f", forward P/E {fwd_pe:.1f}x"
    elif pos_52w is not None:
        valuation = f"Cotiza al {pos_52w:.0f}% de su rango anual (52w: ${week52_low:.0f}–${week52_high:.0f})"
    else:
        valuation = "Datos de valuación no disponibles"

    # --- Veredicto ---
    score = 0
    if rsi and rsi < 35:
        score += 2
    elif rsi and rsi < 45:
        score += 1
    if pos_52w is not None and pos_52w < 20:
        score += 2
    elif pos_52w is not None and pos_52w < 35:
        score += 1
    if pe and pe < 15:
        score += 1
    if fwd_pe and fwd_pe < 15:
        score += 1
    if abs(change_pct) > 10:
        score -= 1  # caída muy grande = riesgo

    if score >= 3:
        verdict, v_color = "COMPRAR", "#38a169"
        v_reason = "Múltiples indicadores de sobreventa sugieren oportunidad de entrada."
    elif score >= 1:
        verdict, v_color = "ESPERAR", "#d69e2e"
        v_reason = "Señales mixtas — esperar confirmación de rebote antes de entrar."
    else:
        verdict, v_color = "EVITAR", "#e53e3e"
        v_reason = "Sin señales claras de sobreventa o valuación atractiva."

    return reason, valuation, verdict, v_color, v_reason, rsi, pos_52w


def get_top_losers():
    log(f"Descargando datos de {len(UNIVERSE)} acciones...")
    data = yf.download(UNIVERSE, period="2d", progress=False, group_by="ticker")
    losers = []
    for sym in UNIVERSE:
        try:
            td = data[sym] if sym in data.columns.get_level_values(0) else None
            if td is None or len(td) < 2:
                continue
            prev = float(td["Close"].iloc[-2])
            curr = float(td["Close"].iloc[-1])
            if prev <= 0:
                continue
            chg = (curr - prev) / prev * 100
            if chg < 0:
                losers.append({"symbol": sym, "price": curr, "change_pct": chg, "prev": prev})
        except Exception:
            continue
    losers.sort(key=lambda x: x["change_pct"])
    return losers[:5]


VERDICT_COLOR = {"COMPRAR": "var(--green)", "ESPERAR": "var(--yellow)", "EVITAR": "var(--red)"}


def generate_html(losers, analyses):
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    avg_chg = sum(l["change_pct"] for l in losers) / len(losers)

    cards = ""
    for l, (reason, valuation, verdict, v_color, v_reason, rsi, pos_52w) in zip(losers, analyses):
        badge_color = VERDICT_COLOR.get(verdict, "var(--muted)")
        rsi_badge = f'<span class="tag">RSI {rsi}</span>' if rsi else ""
        pos_badge = f'<span class="tag">52w {pos_52w:.0f}%</span>' if pos_52w is not None else ""
        cards += f"""
        <div class="card">
          <div class="loser-head">
            <span class="loser-symbol">{l['symbol']}</span>
            <span class="loser-change">▼{abs(l['change_pct']):.2f}%</span>
            <span class="verdict-badge" style="background:{badge_color}">{verdict}</span>
          </div>
          <div class="loser-price">${l['price']:.2f} <span class="prev">(ayer ${l['prev']:.2f})</span>{rsi_badge}{pos_badge}</div>
          <div class="loser-section"><span class="label">¿Por qué bajó?</span><p>{reason}</p></div>
          <div class="loser-section"><span class="label">Valuación</span><p>{valuation}</p></div>
          <div class="loser-section"><span class="label">Veredicto</span><p>{v_reason}</p></div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Market Losers · {date_str}</title>
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
.wrap{{position:relative;z-index:10;padding:72px 24px 60px;max-width:1040px;margin:0 auto}}
.eyebrow{{font-family:'DM Mono',monospace;font-size:11px;letter-spacing:3px;text-transform:uppercase;
  color:var(--blue);margin-bottom:8px;display:flex;align-items:center;gap:8px}}
.pulse{{width:6px;height:6px;background:var(--blue);border-radius:50%;animation:pulse 1.5s ease-in-out infinite}}
@keyframes pulse{{0%,100%{{opacity:1;transform:scale(1)}}50%{{opacity:0.4;transform:scale(0.7)}}}}
h1{{font-family:'DM Serif Display',serif;font-size:clamp(28px,6vw,44px);line-height:1.1;color:#fff;margin-bottom:4px}}
h1 span{{color:var(--red);font-style:italic}}
.subtitle{{font-family:'DM Mono',monospace;font-size:12px;color:var(--muted);margin-bottom:28px}}
.metrics{{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:10px;margin-bottom:28px}}
.metric{{background:var(--surface);border:1px solid var(--border);border-radius:12px;padding:16px 18px}}
.metric-label{{font-size:10px;color:var(--muted);text-transform:uppercase;letter-spacing:1.5px;margin-bottom:6px}}
.metric-val{{font-family:'DM Mono',monospace;font-size:22px;font-weight:500;color:#fff}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:14px}}
.card{{background:var(--surface);border:1px solid var(--border);border-radius:14px;padding:20px 22px}}
.loser-head{{display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap}}
.loser-symbol{{font-family:'DM Serif Display',serif;font-size:1.5em;color:#fff}}
.loser-change{{font-family:'DM Mono',monospace;font-size:1.05em;color:var(--red);font-weight:600}}
.verdict-badge{{padding:4px 12px;border-radius:20px;font-family:'Syne',sans-serif;font-size:.72em;
  font-weight:700;letter-spacing:.5px;color:#0A0C10;margin-left:auto}}
.loser-price{{font-family:'DM Mono',monospace;font-size:1.05em;color:var(--text);margin-bottom:14px;
  display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
.prev{{font-size:.82em;color:var(--muted)}}
.tag{{background:var(--surface2);padding:2px 9px;border-radius:12px;font-size:.72em;color:var(--muted)}}
.loser-section{{margin-top:12px}}
.loser-section .label{{color:var(--muted);font-size:.75em;text-transform:uppercase;letter-spacing:.08em;
  display:block;margin-bottom:4px;font-family:'DM Mono',monospace}}
.loser-section p{{color:var(--text);line-height:1.55;font-size:.9em}}
footer{{position:relative;z-index:10;text-align:center;padding:20px;
  font-family:'DM Mono',monospace;font-size:11px;color:var(--muted);
  border-top:1px solid var(--border);margin-top:32px}}
</style>
</head>
<body>

<nav class="nav">
  <span class="nav-label">Market Intelligence</span>
  <div class="nav-links">
    <a href="/" class="active">📉 Caídas</a>
    <a href="/portfolio" class="inactive">🤖 Auto BOT</a>
    <a href="/historico" class="inactive">📅 Historial</a>
  </div>
</nav>

<div class="wrap">
  <div class="eyebrow"><div class="pulse"></div> NYSE / NASDAQ · Análisis automático</div>
  <h1>Market <span>Losers</span></h1>
  <div class="subtitle">📅 {date_str}</div>

  <div class="metrics">
    <div class="metric">
      <div class="metric-label">Mayor Caída</div>
      <div class="metric-val" style="color:var(--red)">{losers[0]['symbol']} ▼{abs(losers[0]['change_pct']):.1f}%</div>
    </div>
    <div class="metric">
      <div class="metric-label">Promedio Top 5</div>
      <div class="metric-val" style="color:var(--red)">▼{abs(avg_chg):.1f}%</div>
    </div>
    <div class="metric">
      <div class="metric-label">Universo Analizado</div>
      <div class="metric-val">{len(UNIVERSE)}</div>
    </div>
  </div>

  <div class="grid">{cards}</div>
</div>

<footer>Análisis automático con yfinance (P/E · RSI · Rango 52 semanas) · market-dashboard-gga.web.app</footer>
</body>
</html>"""


def run():
    log("market-losers-dashboard iniciando...")
    losers = get_top_losers()

    if not losers:
        log("No se encontraron perdedores (mercado posiblemente cerrado)")
        return

    log(f"Top 5: {[l['symbol'] for l in losers]}")
    log("Analizando con yfinance...")
    analyses = []
    for l in losers:
        ticker = yf.Ticker(l["symbol"])
        analyses.append(analyze(ticker, l["change_pct"]))

    html = generate_html(losers, analyses)
    os.makedirs("public/historico", exist_ok=True)
    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    # Guarda copia fechada para el Historial
    dated = f"public/historico/{datetime.now().strftime('%Y-%m-%d')}.html"
    with open(dated, "w", encoding="utf-8") as f:
        f.write(html)
    log(f"public/index.html + {dated} generados — listos para Firebase deploy")


if __name__ == "__main__":
    run()
