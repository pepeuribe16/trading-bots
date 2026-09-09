"""
GDL → Japan Fare Monitor
------------------------
Agente diario de travel hacking: busca las tarifas mas baratas GDL -> Japon
(NRT/HND prioritarios; KIX/NGO/FUK/CTS secundarios) para viajes de 13-15 dias
en noviembre-diciembre 2026.

Fuente de datos : Travelpayouts Data API (v2/prices/month-matrix) — tarifas
                   cacheadas a partir de busquedas reales de usuarios de
                   Aviasales. Es gratis y de alta inmediata (sin tarjeta),
                   a diferencia de Amadeus Self-Service, que cerro su alta
                   self-service el 17 de julio de 2026.

                   Limitacion frente a una busqueda en vivo: esta API no
                   entrega aerolineas/horarios de conexion exactos, solo
                   precio, fechas y numero de escalas. Los enlaces directos
                   (Google Flights/Skyscanner/Kayak) sirven para ver el
                   itinerario completo antes de comprar.

Estado           : price_history.json (mejor precio historico + serie diaria).
Salida           : public/japan-fares/index.html (Firebase) + correo HTML.
"""
import json
import os
import smtplib
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(_HERE, "config.json"), encoding="utf-8") as f:
    cfg = json.load(f)

HISTORY_FILE = os.path.join(_HERE, "price_history.json")

TRAVELPAYOUTS_BASE = "https://api.travelpayouts.com"

ORIGIN = cfg["origin"]
DESTINATIONS = cfg["destinations"]
DURATIONS = set(cfg["durations_days"])
TOP_N = cfg["top_n"]

STAR_LABELS = {5: "⭐⭐⭐⭐⭐", 4: "⭐⭐⭐⭐", 3: "⭐⭐⭐", 2: "⭐⭐", 1: "⭐"}


def log(msg):
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}")


# ── Estado / historico ──────────────────────────────────────────────────────
def load_history():
    try:
        with open(HISTORY_FILE, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {"best_price_usd": None, "best_price_date": None, "daily_best": {}}


def save_history(hist):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(hist, f, indent=2, ensure_ascii=False)


# ── Travelpayouts ────────────────────────────────────────────────────────────
def get_month_matrix(origin, destination, first_of_month):
    resp = requests.get(
        f"{TRAVELPAYOUTS_BASE}/v2/prices/month-matrix",
        headers={"X-Access-Token": os.environ["TRAVELPAYOUTS_TOKEN"]},
        params={
            "currency": cfg["currency"],
            "origin": origin,
            "destination": destination,
            "month": first_of_month.isoformat(),
            "show_to_affiliates": str(cfg["show_to_affiliates"]).lower(),
        },
        timeout=25,
    )
    if resp.status_code != 200:
        log(f"Travelpayouts {origin}->{destination} {first_of_month}: HTTP {resp.status_code}")
        return []
    body = resp.json()
    if not body.get("success", False):
        log(f"Travelpayouts {origin}->{destination} {first_of_month}: {body.get('error')}")
        return []
    return body.get("data") or []


def collect_candidates():
    candidates = []
    for dest in DESTINATIONS:
        for w in cfg["travel_windows"]:
            entries = get_month_matrix(ORIGIN, dest["code"], date(w["year"], w["month"], 1))
            for e in entries:
                if e.get("actual") is False:
                    continue
                try:
                    dep = datetime.strptime(e["depart_date"], "%Y-%m-%d").date()
                    ret = datetime.strptime(e["return_date"], "%Y-%m-%d").date()
                    price = float(e["value"])
                except (KeyError, TypeError, ValueError):
                    continue
                duration = (ret - dep).days
                if duration not in DURATIONS:
                    continue
                candidates.append({
                    "dest_code": dest["code"],
                    "dest_city": dest["city"],
                    "dep_date": dep,
                    "ret_date": ret,
                    "duration": duration,
                    "price_usd": price,
                    "stops": e.get("number_of_changes"),
                    "found_at": e.get("found_at"),
                })
    return candidates


# ── FX ───────────────────────────────────────────────────────────────────────
def usd_to_mxn_rate():
    try:
        resp = requests.get("https://open.er-api.com/v6/latest/USD", timeout=10)
        resp.raise_for_status()
        rate = resp.json()["rates"]["MXN"]
        return float(rate)
    except Exception as e:
        log(f"No se pudo obtener tipo de cambio en vivo ({e}); usando 18.5 MXN/USD de respaldo")
        return 18.5


# ── Ranking, alertas, links ─────────────────────────────────────────────────
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


def star_rating(c, history):
    score = 0
    if c["price_usd"] < cfg["alert_thresholds"]["max_price_usd_destacada"]:
        score += 2
    if c["stops"] is not None and c["stops"] <= 1:
        score += 1
    prev_best = history.get("best_price_usd")
    if prev_best and c["price_usd"] <= prev_best:
        score += 2
    return min(5, max(1, score))


def is_oferta_destacada(price, history):
    th = cfg["alert_thresholds"]
    prev_best = history.get("best_price_usd")
    if prev_best is not None and price < prev_best:
        return True
    if price < th["max_price_usd_destacada"]:
        return True
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    y_price = history.get("daily_best", {}).get(yesterday)
    if y_price and price <= y_price * (1 - th["min_pct_drop_destacada"] / 100):
        return True
    return False


def rank_top(candidates, n):
    candidates = sorted(candidates, key=lambda c: c["price_usd"])
    seen_routes = set()
    top = []
    for c in candidates:
        key = (c["dest_code"], c["dep_date"], c["ret_date"])
        if key in seen_routes:
            continue
        seen_routes.add(key)
        top.append(c)
        if len(top) >= n:
            break
    return top


# ── Reporte ──────────────────────────────────────────────────────────────────
def _advantages_disadvantages(c):
    adv, disadv = [], []
    if c["stops"] == 0:
        adv.append("Vuelo directo (según dato cacheado)")
    elif c["stops"] is not None and c["stops"] <= 1:
        adv.append("Pocas escalas")
    elif c["stops"] is not None:
        disadv.append(f"{c['stops']} escalas")
    if c["price_usd"] < cfg["alert_thresholds"]["max_price_usd_destacada"]:
        adv.append("Precio por debajo del umbral de oferta destacada")
    disadv.append("Precio cacheado (no es cotización en vivo) — confirma en el enlace antes de comprar")
    if not adv:
        adv.append("Buena relación precio-duración")
    return adv, disadv


def render_option(rank, c, history, mxn_rate):
    price_mxn = c["price_usd"] * mxn_rate
    stars = star_rating(c, history)
    adv, disadv = _advantages_disadvantages(c)
    stops_txt = "Directo" if c["stops"] == 0 else f"{c['stops']} escala(s)" if c["stops"] is not None else "N/D"
    gf = google_flights_link(ORIGIN, c["dest_code"], c["dep_date"], c["ret_date"])
    sk = skyscanner_link(ORIGIN, c["dest_code"], c["dep_date"], c["ret_date"])
    ky = kayak_link(ORIGIN, c["dest_code"], c["dep_date"], c["ret_date"])
    badge = " 🔥 OFERTA DESTACADA" if c.get("destacada", False) else ""

    return f"""
### Ranking #{rank}{badge}

**Precio total:** USD ${c['price_usd']:,.0f} · MXN ${price_mxn:,.0f}

**Fechas:** Salida {c['dep_date'].isoformat()} · Regreso {c['ret_date'].isoformat()} · Duración {c['duration']} días

**Ruta:** {stops_txt} · aerolíneas y horarios exactos en los enlaces de abajo

**Aeropuertos:** Salida {ORIGIN} · Llegada {c['dest_city']} ({c['dest_code']})

**Ventajas:** {'; '.join(adv)}

**Desventajas:** {'; '.join(disadv)}

**Enlaces directos:** [Google Flights]({gf}) · [Skyscanner]({sk}) · [Kayak]({ky})

**Nivel de recomendación:** {STAR_LABELS[stars]}
"""


def render_history_section(history, today_best, mxn_rate):
    best_ever = history.get("best_price_usd")
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    y_price = history.get("daily_best", {}).get(yesterday)

    if y_price:
        pct = (today_best - y_price) / y_price * 100
        trend = "Bajando 📉" if pct < -2 else "Subiendo 📈" if pct > 2 else "Estable ➡️"
    else:
        pct, trend = None, "Sin datos suficientes"

    pct_txt = f"{pct:+.1f}%" if pct is not None else "N/A"
    best_ever_txt = f"${best_ever:,.0f} USD" if best_ever is not None else "N/A (primer registro)"

    return f"""
## Evolución de precios

- Mejor precio histórico registrado: {best_ever_txt}
- Mejor precio encontrado hoy: ${today_best:,.0f} USD (${today_best * mxn_rate:,.0f} MXN)
- Diferencia porcentual vs. ayer: {pct_txt}
- Tendencia: {trend}
"""


def recommendation(history, top, today_best):
    has_destacada = any(c.get("destacada", False) for c in top)
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    y_price = history.get("daily_best", {}).get(yesterday)
    trend_down = y_price is not None and today_best < y_price * 0.98
    trend_up = y_price is not None and today_best > y_price * 1.02

    if has_destacada:
        return (
            "**COMPRAR HOY.** Se detectó una oferta destacada (por debajo del mejor precio "
            "histórico, del umbral de USD 900, o con una caída >10% vs. ayer). Confirma el "
            "itinerario exacto en el enlace antes de pagar, ya que el dato es cacheado."
        )
    if trend_down:
        return (
            "**ESPERAR unos días más.** La tendencia de precios va a la baja; noviembre "
            "(fuera de la temporada alta de Año Nuevo) suele seguir bajando conforme se acerca "
            "la fecha, salvo que aparezca una oferta destacada antes."
        )
    if trend_up:
        return (
            "**CONSIDERAR COMPRAR PRONTO.** La tendencia es al alza, típico conforme se acercan "
            "las fechas de diciembre (Año Nuevo japonés) con mayor demanda. Configurar alerta "
            "especial en las rutas NRT/HND con salida en la primera mitad de noviembre, que "
            "históricamente son más baratas que diciembre."
        )
    return (
        "**CONFIGURAR ALERTA y esperar.** Precios estables; sin urgencia de compra. Se "
        "recomienda una alerta especial en NRT/HND para la ventana del 1-15 de noviembre 2026."
    )


def build_report(top, history, mxn_rate):
    today_best = min(c["price_usd"] for c in top) if top else None
    for c in top:
        c["destacada"] = is_oferta_destacada(c["price_usd"], history)

    fecha = date.today().isoformat()
    destacadas = [c for c in top if c["destacada"]]

    parts = [f"# Monitoreo Japón 2026 — GDL → Japón ({fecha})\n"]
    parts.append(
        "_Precios cacheados de búsquedas reales en Aviasales (Travelpayouts Data API). "
        "No es cotización en vivo: confirma itinerario y disponibilidad en los enlaces "
        "antes de comprar._\n"
    )
    if destacadas:
        parts.append("## 🔥 OFERTA DESTACADA DETECTADA\n")
        parts.append(render_option(1, destacadas[0], history, mxn_rate))
        parts.append("---\n## Top 5 opciones\n")
    else:
        parts.append("## Top 5 opciones\n")

    for i, c in enumerate(top, start=1):
        parts.append(render_option(i, c, history, mxn_rate))

    if top:
        parts.append(render_history_section(history, today_best, mxn_rate))
        parts.append("\n## Recomendación final\n")
        parts.append(recommendation(history, top, today_best))
    else:
        parts.append("\nNo se encontraron ofertas disponibles en esta ejecución.\n")

    return "\n".join(parts), today_best


def markdown_to_html_body(md_text):
    import html as _html
    esc = _html.escape(md_text)
    html_body = esc
    html_body = html_body.replace("\n### ", "\n<h3>").replace("\n## ", "\n<h2>").replace("\n# ", "\n<h1>")
    lines = html_body.split("\n")
    out = []
    for line in lines:
        if line.startswith("<h1>"):
            out.append(f"<h1>{line[4:]}</h1>")
        elif line.startswith("<h2>"):
            out.append(f"<h2>{line[4:]}</h2>")
        elif line.startswith("<h3>"):
            out.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("- "):
            out.append(f"<li>{line[2:]}</li>")
        elif line.strip() == "---":
            out.append("<hr>")
        elif line.strip() == "":
            out.append("<br>")
        else:
            out.append(f"<p>{line}</p>")
    body = "\n".join(out)
    # Enlaces markdown [texto](url) -> <a>
    import re
    body = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', body)
    body = body.replace("**", "")
    return (
        "<html><body style='font-family:system-ui,sans-serif;color:#1a1a1a;line-height:1.5;"
        "max-width:800px;margin:0 auto'>" + body + "</body></html>"
    )


def render_full_html(report_md, generated_at):
    body = markdown_to_html_body(report_md)
    return f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Monitoreo Japón 2026 · {generated_at}</title>
<style>
body{{background:#0f172a;color:#e2e8f0;padding:24px}}
h1,h2,h3{{color:#f1f5f9}}
a{{color:#60a5fa}}
hr{{border-color:#334155;margin:20px 0}}
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
    log("japan-fare-monitor iniciando...")

    log("Recolectando tarifas (Travelpayouts Data API)...")
    candidates = collect_candidates()
    log(f"{len(candidates)} ofertas candidatas encontradas (duración 13-15 días)")

    if not candidates:
        log("Sin resultados en esta ejecución (sin datos cacheados para esas fechas/duración).")
        return

    history = load_history()
    top = rank_top(candidates, TOP_N)
    mxn_rate = usd_to_mxn_rate()
    report_md, today_best = build_report(top, history, mxn_rate)

    fecha = date.today().isoformat()
    out_dir = os.path.join(_HERE, "..", "public", "japan-fares")
    os.makedirs(out_dir, exist_ok=True)
    os.makedirs(os.path.join(out_dir, "historico"), exist_ok=True)
    html = render_full_html(report_md, fecha)
    with open(os.path.join(out_dir, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)
    with open(os.path.join(out_dir, "historico", f"{fecha}.html"), "w", encoding="utf-8") as f:
        f.write(html)

    if today_best is not None:
        history["daily_best"][fecha] = today_best
        if history.get("best_price_usd") is None or today_best < history["best_price_usd"]:
            history["best_price_usd"] = today_best
            history["best_price_date"] = fecha
        save_history(history)

    subject = cfg["email"]["subject_template"].format(fecha=fecha)
    send_email(subject, report_md)

    log("Ejecución completa.")


if __name__ == "__main__":
    run()
