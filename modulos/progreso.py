# modulos/progreso.py
"""
Constantes compartidas para el reporte de progreso hacia el frontend.

Vive en su propio módulo (y no dentro de descarga.py) para que unificacion.py
pueda usar la misma cadencia sin arrastrar Selenium como dependencia.

El flujo completo del progreso es:

    descarga.py / pipeline.py / unificacion.py
        └─ on_progreso(dict)                     ← callback opcional
             └─ rutas/descargas.py::_publicar_progreso
                  └─ _jobs[job_id]['progreso']
                       └─ GET /descargas/progreso/<job_id>
                            └─ el contador "47 de 213" en la página
"""

# Cada cuántos archivos se publica progreso.
#
# Contar es gratis: es un dict en memoria, no toca disco ni red, así que este
# número NO afecta la velocidad de la descarga. Sólo controla qué tan finos son
# los saltos del contador que ve el usuario. Bajarlo a 1 hace que avance de a un
# archivo sin costo alguno; subirlo lo hace más grueso.
#
# Ojo: además de esta cadencia, siempre se publica al cambiar de página y al
# terminar cada etapa, así el contador nunca queda quieto una página entera.
PROGRESO_CADA_N_ARCHIVOS = 1
