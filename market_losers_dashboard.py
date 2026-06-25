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


def generate_html(losers, analyses):
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    cards = ""
    for l, (reason, valuation, verdict, v_color, v_reason, rsi, pos_52w) in zip(losers, analyses):
        rsi_badge = f'<span style="background:#334155;padding:2px 8px;border-radius:12px;font-size:.8em">RSI {rsi}</span>' if rsi else ""
        pos_badge = f'<span style="background:#334155;padding:2px 8px;border-radius:12px;font-size:.8em">52w {pos_52w:.0f}%</span>' if pos_52w is not None else ""
        cards += f"""
        <div class="card">
            <div class="card-header">
                <span class="symbol">{l['symbol']}</span>
                <span class="change">▼{abs(l['change_pct']):.2f}%</span>
                <span class="verdict" style="background:{v_color}">{verdict}</span>
            </div>
            <div class="price">${l['price']:.2f} <span class="prev">(ayer: ${l['prev']:.2f})</span>
                &nbsp;{rsi_badge}{pos_badge}
            </div>
            <div class="section"><strong>¿Por qué bajó?</strong><p>{reason}</p></div>
            <div class="section"><strong>Valuación</strong><p>{valuation}</p></div>
            <div class="section"><strong>Veredicto</strong><p>{v_reason}</p></div>
        </div>"""

    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Market Losers · {date_str}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;padding:24px}}
h1{{font-size:1.8em;font-weight:700;margin-bottom:4px}}
.subtitle{{color:#94a3b8;margin-bottom:24px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:20px}}
.card{{background:#1e293b;border-radius:12px;padding:20px;border:1px solid #334155}}
.card-header{{display:flex;align-items:center;gap:10px;margin-bottom:10px;flex-wrap:wrap}}
.symbol{{font-size:1.4em;font-weight:700;color:#f1f5f9}}
.change{{font-size:1.1em;color:#f87171;font-weight:600}}
.verdict{{padding:4px 12px;border-radius:20px;font-size:.8em;font-weight:700;color:white;margin-left:auto}}
.price{{font-size:1.1em;font-weight:600;color:#cbd5e1;margin-bottom:14px;display:flex;align-items:center;gap:8px;flex-wrap:wrap}}
.prev{{font-size:.85em;color:#64748b}}
.section{{margin-top:12px}}
.section strong{{color:#94a3b8;font-size:.8em;text-transform:uppercase;letter-spacing:.05em;display:block;margin-bottom:4px}}
.section p{{color:#cbd5e1;line-height:1.5;font-size:.92em}}
footer{{text-align:center;color:#475569;margin-top:32px;font-size:.82em}}
</style></head><body>
<h1>📉 Market Losers Dashboard</h1>
<p class="subtitle">Top 5 mayores caídas · {date_str} · NYSE/NASDAQ</p>
<div class="grid">{cards}</div>
<footer>Análisis automático con yfinance (P/E · RSI · Rango 52 semanas) · market-dashboard-gga.web.app</footer>
</body></html>"""


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
