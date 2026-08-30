"""
STJER - Cosecha y busqueda local de jurisprudencia de Entre Rios
================================================================

Reemplaza el enfoque de "un modelo maneja el navegador en cada busqueda" por
"un script cosecha una vez, y despues se consulta un indice local".

Diseño: este paquete NO importa Flask ni modulos.database. Tiene que poder
correr con `python -m scripts.stjer` a secas, sin levantar la app web.

Modulos
-------
ajustes         Constantes y rutas (con override por variables de entorno).
normalizacion   normalizar_texto / tokenizar / STOP_WORDS. Una sola definicion.
parser          HTML -> dicts. Testeable sin red, contra fixtures de la Fase 0.
corpus          Esquema SQLite + FTS5 + upserts idempotentes + cola de tareas.
cliente         Protocolo ClienteSTJER y sus implementaciones (HTTP / navegador).
sesion          Playwright: captcha a resolucion nativa, puente de cookies.
tesauro_stjer   Cosecha del tesauro real + mapeo consulta -> voces.
cosecha         Cola durable, backoff, disyuntor y reconciliacion.
busqueda        BM25 sobre FTS5 + expansion por voces + filtros estructurados.
pdf             Descarga de PDFs (sin sesion ni captcha), perezosa o por lotes.
"""

__all__ = [
    "ajustes",
    "normalizacion",
    "parser",
    "corpus",
    "cliente",
    "sesion",
    "tesauro_stjer",
    "cosecha",
    "busqueda",
    "pdf",
]
