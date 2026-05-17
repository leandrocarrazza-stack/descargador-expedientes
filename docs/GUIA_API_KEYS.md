# Guía: Gestionar API Keys y Modelos de Claude

## 🔍 ESTADO ACTUAL

```
❌ .env NO configurado
❌ ANTHROPIC_API_KEY NO en variables globales
➜ Claude Code usa: TU PLAN PERSONAL
```

**Por eso tus desarrollos están consumiendo créditos de tu plan personal.**

---

## ✅ PASO 1: Crear y Configurar .env

### 1.1 Crear archivo `.env`

En el terminal (en la carpeta del proyecto):

```bash
# Windows PowerShell
Copy-Item .env.example .env

# O manualmente: Copia .env.example y renómbralo a .env
```

### 1.2 Agregar tu nueva API key

Abre `.env` y reemplaza:

```
ANTHROPIC_API_KEY=sk-ant-v4-TU_VERDADERA_API_KEY_AQUI
CLAUDE_MODEL=claude-3-5-haiku
```

**⚠️ IMPORTANTE:**
- Tu nueva API key debe comenzar con `sk-ant-v4-`
- Nunca commitees este archivo (está en `.gitignore`)
- Guárdalo con permisos restringidos

---

## 🔎 PASO 2: Verificar Qué API Key Está en Uso

### Opción A: Script de verificación

```bash
python modulos/credenciales.py
```

Salida esperada:
```
══════════════════════════════════════════════════════════════════
  🔐 VERIFICACIÓN DE CREDENCIALES
══════════════════════════════════════════════════════════════════

✅ ANTHROPIC_API_KEY: sk-ant-v4-...xD9mKl
✅ CLAUDE_MODEL: claude-3-5-haiku

✅ Credenciales configuradas correctamente
```

### Opción B: En Python (interactivo)

```python
import os
from modulos.credenciales import obtener_api_key_anthropic

# Cargar variables
api_key = obtener_api_key_anthropic()
if api_key:
    # Mostrar primeros caracteres
    print(f"API Key: {api_key[:20]}...{api_key[-10:]}")
else:
    print("❌ No hay API key configurada")
```

---

## 💰 COMPARATIVA DE MODELOS Y COSTOS

### Para DESARROLLO (recomendado)

| Modelo | Costo | Velocidad | Casos de Uso |
|--------|-------|-----------|--------------|
| **claude-3-5-haiku** | 🟢 MÁS BARATO | Rápido | Testing, desarrollo |
| **claude-3-5-sonnet** | 🟡 Medio | Normal | Producción equilibrada |
| **claude-opus-4-6** | 🔴 MÁS CARO | Lento | Tareas complejas |

### 📊 Precios aproximados (tokens)

```
Haiku:  $0.80 / M input,  $4.00 / M output
Sonnet: $3.00 / M input,  $15.00 / M output
Opus:   $15.00 / M input, $75.00 / M output
```

### Mi recomendación para tu proyecto

**DURANTE DESARROLLO (ahora):**
```
CLAUDE_MODEL=claude-3-5-haiku
```
- ✅ 5-10x más barato que Opus
- ✅ Suficiente para desarrollo y testing
- ✅ Perfecto para análisis de documentos
- ✅ Rápido (buena UX durante testing)

**PARA PRODUCCIÓN (luego):**
```
CLAUDE_MODEL=claude-3-5-sonnet
```
- ✅ Balance costo/calidad
- ✅ Mejor para usuarios finales
- ✅ Recomendado por Anthropic

---

## 🚀 CÓMO USAR LA API KEY EN EL CÓDIGO

### Ejemplo 1: Usar en un módulo Python

```python
from modulos.credenciales import obtener_api_key_anthropic, obtener_modelo_claude
from anthropic import Anthropic

# Obtener credenciales (desde .env o variables del sistema)
api_key = obtener_api_key_anthropic()
modelo = obtener_modelo_claude()

if not api_key:
    print("❌ API key no configurada")
    exit(1)

# Usar con Anthropic SDK
cliente = Anthropic(api_key=api_key)

response = cliente.messages.create(
    model=modelo,
    max_tokens=1024,
    messages=[
        {"role": "user", "content": "Analiza este documento..."}
    ]
)

print(response.content[0].text)
```

### Ejemplo 2: En Flask (servidor web)

```python
from flask import Flask
from modulos.credenciales import obtener_api_key_anthropic

app = Flask(__name__)

# Cargar API key una sola vez
API_KEY = obtener_api_key_anthropic()

if not API_KEY:
    raise Exception("❌ API_KEY no configurada. Configura .env")

@app.route('/api/analizar', methods=['POST'])
def analizar_documento():
    from anthropic import Anthropic

    cliente = Anthropic(api_key=API_KEY)
    # ... tu lógica
```

---

## ⚠️ SEGURIDAD: Dónde SE GUARDAN los secretos

### ✅ SEGURO

```
.env          ← Archivo local, no se commitea
Variables OS  ← Configuradas en el sistema
Secrets env   ← En producción (Docker, Heroku, etc)
```

### ❌ INSEGURO

```
Hardcodeado en código       ← NUNCA
Archivos .txt sin cifrar    ← NUNCA
Committed a git             ← NUNCA
Logs públicos               ← NUNCA
```

---

## 📋 CHECKLIST: Antes de empezar desarrollo

- [ ] Tengo mi nueva API key (empieza con `sk-ant-v4-`)
- [ ] Copié `.env.example` → `.env`
- [ ] Agregué la API key en `.env`
- [ ] Ejecuté `python modulos/credenciales.py` y veo ✅
- [ ] Decidí usar `claude-3-5-haiku` para desarrollo
- [ ] Confirmé que NO estoy commitiendo `.env` (reviso `.gitignore`)

---

## 🎯 PRÓXIMO PASO

Una vez configurado, puedo:

1. **Agregar procesamiento con Claude** al descargador
   - Resumir expedientes automáticamente
   - Extraer palabras clave
   - Clasificar documentos

2. **Implementar seguridad de la app web** (Opción B)
   - Con tu nueva API key para análisis de documentos
   - Autenticación de usuarios
   - Deployment en producción

**¿Necesitas ayuda para configurar el `.env`? Dime cuándo lo hayas hecho y verificamos.**
