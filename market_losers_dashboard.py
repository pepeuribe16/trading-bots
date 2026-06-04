import yfinance as yf
import anthropic
import json
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


def get_top_losers():
    log(f"Descargando datos de {len(UNIVERSE)} acciones...")
    data = yf.download(UNIVERSE, period="2d", progress=False, group_by="ticker")
    losers = []
    for sym in UNIVERSE:
        try:
            ticker_data = data[sym] if sym in data.columns.get_level_values(0) else None
            if ticker_data is None or len(ticker_data) < 2:
                continue
            prev = float(ticker_data["Close"].iloc[-2])
            curr = float(ticker_data["Close"].iloc[-1])
            if prev <= 0:
                continue
            change_pct = (curr - prev) / prev * 100
            if change_pct < 0:
                losers.append({"symbol": sym, "price": curr, "change_pct": change_pct, "prev": prev})
        except Exception:
            continue
    losers.sort(key=lambda x: x["change_pct"])
    return losers[:5]


def analyze_with_claude(losers):
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    loser_text = "\n".join(
        f"- {l['symbol']}: {l['change_pct']:.2f}% (${l['prev']:.2f} → ${l['price']:.2f})"
        for l in losers
    )
    response = client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": f"""Analiza estas 5 acciones que más bajaron hoy en NYSE/NASDAQ:

{loser_text}

Para cada una responde en JSON con esta estructura exacta:
{{
  "analyses": [
    {{
      "symbol": "TICKER",
      "reason": "Razón principal de la caída (1-2 oraciones)",
      "valuation": "Valuación actual con P/E u otra métrica clave",
      "verdict": "COMPRAR" | "ESPERAR" | "EVITAR",
      "verdict_reason": "Por qué ese veredicto (1 oración)"
    }}
  ]
}}

Responde SOLO el JSON, sin texto adicional.""",
        }],
    )
    return json.loads(response.content[0].text)["analyses"]


def generate_html(losers, analyses):
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    verdict_colors = {"COMPRAR": "#38a169", "ESPERAR": "#d69e2e", "EVITAR": "#e53e3e"}

    cards = ""
    for l, a in zip(losers, analyses):
        color = verdict_colors.get(a["verdict"], "#718096")
        cards += f"""
        <div class="card">
            <div class="card-header">
                <span class="symbol">{l['symbol']}</span>
                <span class="change">▼{abs(l['change_pct']):.2f}%</span>
                <span class="verdict" style="background:{color}">{a['verdict']}</span>
            </div>
            <div class="price">${l['price']:.2f} <span class="prev">(ayer: ${l['prev']:.2f})</span></div>
            <div class="section"><strong>¿Por qué bajó?</strong><p>{a['reason']}</p></div>
            <div class="section"><strong>Valuación</strong><p>{a['valuation']}</p></div>
            <div class="section"><strong>Veredicto</strong><p>{a['verdict_reason']}</p></div>
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
.card-header{{display:flex;align-items:center;gap:12px;margin-bottom:12px}}
.symbol{{font-size:1.4em;font-weight:700;color:#f1f5f9}}
.change{{font-size:1.1em;color:#f87171;font-weight:600}}
.verdict{{padding:4px 12px;border-radius:20px;font-size:.8em;font-weight:700;color:white;margin-left:auto}}
.price{{font-size:1.2em;font-weight:600;color:#cbd5e1;margin-bottom:16px}}
.prev{{font-size:.85em;color:#64748b}}
.section{{margin-top:12px}}
.section strong{{color:#94a3b8;font-size:.8em;text-transform:uppercase;letter-spacing:.05em;display:block;margin-bottom:4px}}
.section p{{color:#cbd5e1;line-height:1.5;font-size:.95em}}
footer{{text-align:center;color:#475569;margin-top:32px;font-size:.85em}}
</style></head><body>
<h1>📉 Market Losers Dashboard</h1>
<p class="subtitle">Top 5 mayores caídas del día · {date_str} · NYSE/NASDAQ</p>
<div class="grid">{cards}</div>
<footer>Generado automáticamente con IA · market-dashboard-gga.web.app</footer>
</body></html>"""


def run():
    log("market-losers-dashboard iniciando...")
    losers = get_top_losers()

    if not losers:
        log("No se encontraron perdedores (mercado posiblemente cerrado)")
        return

    log(f"Top 5 perdedores: {[l['symbol'] for l in losers]}")
    log("Analizando con Claude...")
    analyses = analyze_with_claude(losers)

    html = generate_html(losers, analyses)

    os.makedirs("public", exist_ok=True)
    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    log("public/index.html generado — listo para Firebase deploy")


if __name__ == "__main__":
    run()
