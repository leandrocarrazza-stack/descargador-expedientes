# 📋 Descargador de Expedientes Mesa Virtual - Guía de Inicio

## ¿Qué es esto?

Sistema automatizado para descargar expedientes completos de Mesa Virtual STJER (Entre Ríos), unificarlos en un único PDF y convertir automáticamente archivos RTF a PDF.

## 🎯 Características Principales

- ✅ **Descarga automática de TODOS los archivos** (no solo la primera página)
- ✅ **Paginación automática** (itera 14+ páginas sin intervención)
- ✅ **Conversión RTF→PDF** automática con LibreOffice
- ✅ **PDF unificado y ordenado** cronológicamente
- ✅ **Descargas confiables** con 3 reintentos automáticos
- ✅ **Validación de integridad** en cada paso
- ✅ **Sin molestas auto-aperturas** de PDFs

## ⚡ Uso Rápido

### 1. Verificar que todo está instalado

```powershell
python verificar_libreoffice.py
# Debería mostrar: ✅ SÍ
```

### 2. Ejecutar

```powershell
cd "C:\Users\leand\OneDrive\Documentos\Cowork-Projects\projects\mesa-virtual\descargador_expedientes"

python test_flujo_final.py
```

### 3. Ingresa datos

```
Ingresa número de expediente (ej: 12881): [TÚ INGRESAS]
[Se abre navegador para 2FA manual]
[Sistema descarga, convierte y unifica automáticamente]
```

### 4. Resultado en 3-5 minutos

```
C:\...\descargador_expedientes\descargas\Expediente_12881_UNIFICADO.pdf ✅
```

## 📊 Capacidades

| Característica | Antes | Ahora |
|---|---|---|
| Archivos descargados | ~10 | **139+** ✅ |
| RTF convertido | ❌ No | **✅ Sí** |
| Expediente completo | ❌ Incompleto | **✅ 100%** |
| Descargas confiables | ❌ Fallos frecuentes | **✅ 3 reintentos** |

## 🚀 Próximos Comandos

```powershell
# Flujo completo (RECOMENDADO)
python test_flujo_final.py

# Solo test de paginación
python test_paginacion.py

# Diagnóstico
python debug_paginacion.py

# Verificar LibreOffice
python verificar_libreoffice.py
```

## 📖 Documentación

- `README.md` ← Estás aquí (guía rápida)
- `SISTEMA_LISTO.md` - Estado actual completo
- `QUICK_START.md` - Uso en 3 pasos
- `PASOS_SIGUIENTES.md` - Checklist
- `INSTALAR_LIBREOFFICE.md` - Instalación

## ✅ Status

```
✅ Autenticación
✅ Búsqueda de expedientes
✅ Paginación automática (NUEVO)
✅ Descarga de archivos (MEJORADO)
✅ Conversión RTF→PDF (NUEVO)
✅ Unificación de PDFs
✅ LibreOffice instalado y verificado
✅ Sistema listo para producción
```

---

**¡Todo listo! Ejecuta `python test_flujo_final.py` para empezar.**
