# Celery en Windows - Solución

## El Problema

Celery 5.6.2 **no soporta Windows** con el pool `prefork` (default). El error que ves:

```
ValueError('not enough values to unpack (expected 3, got 0)')
```

Ocurre cuando Celery intenta usar multiprocessing/billiard que Windows no tiene.

## La Solución

Windows requiere usar un pool diferente: **`solo`** o **`threads`**.

### Opción 1: Pool `solo` (Recomendado para desarrollo)

Ejecuta tareas **sincronizadamente** (una a la vez). Perfecto para testing.

```bash
python worker.py --pool=solo
```

**Ventajas:**
- Funciona en Windows sin dependencias extra
- Ideal para desarrollo y debugging
- Simple, predecible

**Desventajas:**
- Solo ejecuta una tarea por vez
- No es escalable para producción

### Opción 2: Pool `threads` (Recomendado para producción en Windows)

Ejecuta tareas usando **threads** (asincrónico). Mejor para I/O-bound tasks.

```bash
python worker.py --pool=threads --concurrency=10
```

**Ventajas:**
- Concurrencia real (múltiples descargas simultáneas)
- Funciona bien para tareas I/O-bound (HTTP, browser scraping)
- Mejor para producción en Windows

**Desventajas:**
- Las tareas comparten memoria (GIL de Python)
- No ideal para CPU-bound tasks

## Nuestro Caso

Las descargas son **I/O-bound** (HTTP, selenium browser), no CPU-bound.

**Recomendación:**
- **Desarrollo**: `python worker.py --pool=solo`
- **Producción (Windows)**: `python worker.py --pool=threads --concurrency=10`
- **Producción (Linux/Docker)**: `python worker.py --pool=prefork --concurrency=5` (mantiene el default)

## Flujo Completo para Testear

1. Asegúrate que Redis esté corriendo:
```bash
# Windows con WSL2/Docker
docker run -d -p 6379:6379 redis:7-alpine

# O si tienes Redis instalado localmente:
redis-server
```

2. Abre 2 terminales:

**Terminal 1 - Servidor Flask:**
```bash
python servidor.py
# Abre http://localhost:5000
```

**Terminal 2 - Celery Worker:**
```bash
# Para desarrollo (síncrono):
python worker.py --pool=solo

# O para testing de concurrencia:
python worker.py --pool=threads --concurrency=10
```

3. En Flask, dispara una tarea:
```python
from modulos.tasks import descargar_expediente_task

task = descargar_expediente_task.delay(user_id=1, numero_expediente="21/24")
print(f"Task ID: {task.id}")
```

4. Observa el worker procesando la tarea.

## Para Producción

Si desplegás en **Linux** (Render, AWS, etc.):

```bash
# Funciona con el pool default (prefork)
celery -A modulos.celery_app worker --loglevel=info --concurrency=5 --autoscale=10,3
```

Si desplegás en **Windows Server** o prefieres evitar problemas:

```bash
# Usa threads
celery -A modulos.celery_app worker --pool=threads --loglevel=info --concurrency=10
```

## Resumen

| Ambiente | Comando | Pool | Notas |
|----------|---------|------|-------|
| Dev Windows | `python worker.py --pool=solo` | solo | Una tarea a la vez |
| Prod Windows | `python worker.py --pool=threads --concurrency=10` | threads | Concurrencia con threads |
| Prod Linux | `celery -A modulos.celery_app worker --concurrency=5` | prefork | Multiprocessing |

---

**Fecha**: 2026-03-10
**Fix**: Actualizado worker.py para soportar múltiples pools con detección automática de SO
