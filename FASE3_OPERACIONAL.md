# FASE 3: Escalabilidad con Celery + Redis ✅ OPERACIONAL

## Estado del Sistema (2026-03-10 22:00 UTC-3)

### ✅ Componentes Operacionales

1. **Flask Server** (Puerto 5000)
   - Status: `RUNNING`
   - URL: http://localhost:5000
   - Health check: `curl http://localhost:5000` → 200 OK

2. **Celery Workers** (Concurrencia: 5)
   - Status: `RUNNING` (2 procesos activos)
   - Broker: Upstash Redis (`rediss://...@relevant-mouse-67768.upstash.io:6379`)
   - Backend: Redis
   - Verificación: `ps aux | grep celery`

3. **Redis Cloud (Upstash)**
   - Status: `CONNECTED` (vía TLS/SSL)
   - Conexión: `rediss://default:TOKEN@relevant-mouse-67768.upstash.io:6379?ssl_cert_reqs=none`
   - Latency: ~50-100ms (aceptable desde Argentina)

4. **Base de Datos** (SQLite Dev)
   - Tablas: `users`, `expedientes_descargados`, `compras_creditos`
   - Usuario de prueba: `leofard@gmail.com` (100 créditos)

### 📊 Endpoint Status

| Endpoint | Método | Status | Función |
|----------|--------|--------|---------|
| `/descargar` | GET | ✅ | Sirve página de descarga |
| `/descargas/expediente` | POST | ✅ | Dispara descarga asincrónica |
| `/descargas/tarea/<id>/estado` | GET | ✅ | Obtiene estado del progreso (POLLING) |
| `/descargas/expediente/<id>/descargar` | GET | ✅ | Descarga PDF completado |
| `/descargas/historial` | GET | ✅ | Historial del usuario |
| `/descargas/tarea/<id>/cancelar` | POST | ✅ | Cancela tarea en curso |

---

## 🧪 Cómo Probar FASE 3

### Opción 1: Prueba Manual (Navegador)

1. **Navega a http://localhost:5000**
2. **Login** con:
   - Email: `leofard@gmail.com`
   - Contraseña: `Test123!`
3. **Click en "Descargar Expediente"**
4. **Ingresa número** (ej: `21/24`)
5. **Observa la barra de progreso** que se actualiza en tiempo real
6. **El Celery worker procesará la tarea en background**

### Opción 2: Prueba por API (cURL)

```bash
# 1. Login
TOKEN=$(curl -X POST http://localhost:5000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"leofard@gmail.com","password":"Test123!"}' \
  | jq -r '.token')

# 2. Disparar descarga
TASK_ID=$(curl -X POST http://localhost:5000/descargas/expediente \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"numero_expediente":"21/24"}' \
  | jq -r '.task_id')

# 3. Polling del estado
curl http://localhost:5000/descargas/tarea/$TASK_ID/estado \
  -H "Authorization: Bearer $TOKEN"
```

### Opción 3: Monitor con Flower (Opcional)

```bash
# Terminal 3: Flower (Celery monitoring)
flower -A modulos.celery_app --port=5555
# Acceder a http://localhost:5555
```

---

## 🔧 Cambios Realizados en Commit 3fb45e1

### config.py
```python
from dotenv import load_dotenv

# Cargar variables de entorno desde .env
load_dotenv()
```
**Por qué**: Celery necesitaba que `REDIS_URL` estuviera cargado en las variables de entorno.

### rutas/descargas.py
```python
# Antes:
if not expediente.pdf_ruta or not os.path.exists(expediente.pdf_ruta):
    return send_file(expediente.pdf_ruta, ...)

ExpedienteDescargado.descargado_en.desc()
exp.descargado_en.isoformat()

# Después:
if not expediente.pdf_ruta_temporal or not os.path.exists(expediente.pdf_ruta_temporal):
    return send_file(expediente.pdf_ruta_temporal, ...)

ExpedienteDescargado.completado_en.desc()
exp.completado_en.isoformat()
```
**Por qué**: Los nombres de campos en el modelo SQLAlchemy son `pdf_ruta_temporal` y `completado_en`, no `pdf_ruta` y `descargado_en`.

---

## 🚀 Próximo Paso: FASE 4 (Deployment)

### Cuando estés listo para FASE 4, necesitarás:

1. **Docker & Docker Compose**
   ```bash
   # Instalación en Windows
   # https://docs.docker.com/desktop/install/windows-install/
   ```

2. **Archivo Dockerfile** (image personalizada con Chrome, LibreOffice, Python)

3. **docker-compose.yml** (Flask + Celery + Redis + PostgreSQL)

4. **Render.com account** (Deployment gratuito)

5. **GitHub Actions** (CI/CD automático)

### Estimación: 6-8 horas de desarrollo

---

## 📝 Comandos Útiles

### Iniciar todo desde cero

```bash
# Terminal 1: Flask
python servidor.py

# Terminal 2: Celery Worker (5 concurrencias)
celery -A modulos.celery_app worker --loglevel=info --concurrency=5

# Terminal 3 (opcional): Flower Monitor
flower -A modulos.celery_app --port=5555
```

### Verificar salud del sistema

```bash
# Flask disponible?
curl http://localhost:5000

# Celery conectado a Redis?
celery -A modulos.celery_app inspect active

# Ver tareas en cola
celery -A modulos.celery_app inspect reserved
```

### Limpiar bases de datos (resetear estado)

```bash
# Python CLI
from servidor import app, db
with app.app_context():
    db.drop_all()  # ⚠️ CUIDADO: borra toda la BD
    db.create_all()
```

---

## ❓ Preguntas Frecuentes

### P: Celery no se conecta a Upstash
**R**: Verifica que:
- `.env` existe y tiene `REDIS_URL=rediss://...?ssl_cert_reqs=none`
- `config.py` tiene `load_dotenv()` en la primera línea
- Tu conexión a internet está activa

### P: Los expedientes no se descargan
**R**: Verifica logs del Celery worker:
```bash
# En la terminal de Celery, deberías ver:
# [2026-03-10 22:00:00,000: INFO] Task descargar_expediente_task[...] received
# [2026-03-10 22:00:05,000: INFO] Task descargar_expediente_task[...] succeeded
```

### P: ¿Puedo usar SQLite en producción?
**R**: **No**, usa PostgreSQL. FASE 4 incluirá migración a PostgreSQL.

### P: ¿Debo resetear los créditos del usuario?
**R**:
```bash
from servidor import app, db
from modulos.models import User

with app.app_context():
    user = User.query.filter_by(email='leofard@gmail.com').first()
    user.creditos_disponibles = 1000  # Reset
    db.session.commit()
```

---

## 🎯 Resumen FASE 3

- ✅ Celery workers procesando tareas en background
- ✅ Redis (Upstash cloud) como broker confiable
- ✅ Polling en tiempo real del progreso
- ✅ Descarga asincrónica sin bloquear servidor
- ✅ Pool de Selenium (máx 5 navegadores concurrentes)
- ✅ Integración con modelo de créditos prepagados
- ✅ Historial de descargas con paginación

**Todas las tareas de FASE 3 completadas e integradas.**

---

## 📚 Documentación Relacionada

- `.env` - Variables de entorno
- `config.py` - Configuración de Celery
- `modulos/celery_app.py` - Setup de Celery
- `modulos/tasks.py` - Tareas asincrónicas
- `rutas/descargas.py` - Endpoints de descarga
- `templates/descargar.html` - Frontend con polling

**Fecha**: 2026-03-10
**Autor**: Claude (con Haiku 4.5)
**Estado**: ✅ FASE 3 COMPLETADA Y OPERACIONAL
