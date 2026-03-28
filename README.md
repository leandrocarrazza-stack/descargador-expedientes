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
