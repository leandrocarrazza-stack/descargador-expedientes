# Proyecto: Descargador de Expedientes - Estado Actual

## 📌 FASE 1: COMPLETADA ✅

**Commit**: 376de73
**Fecha**: 2026-03-09
**Descripción**: Autenticación + Base de Datos SQLAlchemy

### Qué Incluye
✅ Login/Signup con email + bcrypt
✅ Flask-SQLAlchemy con modelos User, ExpedienteDescargado, CompraCreditos
✅ Flask-Login para sesiones
✅ Templates Jinja2 + Bootstrap 5
✅ Migración de BD con Alembic

### Estructura FASE 1
```
modulos/
  ├─ models.py        (User, ExpedienteDescargado, CompraCreditos)
  ├─ auth.py          (password hashing, utils)
  ├─ database.py      (SQLAlchemy init)
  └─ logger.py
rutas/
  └─ auth.py          (/signup, /login, /logout)
templates/
  ├─ base.html        (layout principal)
  ├─ login.html
  ├─ signup.html
  ├─ dashboard.html
  └─ inicio.html
```

---

## 📌 FASE 2: COMPLETADA ✅

**Commits**:
- 80f18c1: Sistema de Pago Mercado Pago (USD)
- 6571b1c: Rediseño con ARS + Créditos Prepagados

**Descripción**: Integración con Mercado Pago

### Qué Incluye
✅ 6 endpoints de pago en `rutas/pagos.py`
✅ Integración API REST Mercado Pago (sin SDK)
✅ Planes en ARS: Básico ($3.000), Ahorro ($12.000), Profesional ($42.000)
✅ Modelo de créditos: usuario compra → gasta al descargar
✅ Webhook de confirmación de pago
✅ UI con 4 templates (planes, confirmación, error, pendiente)

### Endpoints FASE 2
- `GET /pagos/planes` - Mostrar planes disponibles
- `POST /pagos/crear-orden` - Crear orden Mercado Pago
- `POST /pagos/webhook` - Webhook de confirmación
- `GET /pagos/pago-confirmado` - Página de éxito
- `GET /pagos/pago-fallido` - Página de error
- `GET /pagos/pago-pendiente` - Página de transferencia pendiente
- `GET /pagos/historial` - Historial de compras

### Estructura FASE 2
```
modulos/
  └─ mercado_pago.py  (API REST a Mercado Pago)
rutas/
  └─ pagos.py         (6 endpoints)
templates/
  ├─ planes.html      (cards con planes ARS)
  ├─ pago-confirmado.html
  ├─ pago-fallido.html
  └─ pago-pendiente.html
config.py
  └─ PLANES = {basico, ahorro, profesional} (ARS)
```

### Configuración FASE 2
```env
MERCADO_PAGO_PUBLIC_KEY=APP_USR-...
MERCADO_PAGO_ACCESS_TOKEN=APP_USR-...
MERCADO_PAGO_SUCCESS_URL=http://localhost:5000/pagos/pago-confirmado
MERCADO_PAGO_FAILURE_URL=http://localhost:5000/pagos/pago-fallido
MERCADO_PAGO_PENDING_URL=http://localhost:5000/pagos/pago-pendiente
```

---

## 📌 FASE 3: COMPLETADA ✅

**Commits**: (próximo después de testing)
**Descripción**: Escalabilidad con Celery + Redis + Pool Selenium

### Qué Incluye
✅ Celery para tareas asincrónicas (background)
✅ Redis como broker de tareas
✅ Pool reutilizable de navegadores Selenium (máx 5)
✅ API asincrónica con polling en tiempo real
✅ 5 endpoints nuevos en `rutas/descargas.py`
✅ UI responsiva con progreso en vivo (`templates/descargar.html`)
✅ Reintentos automáticos (MAX_REINTENTOS = 3)

### Flujo FASE 3
```
Usuario click "Descargar"
    ↓
Flask responde <100ms con task_id (no espera)
    ↓
Tarea entra a Cola Redis
    ↓
Celery Worker procesa (en background)
    ↓
Usuario hace polling: GET /descargas/tarea/{task_id}/estado
    ↓
Ve barra de progreso 0-100%
    ↓
Cuando esté listo: Descarga PDF ✅
```

### Ventajas FASE 3
- ✅ No bloquea servidor
- ✅ Soporta múltiples descargas simultáneas (illimitadas)
- ✅ Reintentos automáticos si falla
- ✅ Cancelación de tareas
- ✅ Historial persistente en BD
- ✅ Monitoreo con Flower

### Estructura FASE 3
```
modulos/
  ├─ celery_app.py      (Configuración de Celery + integración Flask)
  ├─ selenium_pool.py   (Pool de navegadores, máx 5)
  ├─ tasks.py           (Tareas async: descargar_expediente_task)
rutas/
  └─ descargas.py       (5 endpoints: disparar, estado, descargar, historial, cancelar)
templates/
  └─ descargar.html     (UI con formulario + polling + historial)
config.py
  └─ CELERY_BROKER_URL = redis://localhost:6379/0
     CELERY_RESULT_BACKEND = redis://localhost:6379/0
servidor.py
  └─ init_celery_with_app(app)  (Inicializar Celery)
requirements-fase3.txt
  ├─ celery==5.3.1
  ├─ redis==4.5.5
  └─ selenium==4.8.0
FASE3_SETUP.md          (Guía de instalación y ejecución)
```

### Endpoints FASE 3
1. **GET /descargar** - Página de descarga (formulario + progreso)
2. **POST /descargas/expediente** - Disparar tarea (respuesta inmediata)
3. **GET /descargas/tarea/{task_id}/estado** - Polling (estado 0-100%)
4. **GET /descargas/expediente/{exp_id}/descargar** - Descargar PDF
5. **GET /descargas/historial** - Historial paginado
6. **POST /descargas/tarea/{task_id}/cancelar** - Cancelar tarea

### Tareas Celery
- `descargar_expediente_task` - Ejecuta pipeline de descarga
  - Valida usuario + créditos
  - Ejecuta PipelineDescargador
  - Guarda en BD
  - Deduce 1 crédito si éxito
  - Reintentos automáticos (hasta 3)

- `limpiar_descargas_antiguas_task` - Limpia archivos viejos (>7 días)
- `verificar_pool_selenium_task` - Monitoreo del pool

---

## ⚙️ Instalación y Ejecución

### Instalación Rápida
```bash
# Instalar dependencias FASE 3
pip install -r requirements-fase3.txt

# Instalar Redis (si no lo tienes)
# macOS:
brew install redis && brew services start redis

# Ubuntu:
sudo apt-get install redis-server && sudo systemctl start redis-server

# Docker:
docker run -d -p 6379:6379 redis:7-alpine
```

### Ejecutar en Desarrollo (3 terminales)

**Terminal 1: Flask Server**
```bash
python servidor.py
# http://127.0.0.1:5000
```

**Terminal 2: Celery Worker**
```bash
celery -A modulos.celery_app worker --loglevel=info --concurrency=5
# Procesa máx 5 tareas simultáneas
```

**Terminal 3 (opcional): Flower Monitor**
```bash
flower -A modulos.celery_app --port=5555
# http://localhost:5555 (visualizar tasks en ejecución)
```

---

## 🧪 Testing FASE 3

### Checklist de Pruebas
- [ ] Redis corre: `redis-cli ping` → PONG
- [ ] Flask inicia sin errores: `python servidor.py`
- [ ] Celery worker funciona: `celery -A modulos.celery_app worker --loglevel=info`
- [ ] Puedo login/signup
- [ ] Puedo comprar créditos (FASE 2)
- [ ] Barra de progreso aparece en /descargar
- [ ] Polling actualiza estado cada 1 segundo
- [ ] PDF se descarga exitosamente
- [ ] Historial guarda descargas en BD
- [ ] Puedo cancelar una tarea en progreso
- [ ] Celery reinteneta automáticamente si falla

---

## 🚀 Próximas Fases

### FASE 4: Deployment (estimado 6-8 horas)

**Qué se implementará:**
- Dockerfile con Python 3.11 + Chrome + LibreOffice
- docker-compose.yml (web + redis + celery workers)
- PostgreSQL en producción (Render)
- GitHub Actions CI/CD
- Nginx como reverse proxy
- SSL/HTTPS automático

**Cuando comenzar:**
```bash
git add .
git commit -m "FASE 3: Escalabilidad con Celery + Redis + Pool Selenium ✅"
git push origin main

# Luego iniciar FASE 4 cuando estés listo:
# "Continuemos con FASE 4: Deployment"
```

---

## 📊 Resumen de Fases

| Fase | Tema | Estado | Dependencias |
|------|------|--------|--------------|
| 1 | Auth + BD | ✅ | Flask, SQLAlchemy |
| 2 | Pagos | ✅ | Mercado Pago API |
| 3 | Async Queue | ✅ | Celery, Redis, Selenium |
| 4 | Deployment | ⏳ | Docker, PostgreSQL |

---

## 🔗 Archivos Importantes

| Archivo | Propósito |
|---------|-----------|
| `servidor.py` | Factory Flask app |
| `config.py` | Configuración global |
| `modulos/celery_app.py` | Celery + Flask integration |
| `modulos/tasks.py` | Tareas asincrónicas |
| `rutas/descargas.py` | API endpoints FASE 3 |
| `templates/descargar.html` | UI con polling |
| `requirements-fase3.txt` | Dependencias |
| `FASE3_SETUP.md` | Guía detailed |
| `.env` | Variables de entorno |

---

## 🔐 Notas de Seguridad

✅ Validación de usuario en cada endpoint
✅ Solo propietario puede descargar su PDF
✅ CSRF protection en forms
✅ Contraseñas hasheadas (bcrypt)
✅ No hay secrets en código (variables en .env)
✅ Task revocation (cancelar tareas)

---

## 💡 Tips para Continuar

1. **Después de FASE 3:**
   - Testear con varios usuarios simultáneos
   - Verificar reintentos automáticos
   - Revisar logs de Celery

2. **Antes de FASE 4:**
   - Asegurar que Redis funciona en sistema
   - Tener Docker instalado
   - Tener cuenta en Render o Railway

3. **Debugging:**
   - Ver logs: `tail -f logs/app.log`
   - Monitor Celery: http://localhost:5555
   - Verificar Redis: `redis-cli MONITOR`

---

**Última actualización**: 2026-03-09 (FASE 3 completada)
**Próximo**: FASE 4 Deployment
