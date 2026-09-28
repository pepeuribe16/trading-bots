"""
site_nav
Garantiza que cada página de market-dashboard-gga.web.app tenga los 4 botones
de la barra superior: Caídas, Auto BOT, Historial y Japan Fares.

- Si la página no tiene barra, inserta la barra completa del sitio.
- Si le faltan botones, los agrega copiando el estilo de un botón inactivo de
  la misma página, en el orden canónico.
"""
import re

LINKS = [
    ("/", "📉 Caídas"),
    ("/portfolio", "🤖 Auto BOT"),
    ("/historico", "📅 Historial"),
    ("/japan-fares", "✈️ Japan Fares"),
]

_BTN = ("text-decoration:none;padding:7px 16px;border-radius:7px;font-family:'Syne',sans-serif;"
        "font-size:12px;font-weight:700;white-space:nowrap;")
_OFF = _BTN + "background:rgba(255,255,255,0.07);color:#6B7A99;"
_ON = _BTN + "background:#4D8BFF;color:#fff;"


def _section(path):
    for href, _ in LINKS[1:]:
        if path == href or path.startswith(href + "/"):
            return href
    return None if path.startswith("/backtest") else "/"


def _anchor(html, href):
    suffix = rb'"' if href == "/" else rb'/?"'
    return re.search(rb'<a\s+href="' + re.escape(href.encode()) + suffix + rb'([^>]*)>.*?</a>',
                     html, re.S)


def _full_nav(section):
    buttons = "\n".join(
        f'    <a href="{href}" style="{_ON if href == section else _OFF}">{label}</a>'
        for href, label in LINKS
    )
    return f"""
<div style="position:fixed;top:0;left:0;right:0;z-index:1000;background:rgba(10,12,16,0.92);backdrop-filter:blur(12px);border-bottom:1px solid rgba(255,255,255,0.07);display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 24px;overflow-x:auto;">
  <span style="font-family:'DM Mono',monospace;font-size:11px;letter-spacing:2px;color:#6B7A99;text-transform:uppercase;white-space:nowrap;">Market Intelligence</span>
  <div style="display:flex;gap:8px;">
{buttons}
  </div>
</div>
<div style="height:56px;"></div>
""".encode()


def ensure_nav(html, path):
    """Devuelve el HTML con los 4 botones presentes (sin duplicarlos)."""
    section = _section(path)
    found = {href: _anchor(html, href) for href, _ in LINKS}
    if not any(found.values()):
        body = re.search(rb"<body[^>]*>", html, re.I)
        if not body:
            return html
        return html[:body.end()] + _full_nav(section) + html[body.end():]
    if all(found.values()):
        return html

    # Estilo a copiar: un botón presente que no sea el de la sección actual.
    template = next((m for h, m in found.items() if m and h != section),
                    next(m for m in found.values() if m))
    attrs = template.group(1)
    for i, (href, label) in enumerate(LINKS):
        if _anchor(html, href):
            continue
        new = b'<a href="' + href.encode() + b'"' + attrs + b'>' + label.encode() + b'</a>'
        prev = next((m for h, _ in reversed(LINKS[:i]) if (m := _anchor(html, h))), None)
        if prev:
            html = html[:prev.end()] + b"\n    " + new + html[prev.end():]
        else:
            nxt = next(m for h, _ in LINKS[i + 1:] if (m := _anchor(html, h)))
            html = html[:nxt.start()] + new + b"\n    " + html[nxt.start():]
    return html
