"""
GDL → Japan Fare Monitor
------------------------
Agente diario de travel hacking: busca las tarifas mas baratas GDL -> Japon
(NRT/HND prioritarios; KIX/NGO/FUK/CTS secundarios) para viajes de 13-15 dias
en noviembre-diciembre 2026, con conexiones permitidas en EE.UU.

Fuente de datos : Amadeus Self-Service "Flight Offers Search" (v2), autenticado
                   con AMADEUS_API_KEY / AMADEUS_API_SECRET (GitHub Secrets).
Estado           : price_history.json (mejor precio historico + serie diaria).
Salida           : public/japan-fares/index.html (Firebase) + correo HTML.

Para conservar la cuota mensual de un key de prueba de Amadeus, la busqueda
no barre TODAS las combinaciones de fecha/duracion/aeropuerto cada dia:
usa un muestreo configurable (config.json) sobre las fechas de salida, y
solo explora los destinos secundarios un dia fijo de la semana.
"""
import json
import os
import smtplib
import time
from datetime import date, datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests

_HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(_HERE, "config.json"), encoding="utf-8") as f:
    cfg = json.load(f)

HISTORY_FILE = os.path.join(_HERE, "price_history.json")

# El entorno "test" de Amadeus usa un dataset de muestra con cobertura limitada
# de rutas/fechas lejanas; para inventario y precios reales en producción, usa
# credenciales de producción y AMADEUS_ENV=production.
AMADEUS_BASE = (
    "https://api.amadeus.com" if os.environ.get("AMADEUS_ENV") == "production"
    else "https://test.api.amadeus.com"
)

ORIGIN = cfg["origin"]
DESTINATIONS = cfg["destinations"]
DURATIONS = cfg["durations_days"]
US_HUBS = cfg["us_connection_hubs"]
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


# ── Amadeus ──────────────────────────────────────────────────────────────────
def get_amadeus_token():
    resp = requests.post(
        f"{AMADEUS_BASE}/v1/security/oauth2/token",
        data={
            "grant_type": "client_credentials",
            "client_id": os.environ["AMADEUS_API_KEY"],
            "client_secret": os.environ["AMADEUS_API_SECRET"],
        },
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()["access_token"]


def _parse_iso8601_duration(dur):
    """'PT18H30M' -> 1110 (minutos). Robusto a piezas faltantes."""
    if not dur or not dur.startswith("PT"):
        return 0
    body = dur[2:]
    hours = minutes = 0
    num = ""
    for ch in body:
        if ch.isdigit():
            num += ch
        elif ch == "H":
            hours = int(num or 0)
            num = ""
        elif ch == "M":
            minutes = int(num or 0)
            num = ""
    return hours * 60 + minutes


def _fmt_minutes(mins):
    h, m = divmod(mins, 60)
    return f"{h}h {m}m" if m else f"{h}h"


def _parse_itinerary(itin):
    segs = itin["segments"]
    stops = [s["arrival"]["iataCode"] for s in segs[:-1]]
    carriers = sorted({s["carrierCode"] for s in segs})
    connection_minutes = []
    for i in range(len(segs) - 1):
        arr = datetime.fromisoformat(segs[i]["arrival"]["at"])
        dep = datetime.fromisoformat(segs[i + 1]["departure"]["at"])
        connection_minutes.append(int((dep - arr).total_seconds() // 60))
    return {
        "stops": stops,
        "carriers": carriers,
        "duration_min": _parse_iso8601_duration(itin.get("duration", "")),
        "connection_minutes": connection_minutes,
        "departure_at": segs[0]["departure"]["at"],
        "arrival_at": segs[-1]["arrival"]["at"],
        "departure_airport": segs[0]["departure"]["iataCode"],
        "arrival_airport": segs[-1]["arrival"]["iataCode"],
    }


def search_offers(token, origin, destination, dep_date, ret_date=None, max_results=3):
    params = {
        "originLocationCode": origin,
        "destinationLocationCode": destination,
        "departureDate": dep_date.isoformat(),
        "adults": 1,
        "currencyCode": "USD",
        "max": max_results,
        "nonStop": "false",
    }
    if ret_date:
        params["returnDate"] = ret_date.isoformat()

    resp = requests.get(
        f"{AMADEUS_BASE}/v2/shopping/flight-offers",
        headers={"Authorization": f"Bearer {token}"},
        params=params,
        timeout=25,
    )
    if resp.status_code == 429:
        time.sleep(2)
        resp = requests.get(
            f"{AMADEUS_BASE}/v2/shopping/flight-offers",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
            timeout=25,
        )
    if resp.status_code != 200:
        return []

    data = resp.json().get("data", [])
    offers = []
    for off in data:
        try:
            price = float(off["price"]["total"])
            itins = off["itineraries"]
            offers.append({
                "price_usd": price,
                "outbound": _parse_itinerary(itins[0]),
                "inbound": _parse_itinerary(itins[1]) if len(itins) > 1 else None,
            })
        except (KeyError, IndexError, ValueError):
            continue
    offers.sort(key=lambda o: o["price_usd"])
    return offers


# ── Muestreo de fechas ───────────────────────────────────────────────────────
def _month_dates(year, month):
    d = date(year, month, 1)
    out = []
    while d.month == month:
        out.append(d)
        d += timedelta(days=1)
    return out


def sample_departure_dates(interval_days):
    all_days = []
    for w in cfg["travel_windows"]:
        all_days.extend(_month_dates(w["year"], w["month"]))
    return all_days[::interval_days]


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


# ── Recoleccion de ofertas ──────────────────────────────────────────────────
def collect_candidates(token):
    today_weekday = date.today().weekday()
    candidates = []

    for dest in DESTINATIONS:
        is_primary = dest["priority"] == 1
        if not is_primary and today_weekday != cfg["secondary_scan_weekday"]:
            continue
        interval = (
            cfg["primary_departure_sample_interval_days"]
            if is_primary
            else cfg["secondary_departure_sample_interval_days"]
        )
        dep_dates = sample_departure_dates(interval)

        for dep_date in dep_dates:
            for duration in DURATIONS:
                ret_date = dep_date + timedelta(days=duration)
                offers = search_offers(
                    token, ORIGIN, dest["code"], dep_date, ret_date,
                    max_results=cfg["max_offers_per_search"],
                )
                time.sleep(0.15)
                for off in offers:
                    candidates.append({
                        "dest_code": dest["code"],
                        "dest_city": dest["city"],
                        "dep_date": dep_date,
                        "ret_date": ret_date,
                        "duration": duration,
                        "price_usd": off["price_usd"],
                        "outbound": off["outbound"],
                        "inbound": off["inbound"],
                        "split_ticket": False,
                    })
    return candidates


def check_split_tickets(token, candidates):
    """Para las mejores candidatas, compara contra dos boletos one-way separados."""
    top = sorted(candidates, key=lambda c: c["price_usd"])[: cfg["split_ticket_check_top_n"]]
    min_savings = cfg["split_ticket_min_savings_pct"] / 100

    for c in top:
        out_offers = search_offers(token, ORIGIN, c["dest_code"], c["dep_date"], max_results=1)
        time.sleep(0.15)
        in_offers = search_offers(token, c["dest_code"], ORIGIN, c["ret_date"], max_results=1)
        time.sleep(0.15)
        if not out_offers or not in_offers:
            continue
        split_total = out_offers[0]["price_usd"] + in_offers[0]["price_usd"]
        if split_total < c["price_usd"] * (1 - min_savings):
            c["price_usd"] = split_total
            c["outbound"] = out_offers[0]["outbound"]
            c["inbound"] = in_offers[0]["outbound"]
            c["split_ticket"] = True
    return candidates


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
    total_stops = len(c["outbound"]["stops"]) + (len(c["inbound"]["stops"]) if c["inbound"] else 0)
    if total_stops <= 2:
        score += 1
    if not c["split_ticket"]:
        score += 1
    prev_best = history.get("best_price_usd")
    if prev_best and c["price_usd"] <= prev_best:
        score += 1
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
def _route_text(itin, hubs):
    stops = itin["stops"]
    if not stops:
        return "Directo"
    labeled = [f"{s} (EE.UU.)" if s in hubs else s for s in stops]
    return " → ".join(labeled)


def _advantages_disadvantages(c):
    adv, disadv = [], []
    total_stops = len(c["outbound"]["stops"]) + (len(c["inbound"]["stops"]) if c["inbound"] else 0)
    if total_stops == 0:
        adv.append("Vuelo directo")
    elif total_stops <= 2:
        adv.append("Pocas escalas")
    else:
        disadv.append("Múltiples escalas, viaje más largo")
    if c["price_usd"] < cfg["alert_thresholds"]["max_price_usd_destacada"]:
        adv.append("Precio por debajo del umbral de oferta destacada")
    if c["split_ticket"]:
        adv.append("Ahorro significativo combinando boletos separados")
        disadv.append("Boletos separados: sin protección de conexión, hay que re-facturar equipaje")
    if any(m < 90 for m in c["outbound"]["connection_minutes"]):
        disadv.append("Conexión ajustada en el tramo de ida (<90 min)")
    if not adv:
        adv.append("Buena relación precio-duración")
    if not disadv:
        disadv.append("Sin desventajas relevantes detectadas")
    return adv, disadv


def render_option(rank, c, history, mxn_rate):
    price_mxn = c["price_usd"] * mxn_rate
    stars = star_rating(c, history)
    adv, disadv = _advantages_disadvantages(c)
    out, ret = c["outbound"], c["inbound"]
    airlines = sorted(set(out["carriers"] + (ret["carriers"] if ret else [])))
    total_travel_min = out["duration_min"] + (ret["duration_min"] if ret else 0)
    conn_out = ", ".join(_fmt_minutes(m) for m in out["connection_minutes"]) or "N/A"
    gf = google_flights_link(ORIGIN, c["dest_code"], c["dep_date"], c["ret_date"])
    sk = skyscanner_link(ORIGIN, c["dest_code"], c["dep_date"], c["ret_date"])
    ky = kayak_link(ORIGIN, c["dest_code"], c["dep_date"], c["ret_date"])
    badge = " 🔥 OFERTA DESTACADA" if c.get("destacada", False) else ""

    return f"""
### Ranking #{rank}{badge}

**Precio total:** USD ${c['price_usd']:,.0f} · MXN ${price_mxn:,.0f}

**Fechas:** Salida {c['dep_date'].isoformat()} · Regreso {c['ret_date'].isoformat()} · Duración {c['duration']} días

**Ruta:** {', '.join(airlines)}{' (boletos separados)' if c['split_ticket'] else ''}
- Escalas ida: {_route_text(out, US_HUBS)}
- Escalas regreso: {_route_text(ret, US_HUBS) if ret else 'N/A'}
- Tiempo de conexión (ida): {conn_out}
- Tiempo total de viaje: {_fmt_minutes(total_travel_min)}

**Aeropuertos:** Salida {ORIGIN} ({out['departure_airport']}) · Llegada {c['dest_city']} ({c['dest_code']})

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
            "histórico, del umbral de USD 900, o con una caída >10% vs. ayer). Este tipo de "
            "precios en la ruta GDL→Japón para nov-dic no suele mantenerse más de 24-48h."
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
    token = get_amadeus_token()

    log("Recolectando ofertas (Amadeus Flight Offers Search)...")
    candidates = collect_candidates(token)
    log(f"{len(candidates)} ofertas candidatas encontradas")

    if not candidates:
        log("Sin resultados en esta ejecución (posible falta de disponibilidad o cuota agotada).")
        return

    candidates = check_split_tickets(token, candidates)
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
