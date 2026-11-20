"""Detección de "el navegador está en la pantalla de login" a partir de la URL."""

from urllib.parse import urlsplit


def url_es_login(url: str) -> bool:
    """
    True si la URL es la pantalla de login de Keycloak (host ol-sso...) o una
    ruta /login. Mira SOLO el host y el path, nunca la query ni el fragmento.

    Por qué: al volver del SSO, Mesa Virtual queda un momento en una URL como
    https://mesavirtual.../expedientes/<id>#state=...&iss=https://ol-sso.../...
    El texto "ol-sso" aparece en el fragmento (parámetro iss), pero el
    navegador YA está en Mesa Virtual con sesión válida. Buscar "ol-sso" como
    subcadena de toda la URL daba falso positivo: abortaba la descarga y
    borraba la sesión del usuario, obligándolo a reconectarse en cada intento.
    """
    try:
        partes = urlsplit(url or "")
    except ValueError:
        return False
    return (partes.hostname or "").startswith("ol-sso") or "/login" in partes.path
