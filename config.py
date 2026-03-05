"""
Configuración del descargador de expedientes
============================================

IMPORTANTE: Este archivo NO contiene credenciales. Las cookies de sesión
se guardan automáticamente en ~/.mesa_virtual_cookies/

No es necesario configurar nada aquí para la autenticación.
"""

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE URLS Y ENDPOINTS
# ═══════════════════════════════════════════════════════════════════════════

# URL base de la Mesa Virtual
MESA_VIRTUAL_URL = "https://mesavirtual.jusentrerios.gov.ar"


# Endpoint de la API GraphQL
API_GRAPHQL = "https://mesavirtual.jusentrerios.gov.ar/api/graphql"

# Endpoint de descarga de archivos
API_ARCHIVOS = "https://mesavirtual.jusentrerios.gov.ar/api/archivos"

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE DIRECTORIOS
# ═══════════════════════════════════════════════════════════════════════════

from pathlib import Path

# Carpeta base del proyecto
PROJECT_DIR = Path(__file__).parent

# Carpeta de archivos temporales
TEMP_DIR = PROJECT_DIR / "temp"

# Carpeta de PDFs finales
OUTPUT_DIR = PROJECT_DIR / "output"

# Carpeta de logs
LOGS_DIR = PROJECT_DIR / "logs"

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE COMPORTAMIENTO
# ═══════════════════════════════════════════════════════════════════════════

# Timeout para requests HTTP (en segundos)
REQUEST_TIMEOUT = 30

# Reintentos automáticos si falla una descarga
MAX_REINTENTOS = 3

# Verbose mode (mostrar más información durante la ejecución)
DEBUG = False

# Limpiar archivos temporales al finalizar
LIMPIAR_TEMP = True

# ═══════════════════════════════════════════════════════════════════════════
#  CONFIGURACIÓN DE LIBREOFFICE (conversión RTF → PDF)
# ═══════════════════════════════════════════════════════════════════════════

# Intentar convertir RTF a PDF (requiere LibreOffice instalado)
CONVERTIR_RTF = True

# Timeout para conversión (en segundos)
CONVERSION_TIMEOUT = 60

# ═══════════════════════════════════════════════════════════════════════════
#  VALIDACIÓN AUTOMÁTICA
# ═══════════════════════════════════════════════════════════════════════════

def validar_config():
    """Valida que la configuración básica sea correcta."""
    # Verificar que las URLs estén configuradas
    if not MESA_VIRTUAL_URL or not API_GRAPHQL or not API_ARCHIVOS:
        print("\n" + "═" * 70)
        print("  ❌ ERROR: URLs NO CONFIGURADAS")
        print("═" * 70 + "\n")
        print("Edita config.py y asegúrate de que las URLs estén presentes.")
        print("\n" + "═" * 70 + "\n")
        return False

    return True


if __name__ == "__main__":
    print("Validando configuración...")
    if validar_config():
        print("✅ Configuración válida")
    else:
        print("❌ Configuración incompleta")
