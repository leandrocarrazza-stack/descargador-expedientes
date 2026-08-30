"""
Ajustes del subsistema STJER
============================

Deliberadamente NO importa config.py: config.py depende de python-dotenv y de
Flask, y este paquete tiene que poder correr solo. Los nombres y los valores
por defecto coinciden con los que config.py expone para la app web.

Si hay un .env en la raiz del proyecto y python-dotenv esta instalado, se
carga; si no, se sigue con los defaults.
"""

import os
from pathlib import Path

# .../descargador-expedientes/modulos/jurisprudencia/stjer/ajustes.py -> raiz
PROJECT_DIR = Path(__file__).resolve().parents[3]

try:  # Opcional: si no esta dotenv, se usan las variables de entorno reales.
    from dotenv import load_dotenv

    load_dotenv(PROJECT_DIR / ".env")
except ImportError:
    pass


# ───────────────────────────────────────────────────────────────────────────
#  Rutas
# ───────────────────────────────────────────────────────────────────────────

JURISPRUDENCIA_DIR = PROJECT_DIR / "data" / "jurisprudencia"
PDFS_DIR = JURISPRUDENCIA_DIR / "pdfs"
DESCUBRIMIENTO_DIR = JURISPRUDENCIA_DIR / "descubrimiento"

CORPUS_PATH = Path(
    os.getenv("CORPUS_STJER_PATH", JURISPRUDENCIA_DIR / "corpus_stjer.sqlite")
)
TESAURO_STJER_PATH = Path(
    os.getenv("TESAURO_STJER_PATH", JURISPRUDENCIA_DIR / "tesauro_stjer.json")
)
ESTADO_PATH = Path(
    os.getenv("STJER_ESTADO_PATH", JURISPRUDENCIA_DIR / ".stjer_estado.json")
)


# ───────────────────────────────────────────────────────────────────────────
#  Sitio
# ───────────────────────────────────────────────────────────────────────────

BASE_URL = os.getenv("STJER_BASE_URL", "https://jur.jusentrerios.gov.ar/jur/")
URL_INICIO = BASE_URL + "?ai=jur||newpublica"
URL_APLICACION = BASE_URL + "aplicacion.php"
URL_DOSSIER = BASE_URL + "dossier/"

# El buscador solo publica fallos seleccionados desde 2004.
ANIO_INICIO_CORPUS = int(os.getenv("STJER_ANIO_INICIO", "2004"))


# ───────────────────────────────────────────────────────────────────────────
#  Cortesia con el servidor
#
#  ~15.900 requests repartidos en una noche son ~0,25 req/s: comparable a un
#  humano entusiasta. Una sola conexion, espera jitteada, techo duro por hora.
# ───────────────────────────────────────────────────────────────────────────

ESPERA_SEG = float(os.getenv("STJER_ESPERA_SEG", "1.5"))
JITTER_SEG = float(os.getenv("STJER_JITTER_SEG", "0.8"))
MAX_REQ_HORA = int(os.getenv("STJER_MAX_REQ_HORA", "1800"))
MAX_REINTENTOS = int(os.getenv("STJER_MAX_REINTENTOS", "4"))

# Cinco fallos seguidos = "nos bloquearon o murio la sesion". Seguir seria
# martillarlos al pedo: se aborta la corrida y se retoma despues (la cola es
# durable, no se pierde trabajo).
MAX_FALLOS_CONSECUTIVOS = int(os.getenv("STJER_MAX_FALLOS_CONSECUTIVOS", "5"))

TIMEOUT_SEG = float(os.getenv("STJER_TIMEOUT_SEG", "60"))

# Poner un mail de contacto real es lo correcto: si molestamos, que puedan
# avisar en vez de bloquear a ciegas.
UA = os.getenv(
    "STJER_UA",
    "descargador-expedientes/1.0 (cosecha de jurisprudencia publica; "
    "contacto: leofard@gmail.com)",
)

# UA de navegador para bajar PDFs: ese endpoint es estatico y publico, y
# algunos servidores rechazan UAs no-navegador en archivos estaticos.
UA_NAVEGADOR = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def asegurar_directorios() -> None:
    """Crea los directorios de datos si no existen."""
    for d in (JURISPRUDENCIA_DIR, PDFS_DIR, DESCUBRIMIENTO_DIR):
        d.mkdir(parents=True, exist_ok=True)
