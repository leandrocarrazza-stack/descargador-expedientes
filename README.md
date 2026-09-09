# 📋 Descargador de Expedientes — Mesa Virtual (Entre Ríos)

**Herramienta profesional para descargar y unificar expedientes de la Mesa Virtual del Poder Judicial de Entre Ríos**

---

## ✨ Características

- ✅ **Automatización completa** — Login, búsqueda, descarga y unificación
- ✅ **PDF unificado** — Todos los movimientos en un único archivo
- ✅ **Logging estructurado** — Trazabilidad completa en archivo y consola
- ✅ **Manejo de errores tipificado** — 8 excepciones personalizadas
- ✅ **Type-safe** — 90% de cobertura de type hints con mypy
- ✅ **Bien testeado** — 77 tests unitarios (100% pass)
- ✅ **Production-ready** — Código profesional y documentado

---

## 🌐 Variables de entorno (app web Flask)

Además del uso como CLI (`main.py`), el proyecto corre como app web
(`servidor.py`) con las variables de siempre (`SECRET_KEY`, `DATABASE_URL`,
`MERCADO_PAGO_*`, `MAIL_*`, `ENCRYPTION_KEY`, ver `.env.example` para el
detalle completo) más estas, agregadas junto con la actualización
incremental y el aviso por email:

| Variable | Para qué | Default si falta |
|---|---|---|
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET` | Storage persistente de PDFs en Cloudflare R2 (`modulos/storage.py`) | Cae a un backend local en disco (sin persistencia real entre deploys) |
| `PDF_STORE_DIR` | Carpeta del backend local de storage, si R2 no está configurado | `PROJECT_DIR/pdf_store` |
| `RETENCION_PDF_DIAS` | Días desde el último acceso que se conserva un PDF en el storage | `180` |
| `BASE_URL` | Dominio público, para armar links absolutos en emails (el aviso de descarga corre en un thread sin request activo) | `https://foja.com.ar` |
| `PDF_TTL_HOURS` | Horas que un PDF permanece en el caché local `output/` (ya no se borra al descargarlo — ver storage persistente arriba) | `24` |

Sin las credenciales de R2, la app funciona igual (cae al backend local),
pero en producción eso significa que los PDFs se acumulan en el disco
persistente sin límite de tamaño — ver el warning que tira
`modulos/storage.py` al arrancar en ese caso.

---

## 🧩 Actualización incremental y mejoras del servicio

Este proyecto pasó por una ronda de mejoras organizadas en lotes (rama
`claude/foja-service-improvements-em4pal`), cada uno con su propio commit:

0. **Fundaciones** — migraciones ligeras (`modulos/migraciones.py`, agrega
   columnas a tablas existentes sin Alembic) + storage R2/local + retención de PDFs.
1. **Admin** — borrar mensajes de contacto leídos; el formulario de
   otorgar créditos queda sólo en la tabla de usuarios.
2. **Mi cuenta** — nombre, contraseña, preferencia de aviso por email (`/cuenta/`).
3. **Aviso por email** — al terminar una descarga, con link al PDF.
4. **Página principal unificada** — Descargar es la página de entrada (ya
   no hay un "Mi panel" separado); nav reordenada.
5. **Actualización incremental** — para un expediente ya descargado (planes
   Estudio/Matrícula), baja solo los movimientos nuevos y los une al PDF
   anterior (`POST /descargas/expediente/<id>/actualizar`). Ver el
   docstring de `EstrategiaIncremental` en `modulos/descarga.py` para el
   detalle de cómo ancla filas nuevas contra la descarga previa.
6. **Correcciones colaterales** — historial de compras, reset mensual de
   créditos usados, limpieza de scripts.

Pasos manuales que no hace ningún script:
- Crear el bucket en Cloudflare R2 y cargar sus credenciales en Render.
- Correr `python scripts/backfill_plan_max.py` una vez, para que los
  usuarios que ya habían comprado Estudio/Matrícula antes de este cambio
  tengan la actualización incremental habilitada sin esperar su próxima compra.
- Subir `PDF_TTL_HOURS` a 48 en el panel de Render (ya viene en `render.yaml`).

---

## 🚀 Instalación Rápida

```bash
# 1. Instalar dependencias
pip install -r requirements-test.txt --break-system-packages

# 2. Instalar pre-commit hooks (previene commits con errores)
python3 -m pre_commit install

# 3. ¡Listo!
```

---

## 💻 Uso

```bash
# Ejecutar el descargador
python3 main.py

# Ingresa: número de expediente (ej: "21/24")
# Salida: Expediente_21_24_UNIFICADO.pdf
```

---

## 🧪 Testing

```bash
# Ejecutar todos los tests (77 tests, 100% pass)
python3 -m pytest tests/ -v

# Ver resultado esperado:
# 77 passed in 0.62s ✅
```

---

## 🔍 Validación de Calidad

```bash
# Validar type hints (90% coverage)
python3 -m mypy modulos/ --cache-dir=/tmp/mypy_cache

# Validar estilo PEP 8
python3 -m flake8 modulos/ --max-line-length=100

# Formatear código automáticamente
python3 -m black modulos/ main.py config.py --line-length=100
```

---

## 📊 Arquitectura

### Pipeline Orquestador
```
PipelineDescargador.ejecutar("21/24")
├── _paso_autenticacion()
├── _paso_busqueda()
├── _paso_descarga()
├── _paso_conversion()
├── _paso_unificacion()
└── _paso_limpieza()
```

### Modelos Tipificados (7 Dataclasses)
- NumeroExpediente, Expediente, Movimiento, Archivo
- EstadoPipeline, ResultadoPipeline

### Excepciones Personalizadas (8)
- MesaVirtualError (base)
- ErrorAutenticacion, ErrorBusqueda, ErrorDescarga
- ErrorConversion, ErrorUnificacion, ErrorConfiguracion, ErrorValidacion

---

## 📁 Estructura

```
.
├── main.py                       # Punto de entrada
├── config.py                     # Configuración
├── modulos/                      # Lógica principal
│   ├── logger.py
│   ├── excepciones.py
│   ├── modelos.py
│   ├── pipeline.py
│   └── ... (5 más)
├── tests/                        # 77 tests (100% pass)
├── .pre-commit-config.yaml       # Pre-commit hooks
└── README.md                     # Este archivo
```

---

## 📝 Logging

```bash
# Ver logs en tiempo real
tail -f logs/descargador.log

# Niveles: DEBUG, INFO, WARNING, ERROR
```

---

## 👨‍💻 Desarrollo

### Pre-commit Hooks (Automático)

Antes de cada commit:
1. ✅ Black (formato)
2. ✅ isort (imports)
3. ✅ flake8 (estilo)
4. ✅ mypy (tipos)

```bash
git commit -m "Mi cambio"
# Si hay errores → rechaza commit
# Si está bien → acepta commit
```

---

## 📚 Documentación Adicional

- **ESTADO_FINAL_PROYECTO.md** — Arquitectura completa
- **VALIDACION_TIPOS_RESULTADOS.md** — Análisis mypy
- **LINTING_RESULTADOS.md** — Análisis flake8
- **RESUMEN_SESION_CALIDAD.md** — Mejoras implementadas
- **TESTING_RESULTADOS.md** — Detalles de tests

---

## ✅ Checklist Inicial

- [ ] Instalar: `pip install -r requirements-test.txt`
- [ ] Pre-commit: `python3 -m pre_commit install`
- [ ] Tests: `pytest tests/ -v` (77/77)
- [ ] Usar: `python3 main.py`
- [ ] Logs: `tail -f logs/descargador.log`

---

**Estado:** ✅ Production-Ready
**Tests:** 77/77 (100%)
**Type Coverage:** 90%
**Última Actualización:** 2026-03-08

¡El proyecto está listo para usar! 🚀
