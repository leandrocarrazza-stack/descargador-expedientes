# STATE.md — Descargador de Expedientes

## 🎯 Project Reference

**App:** Descargador de Expedientes (Mesa Virtual - Entre Ríos)  
**Status:** ✅ En producción en Render  
**URL:** https://descargador-expedientes.onrender.com  
**Rama:** `main` (todo commiteado y pusheado)

---

## 📍 Current Position

**Phase:** Production & Maintenance  
**Last Session:** Sesión 12 (2026-03-29)  
**Days since last session:** 14 días  

---

## ✅ Completed Work (Sesiones 1-12)

### Core Features Implemented:
1. ✅ **Descarga automática de expedientes** — Selenium + OCR (LibreOffice)
2. ✅ **Login Relay por usuario** — 2FA soportado, cookies en BD por usuario
3. ✅ **Pagos Mercado Pago** — integración completa con back_urls y webhooks
4. ✅ **Auth Flask** — sesiones de usuario, logout, indicador de sesión MV

### Recent Fixes (Sesión 12 - 2026-03-29):
- Gunicorn 1 worker → evita pérdida de session_id en 2FA
- Chrome flags de memoria → reduce consumo en Render (512 MB)
- Logout redirige al login → antes mostraba JSON
- MP back_urls generadas dinámicamente (antes hardcodeadas a localhost)
- MP token .strip() → eliminó \n accidental en ACCESS_TOKEN

---

## 📊 Architecture

**Stack:**
- Backend: Flask + SQLAlchemy (Python)
- Frontend: HTML/CSS/JS (templates)
- Scraping: Selenium 4.6+ + Keycloak SSO
- OCR: LibreOffice (batik)
- DB: SQLite (local) / PostgreSQL (Render)
- Hosting: Render (Docker, 512 MB RAM, 1 worker)
- Pagos: Mercado Pago API + webhooks

**Key Modules:**
- `modulos/auth_mv.py` — Login Relay (Selenium + 2FA)
- `modulos/pipeline.py` — Descarga y OCR
- `modulos/mercado_pago.py` — Pagos MP
- `rutas/auth.py` — Auth endpoints
- `rutas/descargas.py` — Download endpoints
- `Dockerfile` — Gunicorn 1 worker + Chrome + LibreOffice

---

## 🔧 Known Constraints

1. **1 worker en Gunicorn** → solo 1 descarga simultánea (suficiente para plan starter)
2. **512 MB RAM en Render** → Chrome + LibreOffice son heavyweights, memoria ajustada
3. **Sin "recordar dispositivo" en Mesa Virtual** → 2FA SIEMPRE requerido
4. **~50 abogados/día** → cada uno accede a SUS PROPIOS expedientes con SU CUENTA

---

## 🔧 Mejoras Implementadas - Sesión 13 (2026-04-12)

### Problema Diagnosticado
- Error 502 en expedientes extensos (377+ páginas, 30+ MB de PDFs)
- Causa: proxy de Render cancela requests que tardan >60s
- Expedientes extensos tardan 10-15+ minutos en completarse

### Solución 1: Descarga Asincrónica con Polling ✅
- POST `/descargas/expediente` ahora retorna job_id inmediatamente (HTTP 202)
- Pipeline ejecuta en background thread (no bloquea)
- Frontend hace polling para saber cuándo termina
- **Resultado:** Elimina timeout del proxy (no hay requests largas)

### Solución 2: Long-Polling (Mejor) ✅ 
- Reemplazó polling constante (cada 3s) por long-polling eficiente
- Servidor retiene requests hasta que job complete (máx 5 minutos)
- Threading.Event despierta el request apenas termina el pipeline
- **Ventajas:**
  - 1 request vs. 60+ requests (10 minutos)
  - Respuesta inmediata cuando termina
  - Menos carga en servidor y red
  - No hay timeout en Render

### Status Actual
- ✅ App en producción con long-polling
- ✅ Expedientes extensos funcionan sin timeout
- ✅ Código más eficiente y escalable

---

## 📝 Session Continuity

**Last worked on:** Sesión 13 - Implementación de long-polling para timeout 502  
**Hoy:** 2026-04-12
**Próximo:** Esperar deploy de Render y verificar que expedientes extensos funcionan

