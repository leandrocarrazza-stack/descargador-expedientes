"""
medir_sesion_mv.py
==================
Mide cuánto dura una sesión de Mesa Virtual (Keycloak) sin guardar ni
escribir credenciales. Solo usa las cookies que quedan después de que
VOS iniciás sesión a mano.

Uso:
    python scripts/medir_sesion_mv.py login
        Abre Chrome; vos entrás a Mesa Virtual (con 2FA). Guarda las cookies
        en ~/.mesa_virtual_medicion.pkl (archivo aparte: no toca la sesión
        que usa el MCP) y anota en el log cuándo vencen.

    python scripts/medir_sesion_mv.py chequear
        Pregunta una vez a Keycloak si la sesión sigue viva (sin abrir Chrome)
        y anota el resultado en el log.

    python scripts/medir_sesion_mv.py vigilar HH:MM HH:MM ...
        Se queda corriendo y hace un "chequear" en cada horario indicado
        (hoy o, si ya pasó, mañana). Se detiene sola cuando la sesión muere.

El log queda en ~/.mesa_virtual_medicion.log. Nunca se imprimen valores
de cookies, solo nombres y fechas.

Ojo: cada chequeo cuenta como "actividad" para Keycloak, así que reinicia
el contador de inactividad. Por eso los horarios de "vigilar" conviene
ponerlos iguales a los de las revisiones reales (7:40, 12:00, 14:10).
"""

import sys
import time
import pickle
import datetime as dt
from pathlib import Path
from urllib.parse import urlparse, parse_qs, urlencode

import requests

URL_MV = "https://mesavirtual.jusentrerios.gov.ar/"
DOMINIO_SSO = "ol-sso.jusentrerios.gov.ar"
ARCHIVO = Path.home() / ".mesa_virtual_medicion.pkl"
LOG = Path.home() / ".mesa_virtual_medicion.log"


def anotar(texto):
    """Escribe una línea con fecha y hora en pantalla y en el log."""
    linea = f"{dt.datetime.now():%Y-%m-%d %H:%M:%S}  {texto}"
    print(linea)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(linea + "\n")


def login():
    """Abre Chrome visible, espera tu login y guarda cookies + URL de Keycloak."""
    from selenium import webdriver

    driver = webdriver.Chrome()
    driver.get(URL_MV)
    print("Iniciá sesión en la ventana de Chrome (tenés 10 minutos)...")

    url_auth = None  # la URL de Keycloak que usa Mesa Virtual (trae realm y client_id)
    vistas = set()   # páginas recorridas (sin parámetros), para diagnosticar si falla
    for _ in range(600):
        url = driver.current_url
        u = urlparse(url)
        if (u.netloc, u.path) not in vistas:
            vistas.add((u.netloc, u.path))
            anotar(f"   página: {u.netloc}{u.path}")
        # Guardamos la URL de la página de login de Keycloak
        if DOMINIO_SSO in url and "/protocol/openid-connect/auth" in url:
            url_auth = url
        # Login terminado: estamos en Mesa Virtual, no en Keycloak
        if "mesavirtual.jusentrerios.gov.ar" in url and DOMINIO_SSO not in url and url_auth:
            break
        time.sleep(1)
    else:
        driver.quit()
        anotar("LOGIN: no se completó en 10 minutos")
        return

    time.sleep(3)
    # CDP trae las cookies de TODOS los dominios (incluida la de Keycloak)
    cookies = driver.execute_cdp_cmd("Network.getAllCookies", {})["cookies"]
    driver.quit()

    with open(ARCHIVO, "wb") as f:
        pickle.dump({"cookies": cookies, "url_auth": url_auth}, f)

    anotar("LOGIN OK. Cookies de Keycloak:")
    for c in cookies:
        if DOMINIO_SSO in c.get("domain", ""):
            vence = c.get("expires", -1)
            cuando = (f"vence {dt.datetime.fromtimestamp(vence):%Y-%m-%d %H:%M}"
                      if vence and vence > 0 else "de sesión (sin fecha)")
            anotar(f"   {c['name']}: {cuando}")


def chequear():
    """Pregunta a Keycloak con prompt=none si la sesión sigue viva.

    Keycloak responde con una redirección: si la sesión vive trae 'code=',
    si murió trae 'error=login_required'. No se abre Chrome ni se loguea nada.
    Devuelve True (viva), False (muerta) o None (no se pudo saber).
    """
    with open(ARCHIVO, "rb") as f:
        datos = pickle.load(f)

    s = requests.Session()
    for c in datos["cookies"]:
        if DOMINIO_SSO in c.get("domain", ""):
            s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))

    # Reusamos la misma URL de login que usó Mesa Virtual, agregando prompt=none
    u = urlparse(datos["url_auth"])
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    q["prompt"] = "none"
    url = f"{u.scheme}://{u.netloc}{u.path}?{urlencode(q)}"

    try:
        r = s.get(url, allow_redirects=False, timeout=30)
    except Exception as e:
        anotar(f"CHEQUEO: error de red ({type(e).__name__})")
        return None

    destino = r.headers.get("Location", "")
    if "code=" in destino:
        anotar("CHEQUEO: sesión VIVA")
        return True
    if "login_required" in destino or "error=" in destino:
        anotar("CHEQUEO: sesión MUERTA (Keycloak pide login)")
        return False
    anotar(f"CHEQUEO: respuesta inesperada (HTTP {r.status_code})")
    return None


def vigilar(horarios):
    """Hace un chequeo en cada horario HH:MM, en orden, y para si la sesión muere."""
    ahora = dt.datetime.now()
    momentos = []
    for h in horarios:
        hh, mm = map(int, h.split(":"))
        m = ahora.replace(hour=hh, minute=mm, second=0, microsecond=0)
        # Si el horario ya pasó hoy, o es anterior al anterior, va al día siguiente
        while m <= ahora or (momentos and m <= momentos[-1]):
            m += dt.timedelta(days=1)
        momentos.append(m)

    anotar("VIGILAR: chequeos a las " + ", ".join(f"{m:%d/%m %H:%M}" for m in momentos))
    for m in momentos:
        while dt.datetime.now() < m:
            time.sleep(30)
        if chequear() is False:
            anotar("VIGILAR: fin (sesión muerta)")
            return
    anotar("VIGILAR: fin (todos los chequeos hechos)")


if __name__ == "__main__":
    accion = sys.argv[1] if len(sys.argv) > 1 else ""
    if accion == "login":
        login()
    elif accion == "chequear":
        chequear()
    elif accion == "vigilar":
        vigilar(sys.argv[2:])
    else:
        print(__doc__)
