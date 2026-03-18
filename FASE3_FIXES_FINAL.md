# FASE 3: Resumen de Fixes - Marzo 10, 2026

## Problema Principal Resuelto ✅

### Error Original
```
RuntimeError: Working outside of application context
```

### Causa Raíz
Flask-SQLAlchemy requiere un **contexto de aplicación activo** para ejecutar queries. Cuando Celery ejecuta tareas en background workers, no hay contexto de Flask activo, causando que `User.query.get()` y `db.session.commit()` fallen.

---

## Soluciones Implementadas

### 1. **Agregar Flask App Context a Tareas**
**Commit**: b5bf6d4

#### Problema
```python
# ❌ ANTES - Esto causaba RuntimeError
@celery_app.task
def descargar_expediente_task(user_id, numero_expediente):
    usuario = User.query.get(user_id)  # ← RuntimeError aquí
```

#### Solución
```python
# ✅ DESPUÉS - Envolver en with app.app_context()
@celery_app.task
def descargar_expediente_task(user_id, numero_expediente):
    from servidor import app  # Importación local

    with app.app_context():  # ← Crea contexto
        usuario = User.query.get(user_id)  # ✅ Funciona ahora
        db.session.commit()  # ✅ Funciona ahora
```

#### Tareas Afectadas
- ✅ `descargar_expediente_task` (línea 81-213)
- ✅ `limpiar_descargas_antiguas_task` (línea 235)
- ℹ️ `verificar_pool_selenium_task` (no accede a BD, no necesita cambios)

### 2. **Resolver Importación Circular**
**Commit**: a9e2d80

#### Problema
```
servidor.py → rutas.descargas → modulos.tasks → servidor.py
                                           ↑
                                 Circular import!
```

#### Causa
La línea 30 de `modulos/tasks.py` importaba `app` globalmente:
```python
from servidor import app  # ← Causa circular import
```

#### Solución
```python
# Remover importación global (línea 30)
# from servidor import app

# En su lugar, importar LOCALMENTE dentro de las funciones:
def descargar_expediente_task(...):
    from servidor import app  # ← Importación tardía, evita circular import
    with app.app_context():
        ...
```

---

## Pruebas Realizadas ✅

### Test 1: Servidor Flask
```bash
$ python servidor.py
✅ Servidor inicia sin errores
✅ Blueprints registrados correctamente
✅ BD inicializada con 3 tablas
```

### Test 2: Worker Celery
```bash
$ python worker.py --pool=solo
✅ Conecta a Upstash Redis exitosamente
✅ Registra 3 tareas automáticamente:
   - tareas.descargar_expediente
   - tareas.limpiar_descargas_antiguas
   - tareas.verificar_pool_selenium
```

### Test 3: Ejecución de Tarea
```python
# Python REPL:
from modulos.tasks import descargar_expediente_task
task = descargar_expediente_task.delay(user_id=1, numero_expediente='21/24')
# Resultado: Task ID = 54981db5-f704-4e6e-ae12-1effa9fe266e

# En worker logs:
✅ Task received
✅ User.query executed WITHOUT RuntimeError
✅ db.session.commit() executed
✅ Expediente saved to BD
```

---

## Cómo Ejecutar FASE 3

### Setup Inicial (una sola vez)
```bash
# Crear usuario de prueba
python << 'EOF'
from servidor import app, db
from modulos.models import User

with app.app_context():
    user = User(
        email='test@example.com',
        nombre='Test User',
        plan='profesional',
        creditos_disponibles=100
    )
    user.set_password('Test123!')
    db.session.add(user)
    db.session.commit()
    print(f"Usuario creado: {user.email}")
EOF
```

### Ejecución Diaria

**Terminal 1: Flask Server**
```bash
python servidor.py
# Escucha en http://localhost:5000
```

**Terminal 2: Celery Worker**
```bash
# Opción A: Modo síncrono (Windows)
python worker.py --pool=solo

# Opción B: Con concurrencia (Windows)
python worker.py --pool=threads --concurrency=10

# Opción C: Comando directo
celery -A modulos.celery_app worker --loglevel=info
```

**Terminal 3: Monitor (Opcional)**
```bash
flower -A modulos.celery_app --port=5555
# Acceder a http://localhost:5555
```

---

## Arquitectura Final

```
┌─────────────────────────────────────────────────────────┐
│                    CLIENTE (Navegador)                   │
│                    http://localhost:5000                 │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│                  FLASK SERVER (Puerto 5000)              │
│  ✅ Autenticación ✅ Pagos ✅ API REST endpoints      │
└──────────────┬──────────────────────────────────────────┘
               │ (dispara tareas asincrónicas)
               ▼
┌─────────────────────────────────────────────────────────┐
│               UPSTASH REDIS (Broker)                     │
│  rediss://relevant-mouse-67768.upstash.io:6379         │
└──────────────┬──────────────────────────────────────────┘
               │ (cola de mensajes)
               ▼
┌─────────────────────────────────────────────────────────┐
│            CELERY WORKER (Pool: solo/threads)           │
│  ✅ descargar_expediente_task                          │
│  ✅ limpiar_descargas_antiguas_task                     │
│  ✅ verificar_pool_selenium_task                        │
└──────────────┬──────────────────────────────────────────┘
               │ (acceso a BD dentro de app context)
               ▼
┌─────────────────────────────────────────────────────────┐
│                 SQLite / PostgreSQL                      │
│  ✅ Usuarios ✅ Expedientes ✅ Descargas               │
└─────────────────────────────────────────────────────────┘
```

---

## Cambios Realizados Hoy

| Archivo | Cambio | Razón |
|---------|--------|-------|
| `modulos/tasks.py` | Agregar `with app.app_context():` wrapper | Permitir db.session en workers |
| `modulos/tasks.py` | Importación local de `app` | Evitar circular import |
| `worker.py` | Auto-detección de Windows pool | Compatibilidad con Windows |
| `memoria/MEMORY.md` | Actualizar estado de FASE 3 | Documentar solución |

---

## Estado Final: OPERACIONAL ✅

✅ **Servidor Flask**: Corriendo sin errores
✅ **Celery Workers**: Conectados a Redis, ejecutando tareas
✅ **Base de Datos**: Actualizaciones dentro del worker context
✅ **SIN RuntimeError**: Las queries SQLAlchemy funcionan correctamente
✅ **Windows Compatible**: Pool=solo funciona en Windows 11

---

## Próximos Pasos: FASE 4

- [ ] Docker containerization
- [ ] PostgreSQL en producción
- [ ] Deploy a Render.com
- [ ] GitHub Actions CI/CD
- [ ] SSL/HTTPS en producción

**Estimación**: 6-8 horas de desarrollo

---

**Fecha completada**: 2026-03-10 22:54 UTC-3
**Estado**: ✅ FASE 3 COMPLETADA Y OPERACIONAL
