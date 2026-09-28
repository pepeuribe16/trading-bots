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

SITE = "https://market-dashboard-gga.web.app"
PUBLIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "public")


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
    print(f"  ✓ {path} ({len(data)} bytes)")


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
            if is_real(data):
                save(path, data)


if __name__ == "__main__":
    main()
