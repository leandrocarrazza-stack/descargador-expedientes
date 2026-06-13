# Foja — Descargador de Expedientes

Herramienta web para descargar y unificar expedientes del Poder Judicial de Entre Ríos (Mesa Virtual).

---

## Qué hace

- El usuario conecta su cuenta de Mesa Virtual (login con usuario, contraseña y 2FA).
- Ingresa el número de expediente.
- La app descarga todos los movimientos y los unifica en un PDF.
- Modelo de créditos prepagados: cada descarga consume 1 crédito.
- Pagos con Mercado Pago (Checkout Pro, ARS).

---

## Pila tecnológica

- **Backend:** Python 3.11, Flask, SQLAlchemy, PostgreSQL (producción) / SQLite (desarrollo)
- **Scraping:** Selenium + Chrome headless + Keycloak 2FA
- **PDF:** LibreOffice (RTF→PDF), PyPDF2 (unificación), Ghostscript (compresión opcional)
- **Deploy:** Docker, Gunicorn (1 worker), Render.com

---

## Arrancar en desarrollo local

```bash
# 1. Clonar y crear entorno virtual
python3 -m venv .venv && source .venv/bin/activate

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Copiar y completar variables de entorno
cp .env.example .env
# Editar .env: completar ENCRYPTION_KEY al menos
# python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

# 4. Iniciar la app
python servidor.py
# → http://localhost:5000
```

La app arranca con SQLite local (`app.db`) sin configurar `DATABASE_URL`.

---

## Variables de entorno críticas

| Variable | Obligatoria en producción | Descripción |
|----------|--------------------------|-------------|
| `SECRET_KEY` | Sí | Clave de sesiones Flask (generar con `secrets.token_hex(32)`) |
| `DATABASE_URL` | Sí | URL de PostgreSQL |
| `ENCRYPTION_KEY` | Sí | Cifrado Fernet para sesiones de Mesa Virtual |
| `MERCADO_PAGO_ACCESS_TOKEN` | Sí | Token de Mercado Pago |
| `MERCADO_PAGO_WEBHOOK_SECRET` | Sí | Validación HMAC de webhooks |
| `CORS_ALLOWED_ORIGINS` | Sí | Dominio(s) permitidos para CORS |
| `JURISPRUDENCIA_ENABLED` | No | `true` para habilitar el módulo de jurisprudencia (en desarrollo) |

Ver `.env.example` para la lista completa.

---

## Estructura del proyecto

```
servidor.py          # Entry point — crea la app Flask (factory pattern)
config.py            # Configuración y variables de entorno
modulos/
  models.py          # Modelos SQLAlchemy (User, ExpedienteDescargado, CompraCreditos, ...)
  modelos.py         # Dataclasses del pipeline (Expediente, Movimiento, Archivo)
  auth.py            # Hashing, validación y creación de usuarios
  auth_mv.py         # Login relay de Mesa Virtual (Selenium + Keycloak 2FA)
  pipeline.py        # Orquesta la descarga completa
  navegacion.py      # Búsqueda de expedientes vía Selenium
  descarga.py        # Descarga de archivos del expediente
  login.py           # ClienteSelenium (fallback dev local)
  conversion.py      # RTF → PDF (LibreOffice)
  unificacion.py     # Unificación de PDFs
  compresion.py      # Compresión Ghostscript (opcional)
  mercado_pago.py    # API de Mercado Pago
  extensions.py      # Instancias compartidas (limiter, csrf, mail)
  database.py        # Inicialización de SQLAlchemy
  jurisprudencia/    # Módulo de jurisprudencia STJER (en desarrollo, deshabilitado)
rutas/
  auth.py            # /auth/login, /auth/signup, /auth/mv-login, /auth/mv-2fa, ...
  descargas.py       # /descargas/expediente (async con threading + long-polling)
  pagos.py           # /pagos/crear-orden, /pagos/webhook, ...
  admin.py           # /admin/ (requiere is_admin)
  contacto.py        # /contacto
  jurisprudencia.py  # /jurisprudencia/* (solo activo si JURISPRUDENCIA_ENABLED=true)
templates/           # Jinja2 + Bootstrap 5
static/              # CSS y JS propios
scripts/
  set_admin.py               # Dar permisos admin a un usuario
  encrypt_existing_cookies.py  # Migrar sesiones MV a cifrado Fernet
data/jurisprudencia/ # Tesauro STJER (JSON)
```

---

## Deploy en Render

El archivo `render.yaml` y el `Dockerfile` están listos. Pasos:

1. Crear una cuenta en [render.com](https://render.com) y conectar el repo.
2. Render detecta `render.yaml` automáticamente.
3. Configurar las variables de entorno en el panel de Render (las marcadas como "Sí" arriba).
4. Deploy.

El disco persistente (`/data/sesion.pkl`) se usa solo para el fallback de desarrollo; en producción las sesiones van a PostgreSQL.

---

## Módulo de jurisprudencia (en desarrollo)

El módulo `/jurisprudencia` (buscador de fallos STJER con chat conversacional vía Claude API) está **deshabilitado por defecto**. Para activarlo en desarrollo local:

```bash
JURISPRUDENCIA_ENABLED=true python servidor.py
```

No habilitar en producción hasta que el módulo esté completo.
