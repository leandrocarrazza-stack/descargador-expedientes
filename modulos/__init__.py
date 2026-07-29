"""Módulos del descargador de expedientes.

Los re-exports son perezosos (PEP 562): `from modulos import ClienteSelenium`
sigue funcionando igual, pero importar un subpaquete que no tiene nada que ver
—por ejemplo `modulos.jurisprudencia.stjer`— ya no arrastra Selenium ni el
resto del pipeline de Mesa Virtual.

Hace falta porque el CLI de STJER (`python -m scripts.stjer`) tiene que correr
sin las dependencias de la app web instaladas.
"""

_PEREZOSOS = {
    "ClienteSelenium": ".login",
    "crear_cliente_sesion": ".login",
    "BuscadorExpedientes": ".navegacion",
    "crear_buscador": ".navegacion",
    "DescargadorArchivos": ".descarga",
    "crear_descargador": ".descarga",
    "ConversorRTF": ".conversion",
    "crear_conversor": ".conversion",
    "UnificadorPDF": ".unificacion",
    "crear_unificador": ".unificacion",
}

__all__ = list(_PEREZOSOS)


def __getattr__(nombre):
    """Importa el submodulo recien cuando se pide el simbolo."""
    modulo = _PEREZOSOS.get(nombre)
    if modulo is None:
        raise AttributeError(f"module {__name__!r} has no attribute {nombre!r}")

    from importlib import import_module

    valor = getattr(import_module(modulo, __name__), nombre)
    globals()[nombre] = valor  # cachea para no repetir el import
    return valor


def __dir__():
    return sorted(__all__)
