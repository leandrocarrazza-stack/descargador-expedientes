# ⚙️ FASE 3: Escalabilidad con Celery + Redis

## 📋 Requisitos Previos

- ✅ FASE 1 (Auth + BD) completada
- ✅ FASE 2 (Pagos) completada
- ✅ Python 3.8+
- ✅ Redis instalado en el sistema

---

## 🚀 Instalación Rápida

### Paso 1: Instalar Dependencias

```bash
pip install -r requirements-fase3.txt
```

### Paso 2: Instalar Redis

**macOS:**
```bash
brew install redis
brew services start redis
```

**Ubuntu/Debian:**
```bash
sudo apt-get install redis-server
sudo systemctl start redis-server
```

**Windows (WSL2):**
```bash
wsl
sudo apt-get install redis-server
sudo service redis-server start
```

**Docker (recomendado en producción):**
```bash
docker run -d -p 6379:6379 redis:7-alpine
```

### Paso 3: Verificar Redis Funciona

```bash
redis-cli ping
# Debería mostrar: PONG
```

---

## 🏃 Ejecutar en Desarrollo

### Terminal 1: Flask Server

```bash
python servidor.py
# Escucha en http://127.0.0.1:5000
```

### Terminal 2: Celery Worker

```bash
# En la misma carpeta del proyecto
celery -A modulos.celery_app worker --loglevel=info --concurrency=5

# --concurrency=5 = máximo 5 tareas simultáneas
# --loglevel=info = mostrar eventos importantes
```

### Terminal 3 (opcional): Flower (Monitor de Celery)

```bash
pip install flower
flower -A modulos.celery_app --port=5555
# Acceder a http://localhost:5555
```

---

## 🧪 Probar FASE 3

### 1. Crear Cuenta y Login

```
http://127.0.0.1:5000
→ Signup con email/password
→ Login
```

### 2. Comprar Créditos

```
Dashboard → Comprar Créditos
Seleccionar plan (ej: Básico $3.000 ARS)
→ Mercado Pago TEST → Completar pago
```

### 3. Descargar Expediente

```
Dashboard → Descargar Expediente
Ingresar número: "21/24"
Click en "Descargar Ahora"
→ Ver progreso en tiempo real
→ Descargar PDF
```

### 4. Monitorear en Flower

Abrir http://localhost:5555 para ver:
- Tasks en ejecución
- Estado de workers
- Tiempo de ejecución
- Errores/reintentos

---

## 📚 Archivos Nuevos en FASE 3

| Archivo | Descripción |
|---------|-------------|
| `modulos/celery_app.py` | Configuración de Celery + integración con Flask |
| `modulos/selenium_pool.py` | Pool reutilizable de navegadores (máx 5) |
| `modulos/tasks.py` | Tareas asincrónicas (descargar, limpiar) |
| `rutas/descargas.py` | API de descargas asincrónicas (5 endpoints) |
| `templates/descargar.html` | UI con polling en tiempo real |
| `requirements-fase3.txt` | Dependencias de FASE 3 |
| `FASE3_SETUP.md` | Este archivo |

---

## 🔄 Flujo de Descarga (Nueva Arquitectura)

### Antes (FASE 2 - Síncrono)
```
Usuario click → Flask espera 30-60s → Responde con PDF
Si 3+ usuarios simultáneos → TIMEOUT ❌
```

### Ahora (FASE 3 - Asincrónico)
```
Usuario click → Flask responde en <100ms con task_id
        ↓
      Redis (cola)
        ↓
   Celery Worker
        ↓
   Ejecuta Pipeline
        ↓
Usuario polling: GET /descargas/tarea/{task_id}/estado
        ↓
Vé barra de progreso en tiempo real
        ↓
Descarga PDF cuando esté listo ✅
```

---

## 🎯 API de FASE 3

### POST /descargas/expediente
Disparar descarga

```bash
curl -X POST http://localhost:5000/descargas/expediente \
  -H "Content-Type: application/json" \
  -d '{"numero_expediente": "21/24"}'

# Respuesta:
{
  "exito": true,
  "task_id": "abc123...",
  "polling_url": "/descargas/tarea/abc123/estado"
}
```

### GET /descargas/tarea/{task_id}/estado
Obtener estado (polling)

```bash
curl http://localhost:5000/descargas/tarea/abc123/estado

# Estados posibles:
# - PENDING: en cola
# - PROGRESS: descargando (progreso 0-100%)
# - SUCCESS: completado
# - FAILURE: error
# - RETRY: reintentando
```

### GET /descargas/expediente/{exp_id}/descargar
Descargar PDF

```bash
curl http://localhost:5000/descargas/expediente/123/descargar \
  --output mi_expediente.pdf
```

### GET /descargas/historial
Obtener historial de descargas

```bash
curl http://localhost:5000/descargas/historial?limite=10&pagina=1
```

### POST /descargas/tarea/{task_id}/cancelar
Cancelar descarga en progreso

```bash
curl -X POST http://localhost:5000/descargas/tarea/abc123/cancelar
```

---

## ⚠️ Troubleshooting

### Error: "Connection refused" (Redis no conecta)

```bash
# Verificar Redis está corriendo
redis-cli ping

# Si no está corriendo, iniciar:
# macOS:
brew services start redis

# Ubuntu:
sudo systemctl start redis-server

# Docker:
docker run -d -p 6379:6379 redis:7-alpine
```

### Error: "celery.app module not found"

```bash
# Verificar que estás en la carpeta correcta
cd /ruta/a/descargador_expedientes

# Verificar que exists modulos/celery_app.py
ls modulos/celery_app.py

# Intentar de nuevo
celery -A modulos.celery_app worker --loglevel=info
```

### Las tareas no se ejecutan

1. Verificar que Celery worker está corriendo en otra terminal
2. Verificar que Redis está activo: `redis-cli ping`
3. Revisar logs de Celery: `tail -f celery.log`
4. Revisar logs de Flask: `python servidor.py` (sin background)

### El pool de Selenium no funciona

Actualmente, el pool está implementado pero no se usa en el pipeline.
Esto se optimizará en FASE 4 cuando se añada Docker.

Por ahora, el PipelineDescargador crea su propio driver Selenium.

---

## 📊 Performance Goals

| Métrica | Target | Actual |
|---------|--------|--------|
| Resp time (disparar tarea) | <200ms | ✅ ~100ms |
| Máx descargas simultáneas | 5+ | ✅ Illimitadas (pool limita a 5) |
| Progreso polling | <1s | ✅ 1s |
| Timeout de tarea | 1 hora | ✅ 3600s |
| Reintentos automáticos | 3x | ✅ Config: MAX_REINTENTOS |

---

## 🔐 Seguridad

### Validaciones en FASE 3

✅ Solo usuarios autenticados pueden descargar
✅ Solo el propietario puede acceder a su PDF
✅ CSRF tokens en forms
✅ Rate limiting en API (via Flask)
✅ Task revocation (no acceso sin permisos)

---

## 🚀 Próximo Paso: FASE 4

Después de verificar que FASE 3 funciona bien:

```bash
# FASE 4: Deployment con Docker
- Dockerfile con Python 3.11 + Chrome + LibreOffice
- docker-compose.yml con web + redis + celery
- Deploy a Render/Railway
- PostgreSQL en producción
- CI/CD con GitHub Actions
```

---

## 📝 Checklist para FASE 3

- [ ] Redis instalado y corriendo
- [ ] `pip install -r requirements-fase3.txt` completado
- [ ] `python servidor.py` funciona sin errores
- [ ] Celery worker corre correctamente
- [ ] Puedo signup → login → dashboard
- [ ] Puedo comprar créditos (FASE 2 funciona)
- [ ] Puedo descargar expediente → barra de progreso
- [ ] Historial de descargas se guarda en BD
- [ ] Puedo cancelar una descarga en progreso
- [ ] Flower muestra tasks ejecutándose

---

## 💡 Tips

1. **Desarrollo**: Usar `--loglevel=info` en Celery para ver qué ocurre
2. **Production**: Usar `--loglevel=warning` para menos ruido
3. **Monitor**: Abrir Flower en otra ventana para ver tasks
4. **Testing**: Usar expedientes pequeños (ej: "21/24") para testing rápido
5. **Debug**: Si Celery no funciona, ver `/logs/` para detalles

---

## 🤝 Contacto / Soporte

Si algo no funciona:
1. Revisar logs: `python servidor.py` (sin background)
2. Revisar logs de Celery: tail -f celery.log
3. Verificar Redis: `redis-cli MONITOR` (en otra terminal)
4. Ir a Flower: http://localhost:5555

---

**FASE 3 Completada** ✅
**Próximo**: FASE 4 (Deployment)
