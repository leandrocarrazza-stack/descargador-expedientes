# 🔒 Estrategia de Desarrollo Seguro - Jurisprudencia

## Situación Actual

✅ **BUENA NOTICIA:** Todo está seguro por ahora

```
main                          (PRODUCCIÓN - NO TOCAR)
 └─ commits anteriores (funcionales)

claude/download-email...      (FEATURE BRANCH - DONDE ESTAMOS)
 └─ commit: "Integrar módulo jurisprudencia"
    ✓ Servidor levanta sin errores
    ✓ Blueprints registrados correctamente
    ✓ Tesauro cargado
    ✓ BD operacional
    ✓ No rompió funcionalidad existente
```

---

## 🛡️ Reglas Básicas de Seguridad

### ✅ PUEDES HACER (Sin peligro)

1. **Testear en la rama actual**
   ```bash
   python servidor.py
   curl http://localhost:5000/jurisprudencia/
   ```

2. **Hacer commit con cambios de testing/docs**
   ```bash
   git add JURISPRUDENCIA_TESTING.md
   git commit -m "docs: agregar guía de testing"
   git push origin claude/download-email-attachments-h1Ryc
   ```

3. **Hacer cambios pequeños en la rama**
   - Arreglar typos
   - Mejorar logs
   - Agregar comentarios
   - Ajustar configuración

### ❌ NO HAGAS ESTO (Peligroso)

1. **No mergees a `main` aún**
   ```bash
   # ❌ PROHIBIDO
   git checkout main
   git merge claude/download-email-attachments-h1Ryc
   ```

2. **No cambies código en `main` directamente**
   ```bash
   # ❌ PROHIBIDO
   git checkout main
   git add .
   git commit -m "..."
   ```

3. **No hagas git reset/force push**
   ```bash
   # ❌ PROHIBIDO
   git reset --hard HEAD~1
   git push -f origin claude/download-email-attachments-h1Ryc
   ```

4. **No comittees credenciales reales**
   ```bash
   # ❌ PROHIBIDO EN ARCHIVO
   GMAIL_CLIENT_SECRET=ABC123...  # Nunca en git!
   ANTHROPIC_API_KEY=sk-...       # Nunca en git!
   ```

---

## 📋 Plan de Testing Seguro (Sin Riesgos)

### Fase 1: Verificación Rápida (15 min)
```bash
# 1. Estar en la rama correcta
git branch
# Output: * claude/download-email-attachments-h1Ryc

# 2. Levantar servidor
python servidor.py

# 3. En otra terminal, verificar que existen rutas
curl http://localhost:5000/jurisprudencia/
# Esperar respuesta HTML (no error 404/500)

# 4. Verificar que rutas antiguas siguen funcionando
curl http://localhost:5000/
# Esperar que levante la página de inicio
```

### Fase 2: Testing de BD (10 min)
```bash
# En terminal nueva, usar flask shell
flask shell

# Verificar que modelos existen
>>> from modulos.models import GmailOAuthToken, Fallo, FalloTexto
>>> print("✓ Modelos importan correctamente")

# Verificar que tablas existen
>>> from modulos.database import db
>>> db.session.execute(db.text("SELECT name FROM sqlite_master WHERE type='table'"))
>>> # Buscar: gmail_oauth_tokens, emails_fallo, fallos, fallos_texto

# Salir
>>> exit()
```

### Fase 3: Testing de Tesauro (5 min)
```bash
# En flask shell
>>> from flask import current_app
>>> tesauro = current_app.config['TESAURO']
>>> len(tesauro)
10
>>> list(tesauro.keys())
['RESPONSABILIDAD CIVIL', 'ACCIDENTE DE TRÁNSITO', ...]
```

### Fase 4: Verificar que nada se rompió (10 min)
```bash
# Testear auth (debe funcionar igual)
curl -X POST http://localhost:5000/auth/login
# Esperar formulario HTML

# Testear pagos (debe funcionar igual)
curl http://localhost:5000/pagos/planes
# Esperar respuesta

# Testear descargas (debe funcionar igual)
curl http://localhost:5000/descargas/
# Esperar que redirija a login o muestre contenido
```

**Total:** ~40 minutos para verificar que TODO está bien

---

## 🚀 Próximos Pasos (Después de Testing)

### CUANDO CONFÍES que funciona TODO:

**Paso 1: Configurar credenciales (OPCIONAL, solo si quieres testing real)**
```bash
# Editar .env (no commitear después!)
nano .env

# Agregar:
GMAIL_CLIENT_ID=tu_id_aqui
GMAIL_CLIENT_SECRET=tu_secret_aqui
ANTHROPIC_API_KEY=sk-ant-...
```

**Paso 2: Testear flujo OAuth2 (OPCIONAL)**
- Ir a http://localhost:5000/jurisprudencia/admin
- Click en "Configurar Gmail"
- Completar flujo OAuth2
- Verificar que credenciales se guardaron en BD

**Paso 3: Descargar email real (OPCIONAL)**
- Click en "Descargar ahora"
- Esperar que se descargue email
- Verificar que aparece en `data/jurisprudencia/pdfs/`

**Paso 4: Testear búsqueda (OPCIONAL)**
- Ir a http://localhost:5000/jurisprudencia/
- Escribir consulta: "daños y perjuicios"
- Esperar respuesta del chat

---

## 📊 Matriz de Riesgo

| Acción | Riesgo | Cómo Mitigar |
|--------|--------|-------------|
| Testear en rama actual | 🟢 BAJO | No tocar main |
| Mergear a main sin testear | 🔴 ALTO | Testear primero |
| Commitear credenciales | 🔴 CRÍTICO | Nunca commitear .env |
| Cambiar código en main | 🔴 ALTO | Siempre usar feature branch |
| Reset/force push | 🔴 CRÍTICO | Nunca hacer |

---

## 💬 Comunicación

### Si algo se rompe:
1. **No entres en pánico** - estamos en feature branch
2. **No hagas git reset** - eso rompe más cosas
3. **Revierte el commit específico** (seguro):
   ```bash
   git revert <commit_hash>  # Crea nuevo commit que revierte
   git push origin claude/download-email-attachments-h1Ryc
   ```

### Si todo funciona:
1. **Documenta qué testaste** en JURISPRUDENCIA_STATUS.md
2. **Marca tareas completadas** con [x]
3. **Cuando confíes 100%**, avísame para crear PR a main

---

## ✅ Checklist de Seguridad

Antes de hacer CUALQUIER cosa:

- [ ] ¿Estoy en rama `claude/download-email-attachments-h1Ryc`?
  ```bash
  git branch  # Debe mostrar *claude/download-email-attachments-h1Ryc
  ```

- [ ] ¿He verificado qué voy a cambiar?
  ```bash
  git status  # Ver archivos modificados
  git diff    # Ver cambios en detalle
  ```

- [ ] ¿He testeado localmente primero?
  ```bash
  python servidor.py  # ¿Levanta sin errores?
  ```

- [ ] ¿He commiteado con mensaje claro?
  ```bash
  git commit -m "tipo: descripción clara de qué cambió"
  # Tipos: feat (feature), fix (bugfix), docs, test, refactor
  ```

- [ ] ¿He pusheado a la rama correcta?
  ```bash
  git push origin claude/download-email-attachments-h1Ryc
  ```

- [ ] ¿NO he tocado main?
  ```bash
  git branch -a | grep "* main"  # No debe aparecer
  ```

---

## 📞 Resumen Ejecutivo

**Tú:**
- Eres el dueño del código
- Controlas cuándo mergear a main
- Decides cuándo está "listo"

**Yo:**
- Testeo inicial: ✅ PASÓ
- Te ayudo con problemas: 📞 Disponible
- Creo PR cuando vos lo pidas: 🚀 Listo

**El repo:**
- `main` = SEGURO (no cambió)
- `claude/download-...` = donde estamos (testeable)
- Ambas pueden coexistir indefinidamente

---

**¿Listo para empezar testing?**

Opción 1: **Testing rápido (15 min)** - Levanta servidor y verifica que nada se rompió

Opción 2: **Testing completo (1 hora)** - Testing manual de todos los endpoints

Opción 3: **Esperar un poco** - Quieres pensar qué hacer primero

**¿Cuál prefieres?**
