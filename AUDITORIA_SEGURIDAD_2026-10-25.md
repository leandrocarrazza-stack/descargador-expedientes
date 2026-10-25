# Auditoría de seguridad de Foja — 2026-10-25 (Fase 1, solo lectura)

Alcance: repo `descargador_expedientes` (rama `claude/foja-security-audit-7edb10`), sitio público foja.com.ar y fuentes públicas. No se modificó código ni producción. No se imprimió ningún secreto. Las herramientas de auditoría se instalaron en un venv temporal fuera del repo.

**Límites de esta fase (importante):** no tuve acceso al panel de Render ni a sus logs, así que *no pude* confirmar la versión exacta de Python en producción, la fecha del último build, las variables de entorno reales, el auto-deploy ni conexiones salientes. Donde eso pesa, el veredicto es "Indeterminado" y lo digo.

## 1. Veredicto ejecutivo

1. **PHP: NO afectada.** No hay PHP en el repo, ni en la imagen (`python:3.11-slim` + Chrome + LibreOffice-writer + qpdf + ghostscript), y foja.com.ar responde 404 en `/wp-login.php`, `/index.php`, `/phpinfo.php`, `/xmlrpc.php`; sirve `gunicorn` detrás de Cloudflare, sin `X-Powered-By`.
2. **Python: sí conviene actualizar, sin urgencia de emergencia.** Producción usa `python:3.11-slim` (parche flotante: depende de la fecha del último build; la última es 3.11.17). Ninguna CVE del intérprete es alcanzable en el código de Foja (no hay servidor TLS propio, ni `zipfile`/`tarfile`). El motivo real es ordenar: 3.11 sale de soporte en oct-2027 y reconstruir la imagen trae los parches.
3. **Dependencias: ahí está el riesgo real.** `pip-audit` encontró 43 vulnerabilidades en 11 paquetes (cryptography 42.0.5, requests 2.31.0, flask-cors 5.0.0, gunicorn 21.2.0, werkzeug 3.1.8, selenium 4.12.0, PyPDF2 3.0.1, pdfminer-six transitivo, y herramientas de desarrollo instaladas en producción). Flask ya está en 3.1.3 (OK).
4. **Compromiso por cadena de suministro: sin indicios.** Ningún paquete comprometido (litellm, lightning, telnyx, xinference, Hades) aparece en `requirements.txt` ni en sus 7 commits de historial; todo está fijado con `==`; no hay `.pth` extraños en el Python de desarrollo. **No pude revisar el entorno de Render ni sus logs**, así que no puedo descartar nada sobre producción más allá de lo que muestra el repo. Rotación de secretos: no indicada por esta evidencia.
5. **Discrepancias con tu resumen:** ver sección 5.

## 2. Tabla de hallazgos

Severidad = criterio mío según el aviso y el uso en Foja (no solo el CVSS). "Alcanzable" = si el código vulnerable se ejecuta en Foja.

| # | Componente | Actual | Corregida | Severidad | Alcanzable | Acción |
|---|---|---|---|---|---|---|
| 1 | PHP CVE-2026-109618/109619 | no hay PHP | 8.5.12 / 8.4.27 / 8.3.36 / 8.2.35 | — | **No** | Ninguna |
| 2 | cryptography (cifra credenciales STJER con Fernet) | 42.0.5 (11 avisos: CVE-2024-12797, CVE-2026-26007, 34073, 69248, 69249, GHSA-h4gh…, GHSA-537c…) | 49.0.0 | Alta | Indeterminado (Fernet casi seguro no usa los módulos afectados; OpenSSL empaquetado sí es relevante) | Actualizar y probar descifrado de datos existentes |
| 3 | requests | 2.31.0 (CVE-2024-35195, 2024-47081, 2026-25645) | 2.33.0 | Media | Sí (se usa en MP y STJER) | Actualizar |
| 4 | Flask-CORS | 5.0.0 (CVE-2024-6866, 6844, 6839) | 6.0.0 | Media | Sí (hay `CORS_ALLOWED_ORIGINS`) | Actualizar; revisar config |
| 5 | gunicorn | 21.2.0 (CVE-2024-1135, 2024-6827) | ≥22.0.0 | Media | Parcial (request smuggling; hay Cloudflare/Render delante) | Actualizar |
| 6 | Werkzeug | 3.1.8 (CVE-2026-102598) | 3.1.9 | Media (detalle no verificado) | Indeterminado | Actualizar |
| 7 | selenium | 4.12.0 (CVE-2023-5590) | ≥4.14.0 | Media | Indeterminado | Actualizar y probar scraping |
| 8 | PyPDF2 3.0.1 (CVE-2023-36464) | abandonado | usar `pypdf` | Media + mantenimiento | Sí (arma PDFs) | Migrar a `pypdf` |
| 9 | pdfminer-six (vía pdfplumber 0.11.0) | 20231228 (2 avisos) | ≥20251230 | Media | Sí si se parsean PDFs externos | Actualizar pdfplumber |
| 10 | python-dotenv | 1.0.0 (CVE-2026-28684) | 1.2.2 | Baja | Poco | Actualizar |
| 11 | pytest, black, mypy, flake8, etc. | en `requirements.txt` de producción (pytest CVE-2025-71176, black CVE-2024-21503 y otros) | — | Baja | No (no se ejecutan) | Separar `requirements-dev.txt` |
| 12 | Flask | 3.1.3 | 3.1.3 | — | — | OK. CVE-2026-27205 es de severidad **baja** (falta `Vary: Cookie`), ya corregido |
| 13 | certifi / urllib3 / idna / Jinja2 / Pillow | **sin fijar** (transitivos): hoy resuelven a certifi 2026.10.23, urllib3 2.8.0, Jinja2 3.1.6, Pillow 13.0.0, sin avisos | — | Media (reproducibilidad) | — | Fijar con hashes |
| 14 | Python 3.11 (prod, `python:3.11-slim`) | 3.11.x flotante (≤3.11.17) | 3.11.17 o 3.13.17 | Baja-Media | No (ver §3) | Reconstruir imagen; evaluar 3.13 |
| 15 | Python dev local | 3.13.4 | 3.13.17 | Baja | No | Actualizar |
| 16 | Chrome | `google-chrome-stable` sin versión, queda la del día del build | última estable | Media (el navegador abre sitio externo) | Sí | Rebuild periódico |
| 17 | chromedriver | Selenium Manager lo baja en runtime; además `selenium_pool.py` importa `webdriver_manager` 3.9.1 | — | Baja-Media | Sí | Descargas sin verificar de integridad; ver plan |
| 18 | Sesión de Mesa Virtual con `pickle.load` (`modulos/login.py:143`) | — | — | Baja | Solo si alguien escribe en `/data` | Preferir JSON |
| 19 | Contenedor corre como root con `--no-sandbox` | — | — | Baja-Media | Sí | Usuario no root cuando sea viable |

Notas de método: la primera pasada de `pip-audit` fue sobre los pines directos (39 avisos). La segunda resolvió las 134 dependencias totales y sumó los 4 de pdfminer-six; para ella se excluyó `psycopg2-binary` porque 2.9.9 no compila en Python 3.13 (relevante para la migración, ver plan).

## 3. Alcanzabilidad de las CVE del intérprete (verificado en el código)

Búsqueda en todo el repo (salvo tests) de `ssl`, `SSLContext`, `zipfile`, `tarfile`, `shutil.unpack`, `urllib.request`, `HTTPPasswordMgr`, `webbrowser`, `xml`:

| CVE (3.11.17) | Qué es | Alcanzable | Evidencia |
|---|---|---|---|
| 2026-19445 | UAF en `SSLContext` servidor con `sni_callback` | **No** | La app no crea servidores TLS; gunicorn atiende HTTP plano detrás del proxy. `ssl` solo aparece en `scripts/enviar_promo.py:196` (cliente SMTP) |
| 2026-15310 | `zipfile`: descompresión sin límite (bzip2/LZMA) | **No** | Sin `zipfile` en el código |
| 2026-19672, 82049, 87910 | `tarfile`: filtros de extracción | **No** | Sin `tarfile` |
| 2026-19553 | `SSLContext.wrap_bio()`/asyncio: validación | **No** | Sin uso de `wrap_bio` ni asyncio de red |
| 2026-15806 | `urllib.request` HTTPPasswordMgr: credenciales por esquema | **No** | Sin `urllib.request`; se usa `requests` |
| 2026-17084 | stringprep/IDNA acotados | Indeterminado, bajo | Lo usa indirectamente cualquier resolución de dominios |
| `xml.etree` | `modulos/plazos/sync_stjer.py:27` parsea XML del STJER | Relevante a futuro | No está en la lista, pero es entrada externa; se beneficia del parche de expat |

**Serie objetivo recomendada:** 3.13 (3.13.17, soporte de seguridad hasta 2029-10). Para ir a 3.13 hay que subir `psycopg2-binary` (2.9.9 no tiene rueda para 3.13) y probar `celery 5.3.1`, `flask-limiter 3.5.0`, `anthropic 0.30.0`. Opción más conservadora: quedarse en 3.11.17 ahora y migrar luego.

## 4. Cadena de suministro y entorno

- **Cruce con paquetes comprometidos** (litellm 1.82.7/1.82.8, lightning 2.6.2/2.6.3, telnyx 4.87.1/4.87.2, xinference 2.6.0–2.6.2, campaña Hades): sin coincidencias en `requirements.txt` ni en `git log -p` de sus 7 versiones. No hay líneas sin `==` en el historial.
- **Typosquatting:** los nombres directos de `requirements.txt` corresponden a paquetes legítimos conocidos (revisión visual, no automática). El riesgo está en los transitivos sin fijar (134 dependencias en total al resolver), que se resuelven en cada build.
- **Indicadores de compromiso (IOC):** el Python de desarrollo no tiene archivos `.pth` en `site-packages`; no tiene litellm/lightning/telnyx/xinference. **No inspeccioné la imagen de producción ni los logs de Render**: queda pendiente que revises en el panel conexiones salientes anómalas y la fecha del último build (si es anterior a mar-2026 o posterior, igual no trae esos paquetes porque Foja no depende de ellos).
- **Medidas preventivas:** pines con hashes (`pip-compile --generate-hashes` + `pip install --require-hashes`), `pip-audit` en CI, Dependabot o Renovate, y no instalar herramientas de desarrollo en la imagen de producción.

## 5. Discrepancias entre tu resumen y las fuentes primarias

- **PHP:** `php.net/ChangeLog-8.php` fecha 8.5.12 y 8.4.27 el **22/10/2026**, no el 21. Esas dos entradas citan los avisos GHSA-w8hw-w9fx-j79w y GHSA-6wmf-4rv5-p3rp ("Avoid state corruption via bailout"), **no los CVE-2026-109618/109619**. No pude verificar 8.3.36 ni 8.2.35 (la página es enorme y quedó truncada). Hay fuentes que dicen que MITRE/NVD aún no registraban esos IDs. Las versiones afectadas que da HeroDevs (<8.2.35, 8.3.0–8.3.35, 8.4.0–8.4.26, 8.5.0–8.5.11) **no** incluyen 5.x ni 7.x. Tratar los detalles técnicos como no confirmados. Para Foja es irrelevante igual.
- **Python:** python.org lista **3.14.9** (21/10), no 3.14.8; 3.13.17 salió el mismo día junto a 3.15.1. 3.12.15 es del 30/09, 3.11.17 del 01/10. La lista de CVE de 3.11.17 incluye 19445, 15310, 15806, 17084, 19553, 19672, 82049 y además 87910.
- **CVE-2026-19445:** el CVSS 9.2 lo reporta solo Action1; OSV, SUSE y Mend difieren en rangos. **CVE-2026-15310** es de severidad baja (NVD 2.1 / SUSE 3.1), no grave.
- **Flask CVE-2026-27205:** severidad baja (CVSS v4 2.3), corregido en 3.1.3.
- **Hades:** Socket cuenta 19 paquetes (37 wheels); Orca cuenta 26. Telnyx es 4.87.1 y 4.87.2. Lightning 2.6.2/2.6.3 salió el 30/04. La atribución a TeamPCP está disputada.
- **eMudhra/emSign:** confirmado en discuss.python.org y python.org: 3.13.17 trae pip 26.2.2 y certifi 2026.10.19. Importante: pip usa su certifi vendorizado, así que hay que actualizar pip en la imagen, no solo certifi.

## 6. Plan de actualización priorizado (para Fase 2, requiere tu confirmación)

Todo en una rama aparte, con tests y smoke test (login, scraping Mesa Virtual con Selenium, armado de PDF, webhook de Mercado Pago, descifrado de credenciales existentes).

**P0 — hoy/esta semana (bajo riesgo):**
1. Dockerfile: `FROM python:3.11.17-slim` (o 3.13.17-slim, ver P2) y `RUN pip install --upgrade "pip>=26.2.2"` antes de instalar dependencias. Reconstruir imagen.
2. Dependencias de bajo riesgo: `requests==2.33.0`, `Werkzeug==3.1.9`, `gunicorn==23.0.0` (≥22), `python-dotenv==1.2.2`, `selenium` ≥4.14 (probar versión estable reciente).
3. Separar `requirements-dev.txt` (pytest*, black, mypy, flake8, isort) y que el Dockerfile solo instale `requirements.txt`.

**P1 — esta semana, con pruebas:**
4. `cryptography==49.0.0`. Riesgo: salto de major; probar que descifra datos ya guardados (`ENCRYPTION_KEY` existente) antes de desplegar.
5. `Flask-CORS==6.0.0` (cambió el matching de rutas/orígenes: verificar que foja.com.ar siga permitido).
6. Reemplazar `PyPDF2` por `pypdf` (cambios de import y nombres de API menores); subir `pdfplumber` para traer `pdfminer-six` ≥20251230.
7. Fijar con hashes: `pip-compile --generate-hashes -o requirements.lock requirements.txt` y usar `pip install --require-hashes -r requirements.lock` en el Dockerfile.

**P2 — próximas semanas:**
8. CI: `pip-audit -r requirements.lock` en GitHub Actions + Dependabot para `pip` y `docker`.
9. Evaluar Python 3.13.17: requiere `psycopg2-binary` ≥2.9.10 (o `psycopg` 3) y probar celery/flask-limiter/anthropic; subir `anthropic` (0.30.0 es muy viejo).
10. Quitar `webdriver_manager` si ya no se usa (Selenium Manager basta); rebuild semanal para traer parches de Chrome.
11. Contenedor sin root cuando se pueda; reemplazar `pickle` de sesión por JSON.

**Cloud Run Jobs / PostgreSQL (implicancias de seguridad):** secretos en Secret Manager, no en variables planas; cuenta de servicio de mínimo privilegio; Cloud SQL con IP privada o conector con IAM/SSL; sin disco persistente local en Cloud Run (la sesión de Mesa Virtual de `/data` y el `pickle` hay que moverlos a Secret Manager/GCS cifrado); Chrome con `--no-sandbox` en un contenedor efímero es más aceptable si el job no recibe tráfico entrante. Pasar de SQLite a PostgreSQL elimina el archivo de BD en disco pero exige TLS y credenciales rotables.

## 7. Infraestructura (lo que se pudo ver desde el repo y el sitio)

- `SECRET_KEY` con `generateValue: true`; los demás secretos se cargan a mano en el panel (no están en el repo). Bien.
- Respuesta pública: CSP, `X-Frame-Options: DENY`, `nosniff`, cookie de sesión `Secure; HttpOnly; SameSite=Lax`. Bien. La CSP permite `'unsafe-inline'` en scripts, lo que debilita la protección ante XSS.
- `.github/workflows/restart-diario.yml` usa `RENDER_API_KEY` como secreto de GitHub: rotar si alguna vez se expuso, y limitar su alcance.
- No pude verificar: auto-deploy, puertos extra, variables expuestas en Render, ni la versión de Python configurada (el servicio es Docker, así que manda el Dockerfile).

## 8. Fuentes consultadas

- PHP changelog: https://www.php.net/ChangeLog-8.php
- Rapid7 sobre CVE-2026-109618/109619: https://www.rapid7.com/blog/post/etr-cve-2026-109618-and-cve-2026-109619-critical-unauthenticated-remote-code-execution-in-php/
- Centro Canadiense de Ciberseguridad AL26-026: https://cyber.gc.ca/en/alerts-advisories/al26-026-critical-vulnerabilities-impacting-php-cve-2026-109618-cve-2026-109619
- HeroDevs CVE-2026-109619: https://www.herodevs.com/vulnerability-directory/cve-2026-109619
- Descargas Python: https://www.python.org/downloads/
- Python 3.11.17: https://www.python.org/downloads/release/python-31117/
- Python 3.13.17: https://www.python.org/downloads/release/python-31317/
- Python 3.14.9: https://www.python.org/downloads/release/python-3149/
- Incidente eMudhra/emSign: https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576
- OSV CVE-2026-19445: https://osv.dev/vulnerability/CVE-2026-19445
- Flask CVE-2026-27205 (Debian/NVD): https://nvd.nist.gov/vuln/detail/cve-2026-27205
- Reporte de incidente PyPI litellm/telnyx: https://blog.pypi.org/posts/2026-04-02-incident-report-litellm-telnyx-supply-chain-attack/
- Datadog sobre litellm: https://securitylabs.datadoghq.com/articles/litellm-compromised-pypi-teampcp-supply-chain-campaign/
- CSA sobre lightning: https://labs.cloudsecurityalliance.org/research-rb/csa-research-note-pytorch-lightning-supply-chain-ml-framewor/
- Hades (The Hacker News): https://thehackernews.com/2026/06/hades-pypi-attack-19-packages-poisoned.html
- Hades (Orca): https://orca.security/resources/blog/hades-pypi-supply-chain-attack/
- Base de avisos PyPA/OSV vía `pip-audit` (ejecutado el 2026-10-25).

Observación sobre fuentes: algunas búsquedas devolvieron blogs secundarios; los datos de versiones salen de php.net y python.org, y los de paquetes de PyPI/OSV. Lo que no pude contrastar con la fuente primaria (detalle técnico de CVE-2026-109618/109619, CVE-2026-102598 de Werkzeug, alcance de CVE-2023-5590) está marcado como no confirmado.

## 9. Fase 2 aplicada (P0 + P1) — rama `claude/foja-security-audit-7edb10`

Cambios: `Dockerfile` (python 3.11.17-slim, pip ≥26.2.2, instalación con `--require-hashes`), `requirements.txt` (pines actualizados), `requirements.lock` (nuevo, 122 paquetes con hashes, generado para Python 3.11/Linux), `requirements-dev.txt` (nuevo), migración `PyPDF2` → `pypdf` en `modulos/unificacion.py`, `modulos/descarga.py` y `tests/test_anexar_qpdf.py`.

| Paquete | Antes | Ahora |
|---|---|---|
| Werkzeug | 3.1.8 | 3.1.9 |
| Flask-CORS | 5.0.0 | 6.0.5 |
| python-dotenv | 1.0.0 | 1.2.4 |
| selenium | 4.12.0 | 4.20.0 (**no 4.51**: ver abajo) |
| requests | 2.31.0 | 2.33.0 |
| PyPDF2 | 3.0.1 | pypdf 6.21.2 |
| pdfplumber (→ pdfminer-six) | 0.11.0 | 0.11.10 (pdfminer-six 20260107) |
| cryptography | 42.0.5 | 49.0.0 |
| gunicorn | 21.2.0 | 23.0.0 |
| pytest y herramientas dev | en producción | solo en `requirements-dev.txt` |

**Hallazgo durante la prueba:** `selenium` ≥4.27 rompe `RemoteConnection.set_timeout(...)` (`modulos/auth_mv.py:90`) y hacía fallar 22 archivos de test al importar. Se fijó 4.20.0, que ya corrige CVE-2023-5590. Para subir más hay que adaptar ese código.

**Pendiente de aviso:** `pip-audit` sobre el lock marca `cryptography 49.0.0` con PYSEC-2026-3552 (corregido en 50.0.0); afecta a `pkcs7_decrypt_*`, que Foja no usa. Se puede subir a 50.x en otra ronda.

**Verificación:**
- Suite completa de tests, línea base (HEAD sin cambios) vs. después: idénticos, 170 pasan; los mismos 2 fallos y 4 errores existían antes (`test_conversion_lote` necesita LibreOffice en Windows; `test_descarga::test_pipeline` y `test_e2e_completo` necesitan un servidor en localhost:5000 y credenciales reales).
- Smoke test fuera del repo (Flask con BD SQLite temporal y valores de prueba): `/` y `/auth/login` responden 200; CORS permite foja.com.ar y bloquea otros orígenes; webhook de Mercado Pago rechaza firma inválida (400) y acepta firma HMAC válida (200); un token Fernet creado con cryptography 42.0.5 se descifra con 49.0.0; unión de PDFs con `pypdf` omite un archivo corrupto.
- **No verificado:** build de la imagen Docker (el daemon de Docker no estaba corriendo), scraping real contra Mesa Virtual y descargas reales (requieren credenciales y Chrome), y pago real en Mercado Pago.
