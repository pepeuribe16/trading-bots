"""
GDL → Japan Search-Link Digest
-------------------------------
Agente diario que arma enlaces de busqueda listos para abrir (Google
Flights, Skyscanner, Kayak) para viajes GDL -> Japon de 13-15 dias en
noviembre-diciembre 2026, y los manda por correo.

No cotiza precios: se probaron dos fuentes de datos y ninguna sirvio para
un origen como GDL --
  - Amadeus Self-Service: cerro su portal de alta el 17 de julio de 2026.
  - Travelpayouts Data API: es gratis pero solo devuelve tarifas *cacheadas
    de busquedas reales de otros usuarios*; GDL no tiene volumen de
    busqueda en Aviasales, asi que el cache esta vacio sin importar el
    destino o las fechas.
Cualquier fuente de datos en vivo real (Duffel, Amadeus Enterprise, etc.)
tiene costo. Mientras tanto este bot solo automatiza el armado de los
enlaces de busqueda -- el precio se revisa manualmente al abrirlos.

Salida: public/japan-fares/index.html (Firebase) + correo HTML.
"""
import json
import os
import smtplib
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

_HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(_HERE, "config.json"), encoding="utf-8") as f:
    cfg = json.load(f)

ORIGIN = cfg["origin"]
DESTINATIONS = cfg["destinations"]
DURATIONS = cfg["durations_days"]


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ── Enlaces ──────────────────────────────────────────────────────────────────
def google_flights_link(origin, dest, dep, ret):
    return (
        f"https://www.google.com/travel/flights?q=Flights%20from%20{origin}%20to%20{dest}"
        f"%20on%20{dep.isoformat()}%20through%20{ret.isoformat()}"
    )


def skyscanner_link(origin, dest, dep, ret):
    return (
        f"https://www.skyscanner.com/transport/flights/{origin.lower()}/{dest.lower()}/"
        f"{dep.strftime('%y%m%d')}/{ret.strftime('%y%m%d')}/"
    )


def kayak_link(origin, dest, dep, ret):
    return f"https://www.kayak.com/flights/{origin}-{dest}/{dep.isoformat()}/{ret.isoformat()}"


def anchor_dates(dest):
    days = cfg["priority_anchor_days"] if dest["priority"] == 1 else cfg["secondary_anchor_days"]
    dates = []
    for w in cfg["travel_windows"]:
        for d in days:
            dates.append(date(w["year"], w["month"], d))
    return dates


def build_rows():
    rows = []
    for dest in DESTINATIONS:
        for dep in anchor_dates(dest):
            legs = []
            for duration in DURATIONS:
                ret = dep + timedelta(days=duration)
                legs.append({
                    "duration": duration,
                    "ret_date": ret,
                    "google_flights": google_flights_link(ORIGIN, dest["code"], dep, ret),
                    "skyscanner": skyscanner_link(ORIGIN, dest["code"], dep, ret),
                    "kayak": kayak_link(ORIGIN, dest["code"], dep, ret),
                })
            rows.append({
                "dest_code": dest["code"],
                "dest_city": dest["city"],
                "priority": dest["priority"],
                "dep_date": dep,
                "legs": legs,
            })
    return rows


MESES_ES = {1: "ene", 2: "feb", 3: "mar", 4: "abr", 5: "may", 6: "jun",
            7: "jul", 8: "ago", 9: "sep", 10: "oct", 11: "nov", 12: "dic"}


def fmt_es(d):
    return f"{d.day} {MESES_ES[d.month]} {d.year}"


# ── Reporte (correo, en texto/markdown) ─────────────────────────────────────
def render_row(row):
    legs_md = "\n".join(
        f"  - **{leg['duration']} días** (regreso {leg['ret_date'].isoformat()}): "
        f"[Google Flights]({leg['google_flights']}) · [Skyscanner]({leg['skyscanner']}) · [Kayak]({leg['kayak']})"
        for leg in row["legs"]
    )
    return f"- **{row['dest_city']} ({row['dest_code']})** — salida {row['dep_date'].isoformat()}\n{legs_md}"


def build_report(rows):
    fecha = date.today().isoformat()
    priority_rows = [r for r in rows if r["priority"] == 1]
    secondary_rows = [r for r in rows if r["priority"] == 2]

    parts = [
        f"# Enlaces GDL → Japón — {fecha}\n",
        "_Este reporte no cotiza precios (no hay fuente de precios en vivo gratuita para GDL — "
        "ver nota al final). Abre cada enlace para ver tarifas y disponibilidad actuales._\n",
        "## Tokio (prioridad)\n",
        "\n".join(render_row(r) for r in priority_rows),
        "\n## Otros destinos en Japón\n",
        "\n".join(render_row(r) for r in secondary_rows),
        "\n---\n",
        (
            "**¿Por qué no hay precios?** Se probaron Amadeus Self-Service (cerró su alta el "
            "17 de julio de 2026) y la Data API de Travelpayouts (gratis, pero solo cubre rutas "
            "con historial real de búsquedas en Aviasales — GDL no tiene volumen ahí, así que no "
            "devuelve nada). Una fuente en vivo real tiene costo (ej. Duffel, ~$0.005/búsqueda). "
            "Mientras tanto, compara manualmente en los enlaces de arriba."
        ),
    ]
    return "\n".join(parts)


def markdown_to_html_body(md_text):
    import html as _html
    esc = _html.escape(md_text)
    html_body = esc.replace("\n### ", "\n<h3>").replace("\n## ", "\n<h2>").replace("\n# ", "\n<h1>")
    lines = html_body.split("\n")
    out = []
    for line in lines:
        if line.startswith("<h1>"):
            out.append(f"<h1>{line[4:]}</h1>")
        elif line.startswith("<h2>"):
            out.append(f"<h2>{line[4:]}</h2>")
        elif line.startswith("<h3>"):
            out.append(f"<h3>{line[4:]}</h3>")
        elif line.lstrip().startswith("- "):
            indent = len(line) - len(line.lstrip())
            out.append(f"{'&nbsp;' * indent}<li>{line.lstrip()[2:]}</li>")
        elif line.strip() == "---":
            out.append("<hr>")
        elif line.strip() == "":
            out.append("<br>")
        else:
            out.append(f"<p>{line}</p>")
    body = "\n".join(out)
    import re
    body = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', body)
    body = body.replace("**", "")
    return (
        "<html><body style='font-family:system-ui,sans-serif;color:#1a1a1a;line-height:1.6;"
        "max-width:800px;margin:0 auto'>" + body + "</body></html>"
    )


# ── Historial / pestañas ─────────────────────────────────────────────────────
def list_historico_dates(out_dir, today):
    """Fechas con reporte disponible (incluye la de hoy aunque aún no se haya escrito)."""
    hist_dir = os.path.join(out_dir, "historico")
    dates = set()
    if os.path.isdir(hist_dir):
        for fname in os.listdir(hist_dir):
            if fname.endswith(".html"):
                d = fname[:-5]
                try:
                    datetime.strptime(d, "%Y-%m-%d")
                    dates.add(d)
                except ValueError:
                    continue
    dates.add(today)
    return sorted(dates, reverse=True)


def render_tabs(dates, current, in_historico, max_tabs=14):
    if not dates:
        return ""
    latest = dates[0]
    items = []
    for d in dates[:max_tabs]:
        label = "Hoy" if d == latest else d
        if in_historico:
            href = "../index.html" if d == latest else f"{d}.html"
        else:
            href = "index.html" if d == latest else f"historico/{d}.html"
        cls = ' class="active"' if d == current else ""
        items.append(f'<a href="{href}"{cls}>{label}</a>')
    return '<nav class="tabs">' + "".join(items) + "</nav>"


WEB_CSS = """
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;background:#0f172a;color:#e2e8f0;
  margin:0;padding:24px}
.wrap{max-width:1100px;margin:0 auto}
h1{font-size:1.7em;font-weight:700;margin:0 0 4px;color:#f1f5f9}
.subtitle{color:#94a3b8;margin:0 0 18px;font-size:.95em}
.tabs{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:20px}
.tabs a{background:#1e293b;border:1px solid #334155;border-radius:16px;padding:5px 12px;
  font-size:.85em;text-decoration:none;color:#cbd5e1}
.tabs a.active{background:#2563eb;border-color:#2563eb;color:#fff}
.info-box{background:#1e293b;border:1px solid #334155;border-radius:10px;
  padding:14px 18px;margin-bottom:28px;font-size:.88em;color:#cbd5e1;line-height:1.55}
.info-box strong{color:#f1f5f9}
h2.section{font-size:1.15em;color:#f1f5f9;margin:30px 0 14px;padding-bottom:6px;
  border-bottom:1px solid #334155}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:16px}
.card{background:#1e293b;border-radius:12px;padding:18px;border:1px solid #334155}
.card-header{display:flex;justify-content:space-between;align-items:baseline;
  margin-bottom:10px;flex-wrap:wrap;gap:6px}
.dest{font-size:1.05em;font-weight:700;color:#f1f5f9}
.dest small{color:#94a3b8;font-weight:500;margin-left:4px}
.date-badge{background:#334155;color:#cbd5e1;padding:3px 10px;border-radius:12px;
  font-size:.78em;white-space:nowrap}
.dur-row{padding:10px 0;border-top:1px solid #334155}
.dur-row:first-child{border-top:none;padding-top:0}
.dur-main{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:6px}
.dur-label{font-weight:600;color:#e2e8f0}
.dur-return{color:#94a3b8;font-size:.85em}
.links a{display:inline-block;background:#0f172a;border:1px solid #334155;color:#93c5fd;
  text-decoration:none;font-size:.8em;padding:4px 10px;border-radius:8px;margin:2px 4px 0 0}
.links a:hover{background:#334155}
footer{text-align:center;color:#475569;margin-top:36px;font-size:.82em}
"""


def render_card(row):
    dur_rows = "".join(f"""
      <div class="dur-row">
        <div class="dur-main">
          <span class="dur-label">{leg['duration']} días</span>
          <span class="dur-return">regreso {fmt_es(leg['ret_date'])}</span>
        </div>
        <div class="links">
          <a href="{leg['google_flights']}" target="_blank" rel="noopener">Google Flights</a>
          <a href="{leg['skyscanner']}" target="_blank" rel="noopener">Skyscanner</a>
          <a href="{leg['kayak']}" target="_blank" rel="noopener">Kayak</a>
        </div>
      </div>""" for leg in row["legs"])
    return f"""
    <div class="card">
      <div class="card-header">
        <span class="dest">{row['dest_city']}<small>{row['dest_code']}</small></span>
        <span class="date-badge">Sale {fmt_es(row['dep_date'])}</span>
      </div>
      {dur_rows}
    </div>"""


def render_web_page(rows, tabs_html, generated_at):
    priority_cards = "".join(render_card(r) for r in rows if r["priority"] == 1)
    secondary_cards = "".join(render_card(r) for r in rows if r["priority"] == 2)
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Enlaces GDL → Japón · {generated_at}</title>
<style>{WEB_CSS}</style></head><body>
<div class="wrap">
<h1>✈️ GDL → Japón</h1>
<p class="subtitle">Enlaces de búsqueda para viajes de 13-15 días · noviembre-diciembre 2026</p>
{tabs_html}
<div class="info-box"><strong>¿Por qué no hay precios aquí?</strong> No existe una fuente de
precios en vivo gratuita para GDL como origen (Amadeus Self-Service cerró su alta el 17 de
julio de 2026; la Data API de Travelpayouts es gratis pero solo tiene caché de rutas con
historial de búsqueda real, y GDL no lo tiene). Da clic en cualquier enlace para ver tarifas
y disponibilidad reales al momento.</div>
<h2 class="section">Tokio (prioridad)</h2>
<div class="grid">{priority_cards}</div>
<h2 class="section">Otros destinos en Japón</h2>
<div class="grid">{secondary_cards}</div>
</div>
<footer>Generado automáticamente · japan-fare-monitor</footer>
</body></html>"""


# ── Correo ───────────────────────────────────────────────────────────────────
def send_email(subject, report_md):
    host = os.environ.get("SMTP_HOST")
    if not host:
        log("SMTP_HOST no configurado; se omite el envío de correo (solo se genera el reporte).")
        return

    to_addr = cfg["email"]["to"]
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = os.environ.get("SMTP_USER", "")
    msg["To"] = to_addr
    msg.attach(MIMEText(report_md, "plain", "utf-8"))
    msg.attach(MIMEText(markdown_to_html_body(report_md), "html", "utf-8"))

    try:
        port = int(os.environ.get("SMTP_PORT", "587").strip())
    except ValueError:
        log("SMTP_PORT no es un número válido; usando 587 por defecto.")
        port = 587

    try:
        with smtplib.SMTP(host, port, timeout=30) as server:
            server.starttls()
            server.login(os.environ["SMTP_USER"], os.environ["SMTP_PASSWORD"])
            server.sendmail(msg["From"], [to_addr], msg.as_string())
        log(f"Correo enviado a {to_addr}")
    except Exception as e:
        log(f"Error enviando correo: {e}")


# ── Main ─────────────────────────────────────────────────────────────────────
def run():
    log("japan-fare-monitor (modo enlaces) iniciando...")
    rows = build_rows()
    report_md = build_report(rows)

    fecha = date.today().isoformat()
    out_dir = os.path.join(_HERE, "..", "public", "japan-fares")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "historico"), exist_ok=True)

    dates = list_historico_dates(out_dir, fecha)
    html_index = render_web_page(rows, render_tabs(dates, fecha, in_historico=False), fecha)
    html_hist = render_web_page(rows, render_tabs(dates, fecha, in_historico=True), fecha)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_index)
    with open(os.path.join(out_dir, "historico", f"{fecha}.html"), "w", encoding="utf-8") as f:
        f.write(html_hist)

    subject = cfg["email"]["subject_template"].format(fecha=fecha)
    send_email(subject, report_md)

    log("Ejecución completa.")


if __name__ == "__main__":
    run()
