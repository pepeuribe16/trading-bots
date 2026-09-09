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


# ── Reporte ──────────────────────────────────────────────────────────────────
def render_row(row):
    legs_md = "\n".join(
        f"  - **{leg['duration']} días** (regreso {leg['ret_date'].isoformat()}): "
        f"[Google Flights]({leg['google_flights']}) · [Skyscanner]({leg['skyscanner']}) · [Kayak]({leg['kayak']})"
        for leg in row["legs"]
    )
    return f"- **{row['dest_city']} ({row['dest_code']})** — salida {row['dep_date'].isoformat()}\n{legs_md}"


def build_report():
    fecha = date.today().isoformat()
    rows = build_rows()
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


def render_full_html(report_md, generated_at):
    body = markdown_to_html_body(report_md)
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Enlaces GDL → Japón · {generated_at}</title>
<style>
body{{background:#0f172a;color:#e2e8f0;padding:24px}}
h1,h2,h3{{color:#f1f5f9}}
a{{color:#60a5fa}}
hr{{border-color:#334155;margin:20px 0}}
li{{margin:4px 0}}
</style></head><body>{body}
<footer style="text-align:center;color:#475569;margin-top:32px;font-size:.82em">
Generado automáticamente · japan-fare-monitor</footer>
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

    port = int(os.environ.get("SMTP_PORT", "587"))
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
    report_md = build_report()

    fecha = date.today().isoformat()
    out_dir = os.path.join(_HERE, "..", "public", "japan-fares")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "historico"), exist_ok=True)
    html = render_full_html(report_md, fecha)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(out_dir, "historico", f"{fecha}.html"), "w", encoding="utf-8") as f:
        f.write(html)

    subject = cfg["email"]["subject_template"].format(fecha=fecha)
    send_email(subject, report_md)

    log("Ejecución completa.")


if __name__ == "__main__":
    run()
