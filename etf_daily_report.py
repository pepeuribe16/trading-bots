"""
AutoBOT-ETF-Daily-Report
Toma foto del portafolio al cierre, genera HTML /portfolio,
manda email resumen. Si cayó >8%, cierra todo primero.
"""
import requests
import json
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime

BASE = "https://paper-api.alpaca.markets/v2"
HEADS = {
    "APCA-API-KEY-ID": os.environ["APCA_API_KEY_ID"].encode("ascii", "ignore").decode("ascii").strip(),
    "APCA-API-SECRET-KEY": os.environ["APCA_API_SECRET_KEY"].encode("ascii", "ignore").decode("ascii").strip(),
}
EMAIL_FROM = os.environ.get("EMAIL_FROM", "pepeuribe16@gmail.com")
EMAIL_TO = os.environ.get("EMAIL_TO", "pepeuribe16@gmail.com")
EMAIL_PASSWORD = os.environ.get("EMAIL_PASSWORD", "")
DRAWDOWN_LIMIT = 0.08


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


def alpaca_get(path):
    r = requests.get(BASE + path, headers=HEADS, timeout=10)
    r.raise_for_status()
    return r.json()


def close_all():
    r = requests.delete(BASE + "/positions", headers=HEADS, timeout=15)
    log(f"Cierre de emergencia: {r.status_code}")


def generate_html(account, positions, emergency=False):
    equity = float(account["equity"])
    cash = float(account["cash"])
    last_equity = float(account.get("last_equity", equity))
    pl_day = equity - last_equity
    pl_pct = (pl_day / last_equity * 100) if last_equity > 0 else 0
    date_str = datetime.now().strftime("%Y-%m-%d %H:%M ET")

    rows = ""
    for p in positions:
        sym = p["symbol"]
        qty = p["qty"]
        price = float(p["current_price"])
        entry = float(p["avg_entry_price"])
        pl = float(p["unrealized_plpc"]) * 100
        sign = "▲" if pl >= 0 else "▼"
        color = "#38a169" if pl >= 0 else "#e53e3e"
        rows += f"""<tr>
            <td><strong>{sym}</strong></td><td>{qty}</td>
            <td>${price:.2f}</td><td>${entry:.2f}</td>
            <td style="color:{color}">{sign}{abs(pl):.1f}%</td>
        </tr>"""

    emergency_banner = """<div style="background:#e53e3e;color:white;padding:14px;border-radius:8px;
        margin-bottom:20px;font-weight:600">
        ⚠️ CIERRE DE EMERGENCIA — El portafolio cayó más del 8%.
        Todas las posiciones fueron cerradas automáticamente.</div>""" if emergency else ""

    pl_color = "#38a169" if pl_pct >= 0 else "#e53e3e"

    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Portfolio ETF · {date_str}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:system-ui,sans-serif;background:#f8fafc;color:#1e293b;padding:32px 24px;max-width:800px;margin:0 auto}}
h1{{font-size:1.6em;font-weight:700;margin-bottom:4px}}
.subtitle{{color:#64748b;margin-bottom:24px}}
.card{{background:white;border-radius:10px;padding:20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,.08)}}
.card h2{{font-size:1em;font-weight:600;color:#475569;margin-bottom:12px;text-transform:uppercase;letter-spacing:.05em}}
.metric{{font-size:2.2em;font-weight:700}}
.sub{{color:#64748b;font-size:.9em;margin-top:4px}}
table{{width:100%;border-collapse:collapse;margin-top:8px}}
th{{text-align:left;padding:10px;background:#f1f5f9;font-size:.85em;color:#475569;font-weight:600}}
td{{padding:10px;border-bottom:1px solid #f1f5f9;font-size:.95em}}
footer{{color:#94a3b8;font-size:.82em;text-align:center;margin-top:24px}}
</style></head><body>
<h1>📊 Portfolio ETF</h1>
<p class="subtitle">{date_str}</p>
{emergency_banner}
<div class="card">
    <h2>Resumen del día</h2>
    <div class="metric">${equity:,.2f}</div>
    <div class="sub">Equity total · Cash disponible: ${cash:,.2f}</div>
    <div class="sub" style="color:{pl_color};margin-top:8px;font-weight:600">
        P&L hoy: {'▲' if pl_pct >= 0 else '▼'}{abs(pl_pct):.2f}% (${pl_day:+,.2f})
    </div>
</div>
<div class="card">
    <h2>Posiciones abiertas</h2>
    <table>
        <thead><tr><th>Símbolo</th><th>Qty</th><th>Precio</th><th>Entrada</th><th>P&L</th></tr></thead>
        <tbody>{rows if rows else "<tr><td colspan='5' style='color:#94a3b8;text-align:center;padding:20px'>Sin posiciones abiertas</td></tr>"}</tbody>
    </table>
</div>
<footer>AutoBOT-ETF-Daily-Report · Generado automáticamente</footer>
</body></html>"""


def send_email(subject, html):
    if not EMAIL_PASSWORD:
        log("Sin contraseña de email configurada — omitiendo envío")
        return
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM
    msg["To"] = EMAIL_TO
    msg.attach(MIMEText(html, "html"))
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as smtp:
        smtp.login(EMAIL_FROM, EMAIL_PASSWORD)
        smtp.send_message(msg)
    log(f"Email enviado a {EMAIL_TO}")


def run():
    log("AutoBOT-ETF-Daily-Report iniciando...")

    account = alpaca_get("/account")
    positions = alpaca_get("/positions")

    equity = float(account["equity"])
    last_equity = float(account.get("last_equity", equity))
    drawdown = (last_equity - equity) / last_equity if last_equity > 0 else 0

    log(f"Equity: ${equity:,.2f} | Último cierre: ${last_equity:,.2f} | Drawdown: {drawdown*100:.2f}%")

    emergency = False
    if drawdown >= DRAWDOWN_LIMIT:
        log(f"EMERGENCIA: caída {drawdown*100:.1f}% >= {DRAWDOWN_LIMIT*100}% — cerrando todo")
        close_all()
        emergency = True
        positions = []

    html = generate_html(account, positions, emergency)

    os.makedirs("public/portfolio", exist_ok=True)
    with open("public/portfolio/index.html", "w", encoding="utf-8") as f:
        f.write(html)
    log("public/portfolio/index.html generado")

    subject = f"📊 ETF Report {datetime.now().strftime('%Y-%m-%d')}"
    if emergency:
        subject += " — ⚠️ CIERRE DE EMERGENCIA"
    send_email(subject, html)
    log("Listo.")


if __name__ == "__main__":
    run()
