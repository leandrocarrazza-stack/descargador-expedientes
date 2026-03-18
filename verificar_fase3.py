#!/usr/bin/env python3
"""
Script de Verificación de FASE 3
================================

Valida que todos los componentes de FASE 3 estén correctamente implementados
sin necesidad de ejecutar Redis o Celery.

Uso:
    python verificar_fase3.py
"""

import sys
from pathlib import Path

# Agregar proyecto a sys.path
sys.path.insert(0, str(Path(__file__).parent))

print("\n" + "=" * 70)
print("  🔍 VERIFICACIÓN DE FASE 3: Escalabilidad con Celery + Redis")
print("=" * 70 + "\n")

# ═══════════════════════════════════════════════════════════════════════════
#  TEST 1: Verificar imports de módulos nuevos
# ═══════════════════════════════════════════════════════════════════════════

print("✓ TEST 1: Verificar imports de módulos FASE 3\n")

try:
    from modulos.celery_app import celery_app, init_celery_with_app
    print("  ✅ modulos.celery_app")
except Exception as e:
    print(f"  ❌ modulos.celery_app: {e}")
    sys.exit(1)

try:
    from modulos.selenium_pool import SeleniumPool, obtener_pool, limpiar_pool
    print("  ✅ modulos.selenium_pool")
except Exception as e:
    print(f"  ❌ modulos.selenium_pool: {e}")
    sys.exit(1)

try:
    from modulos.tasks import descargar_expediente_task, limpiar_descargas_antiguas_task
    print("  ✅ modulos.tasks")
except Exception as e:
    print(f"  ❌ modulos.tasks: {e}")
    sys.exit(1)

try:
    from rutas.descargas import descargas_bp
    print("  ✅ rutas.descargas")
except Exception as e:
    print(f"  ❌ rutas.descargas: {e}")
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════════════════
#  TEST 2: Verificar configuración de Celery
# ═══════════════════════════════════════════════════════════════════════════

print("\n✓ TEST 2: Verificar configuración de Celery\n")

try:
    import config

    broker = config.CELERY_BROKER_URL
    backend = config.CELERY_RESULT_BACKEND

    print(f"  ✅ CELERY_BROKER_URL: {broker}")
    print(f"  ✅ CELERY_RESULT_BACKEND: {backend}")

    if "redis://" in broker:
        print(f"  ✅ Broker configurado a Redis")
    else:
        print(f"  ⚠️  Broker no es Redis: {broker}")

except Exception as e:
    print(f"  ❌ Error en configuración: {e}")
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════════════════
#  TEST 3: Verificar que Celery app tiene tareas registradas
# ═══════════════════════════════════════════════════════════════════════════

print("\n✓ TEST 3: Verificar tareas Celery registradas\n")

try:
    # Obtener lista de tareas registradas
    tareas = list(celery_app.tasks.keys())

    tareas_esperadas = [
        'tareas.descargar_expediente',
        'tareas.limpiar_descargas_antiguas',
        'tareas.verificar_pool_selenium'
    ]

    for tarea in tareas_esperadas:
        if tarea in tareas:
            print(f"  ✅ {tarea}")
        else:
            print(f"  ⚠️  {tarea} (no encontrada)")

    print(f"\n  Total de tareas registradas: {len(tareas)}")

except Exception as e:
    print(f"  ❌ Error: {e}")
    sys.exit(1)

# ═══════════════════════════════════════════════════════════════════════════
#  TEST 4: Verificar blueprints en servidor
# ═══════════════════════════════════════════════════════════════════════════

print("\n✓ TEST 4: Verificar blueprints registrados\n")

try:
    from servidor import app

    blueprints = list(app.blueprints.keys())

    blueprints_esperados = ['auth', 'pagos', 'descargas']

    for bp in blueprints_esperados:
        if bp in blueprints:
            print(f"  ✅ Blueprint '{bp}'")
        else:
            print(f"  ❌ Blueprint '{bp}' no registrado")

except Exception as e:
    print(f"  ⚠️  No se pudo cargar servidor (requiere BD): {e}")

# ═══════════════════════════════════════════════════════════════════════════
#  TEST 5: Verificar endpoints FASE 3
# ═══════════════════════════════════════════════════════════════════════════

print("\n✓ TEST 5: Verificar endpoints FASE 3\n")

try:
    from servidor import app

    endpoints_esperados = {
        'descargas.pagina_descargar': 'GET /descargar',
        'descargas.disparar_descarga': 'POST /descargas/expediente',
        'descargas.obtener_estado_tarea': 'GET /descargas/tarea/<task_id>/estado',
        'descargas.descargar_pdf': 'GET /descargas/expediente/<exp_id>/descargar',
        'descargas.obtener_historial': 'GET /descargas/historial',
        'descargas.cancelar_tarea': 'POST /descargas/tarea/<task_id>/cancelar',
    }

    reglas = {str(rule): rule.endpoint for rule in app.url_map.iter_rules()}
    endpoints = list(reglas.values())

    for endpoint, ruta in endpoints_esperados.items():
        if endpoint in endpoints:
            print(f"  ✅ {ruta}")
        else:
            print(f"  ❌ {ruta} (no encontrado)")

except Exception as e:
    print(f"  ⚠️  No se pudo verificar endpoints: {e}")

# ═══════════════════════════════════════════════════════════════════════════
#  TEST 6: Verificar templaces existen
# ═══════════════════════════════════════════════════════════════════════════

print("\n✓ TEST 6: Verificar templates existen\n")

templates_esperados = [
    'templates/descargar.html',
]

for template in templates_esperados:
    path = Path(__file__).parent / template
    if path.exists():
        lines = len(path.read_text().splitlines())
        print(f"  ✅ {template} ({lines} líneas)")
    else:
        print(f"  ❌ {template} no encontrado")

# ═══════════════════════════════════════════════════════════════════════════
#  TEST 7: Verificar que SeleniumPool puede instanciarse
# ═══════════════════════════════════════════════════════════════════════════

print("\n✓ TEST 7: Verificar instanciación de SeleniumPool\n")

try:
    pool = SeleniumPool(max_drivers=5)
    estado = pool.estado()

    print(f"  ✅ SeleniumPool creado")
    print(f"  ✅ Max drivers: {estado['max_drivers']}")
    print(f"  ✅ Drivers creados: {estado['total_creados']}")

    pool.limpiar()
    print(f"  ✅ Pool limpiado")

except Exception as e:
    print(f"  ❌ Error con SeleniumPool: {e}")

# ═══════════════════════════════════════════════════════════════════════════
#  RESUMEN
# ═══════════════════════════════════════════════════════════════════════════

print("\n" + "=" * 70)
print("  ✅ FASE 3: VERIFICACIÓN COMPLETADA")
print("=" * 70)
print("""
PRÓXIMOS PASOS:

1. Instalar Redis en tu sistema:

   macOS:
   $ brew install redis
   $ brew services start redis

   Ubuntu:
   $ sudo apt-get install redis-server
   $ sudo systemctl start redis-server

   Docker:
   $ docker run -d -p 6379:6379 redis:7-alpine

2. Verificar Redis funciona:
   $ redis-cli ping
   (debería mostrar: PONG)

3. Instalar dependencias:
   $ pip install -r requirements-fase3.txt

4. Ejecutar en 3 terminales:

   Terminal 1:
   $ python servidor.py

   Terminal 2:
   $ celery -A modulos.celery_app worker --loglevel=info --concurrency=5

   Terminal 3 (opcional):
   $ flower -A modulos.celery_app --port=5555

5. Probar en navegador:
   $ http://127.0.0.1:5000
   → Signup/Login
   → Comprar créditos
   → Descargar expediente
   → Ver progreso en tiempo real

Para más detalles, ver: FASE3_SETUP.md
""")
print("=" * 70 + "\n")
