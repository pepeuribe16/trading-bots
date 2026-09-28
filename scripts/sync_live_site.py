"""
sync_live_site
Descarga a public/ los archivos que publican las rutinas de Claude directamente
en Firebase (sin pasar por el repo), para que un `firebase deploy` desde un
GitHub Action no los borre ni los reemplace con copias viejas del repo.

Archivos que se preservan desde el sitio en vivo:
  /index.html                  → dashboard detallado (rutina market-losers-dashboard)
  /historico/index.html        → índice del histórico (misma rutina)
  /historico/YYYY-MM-DD.html   → reportes diarios (misma rutina)

Si un archivo no existe en vivo, Firebase responde con /index.html por la regla
de rewrite "**"; esos casos se detectan y se deja la copia del repo.
"""
import os
import re
import sys
import time
import urllib.request

from site_nav import ensure_nav

SITE = "https://market-dashboard-gga.web.app"
PUBLIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "public")
SYNCED = set()  # páginas traídas del sitio en vivo


def fetch(path):
    url = f"{SITE}{path}?t={int(time.time())}"
    req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status != 200:
                return None
            return resp.read()
    except Exception as e:
        print(f"  ! {path}: {e}")
        return None


def save(path, data):
    dest = os.path.join(PUBLIC, path.lstrip("/"))
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(data)
    SYNCED.add(path)
    print(f"  ✓ {path} ({len(data)} bytes)")


def fix_nav_everywhere():
    """Agrega los botones faltantes en todas las páginas de public/.

    Devuelve las páginas traídas del sitio en vivo a las que les faltaba algo:
    si hay alguna, el sitio publicado necesita un deploy.
    """
    live_missing = []
    for folder, _, files in os.walk(PUBLIC):
        for name in files:
            if not name.endswith(".html"):
                continue
            full = os.path.join(folder, name)
            path = "/" + os.path.relpath(full, PUBLIC).replace(os.sep, "/")
            with open(full, "rb") as f:
                html = f.read()
            fixed = ensure_nav(html, path)
            if fixed != html:
                with open(full, "wb") as f:
                    f.write(fixed)
                print(f"  + botones agregados en {path}")
                if path in SYNCED:
                    live_missing.append(path)
    return live_missing


def main():
    root = fetch("/index.html")
    if not root or b"<html" not in root.lower():
        # Sin el index en vivo no podemos distinguir archivos reales de rewrites;
        # mejor abortar el deploy que publicar una versión incompleta.
        print("No se pudo leer /index.html en vivo — abortando deploy.")
        sys.exit(1)
    save("/index.html", root)

    def is_real(data):
        return data and data != root

    hist_index = fetch("/historico/index.html")
    if is_real(hist_index):
        save("/historico/index.html", hist_index)
        dates = sorted(set(re.findall(rb"/historico/(\d{4}-\d{2}-\d{2})\.html", hist_index)))
        for d in dates:
            path = f"/historico/{d.decode()}.html"
            data = fetch(path)
            # La rutina publica el reporte más reciente también como /index.html,
            # así que ese sí puede ser idéntico al index sin ser un rewrite.
            if is_real(data) or (data and d == dates[-1]):
                save(path, data)
    else:
        # Sin índice en vivo, /historico caería en el rewrite "**" y mostraría la
        # página principal; generamos uno con los reportes que sí hay en public/.
        build_historico_index()

    live_missing = fix_nav_everywhere()
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a") as f:
            f.write(f"needs_deploy={'true' if live_missing else 'false'}\n")


def build_historico_index():
    folder = os.path.join(PUBLIC, "historico")
    dates = sorted(
        (f[:-5] for f in os.listdir(folder) if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.html", f)),
        reverse=True,
    ) if os.path.isdir(folder) else []
    items = "".join(
        f'<a href="/historico/{d}.html" style="display:flex;justify-content:space-between;'
        f'padding:16px 24px;text-decoration:none;border-bottom:1px solid #21262d;">'
        f'<span style="color:#58a6ff;font-weight:600;">{d}</span>'
        f'<span style="color:#3fb950;font-size:12px;">Ver reporte ›</span></a>'
        for d in dates
    ) or '<p style="padding:32px;text-align:center;color:#8b949e">No hay reportes disponibles.</p>'
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Histórico — Market Intelligence</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Mono:wght@400;500&family=Syne:wght@700&display=swap" rel="stylesheet">
</head>
<body style="margin:0;background:#0A0C10;color:#c9d1d9;font-family:'DM Mono',monospace;">
<div style="position:fixed;top:0;left:0;right:0;z-index:1000;background:rgba(10,12,16,0.92);border-bottom:1px solid rgba(255,255,255,0.07);display:flex;align-items:center;justify-content:space-between;padding:10px 24px;">
  <span style="font-size:11px;letter-spacing:2px;color:#6B7A99;text-transform:uppercase;">Market Intelligence</span>
  <div style="display:flex;gap:8px;">
    <a href="/" style="text-decoration:none;padding:7px 16px;border-radius:7px;font-family:'Syne',sans-serif;font-size:12px;font-weight:700;background:rgba(255,255,255,0.07);color:#6B7A99;">📉 Caídas</a>
    <a href="/portfolio" style="text-decoration:none;padding:7px 16px;border-radius:7px;font-family:'Syne',sans-serif;font-size:12px;font-weight:700;background:rgba(255,255,255,0.07);color:#6B7A99;">🤖 Auto BOT</a>
    <a href="/historico" style="text-decoration:none;padding:7px 16px;border-radius:7px;font-family:'Syne',sans-serif;font-size:12px;font-weight:700;background:#FFB800;color:#000;">📅 Historial</a>
    <a href="/japan-fares" style="text-decoration:none;padding:7px 16px;border-radius:7px;font-family:'Syne',sans-serif;font-size:12px;font-weight:700;background:rgba(255,255,255,0.07);color:#6B7A99;">✈️ Japan Fares</a>
  </div>
</div>
<div style="max-width:720px;margin:0 auto;padding:96px 16px 32px;">
  <h1 style="font-family:'Syne',sans-serif;font-size:26px;color:#f0f6fc;text-align:center;">📅 Histórico de Reportes</h1>
  <p style="text-align:center;color:#8b949e;font-size:12px;margin-bottom:32px;letter-spacing:1px;text-transform:uppercase;">{len(dates)} reportes disponibles</p>
  <div style="border:1px solid #30363d;border-radius:12px;overflow:hidden;background:#0d1117;">{items}</div>
</div>
</body>
</html>"""
    save("/historico/index.html", html.encode())


if __name__ == "__main__":
    main()
