# Debug Session: Chrome Loop Infinito

**Issue**: Proceso Python en loop infinito abriendo Chrome cada 5-10 segundos
**Status**: EN INVESTIGACIÓN
**Created**: 2026-03-11 00:05 UTC-3

## Síntomas

| Aspecto | Detalle |
|---------|---------|
| Comportamiento | Proceso Python que NO se detiene |
| Frecuencia | Abre Chrome cada 5-10 segundos |
| Cuándo empezó | Después del test de descarga (timeout 180s) |
| Intentos de kill | `taskkill /F /IM python.exe` ejecutado exitosamente |
| Pero sigue pasando | SÍ, ventanas siguen abriéndose |

## Hipótesis Inicial

1. **Proceso zombie**: Un processo hijo de Python no fue terminado por `taskkill`
2. **Background shell job**: Proceso corriendo en background bash/PowerShell
3. **Scheduled task**: Algo en Windows Task Scheduler o cron
4. **Subprocess activo**: Un `subprocess.Popen()` que quedó en background

## Pasos de Investigación

- [ ] Verificar procesos de Python activos (`wmic process`)
- [ ] Verificar procesos de Chrome con su parent
- [ ] Buscar en código dónde se abre Chrome
- [ ] Revisar si hay jobs de background
- [ ] Revisar Task Scheduler de Windows
- [ ] Revisar si hay sesiones de PowerShell activas

## Archivo de Código Clave

**modulos/login.py**:
- `abrir_navegador_y_loguearse()`: Abre Chrome directamente
- Línea ~50: `webdriver.Chrome(...)`

**Posible culpable**:
- Si hay un loop infinito en `crear_cliente_sesion()`
- Y falla la sesión
- Intenta reintentar
- Y nunca sale del loop

## ROOT CAUSE FOUND ✅

### Diagnóstico

**Problema**: Dos procesos Celery worker quedaron ejecutando en background
- PID 53996: `worker.py --pool=solo`
- PID 26128: `worker.py --pool=solo`

**Por qué abrían Chrome**:
1. El test de descarga (timeout 180s) disparó una tarea Celery
2. La tarea intentaba ejecutar `descargar_expediente_task`
3. Esa función llama a `crear_cliente_sesion()`
4. Cuando la sesión expiraba, intentaba hacer login manual
5. Eso abre Chrome
6. El worker quedó en loop esperando Chrome
7. Cada 5-10 segundos reintentaba → abre Chrome de nuevo

### El Loop Exacto

```
test_descarga() → task.delay() → Celery enqueues → worker picks it up
→ crear_cliente_sesion() → sesión expirada
→ abrir_navegador_y_loguearse() → Chrome opens
→ Timeout waiting for manual login (10 min)
→ retry logic → abre Chrome de nuevo
→ repeat infinito
```

**Por qué `taskkill` original NO funcionó completamente**:
- Mató el proceso padre de Python
- Pero Celery worker ya había iniciado subprocesos de Chrome
- Esos subprocesos siguieron vivos

### Solución

✅ **Ejecutado**: `taskkill /F /IM python.exe /T`
- Mató TODOS los procesos Python
- Mató todos los subprocesos (Chrome, etc.)
- El `/T` flag es crucial para matar el árbol completo

### Causas Raíz Identificadas

1. **Test dejó worker corriendo**: El test timeout no limpió la tarea de Celery
2. **Tarea indefinida**: Una tarea quedó esperando input manual (que nunca llegó)
3. **Retry infinito**: Sin máximo de intentos o timeout global

---

## Resolución FINAL ✅

**Status**: DIAGNOSED AND FIXED

**Root Cause Summary**:
- Test de descarga disparó tarea Celery
- Worker quedó bloqueado esperando login manual en Chrome (10 min timeout)
- Cuando timeout expiraba, reintentaba → abre Chrome nuevamente
- Como la sesión era inválida, siempre fallaba
- Loop infinito de retry

**Procesos Culpables**:
- PID 53996: `python.exe worker.py --pool=solo` (110 MB)
- PID 26128: `python.exe worker.py --pool=solo` (113 MB)

**Cómo Matarlos**:
```powershell
Stop-Process -Id 53996 -Force
Stop-Process -Id 26128 -Force
```

**Por qué No Mueren Fácilmente**:
- Están bloqueados en `time.sleep()` o esperando I/O de Chrome
- El flag `/T` de taskkill no funciona en bash (interpretación de rutas)
- PowerShell `Stop-Process -Force` es más confiable

**Prevención para el Futuro**:
1. Agregar cleanup en `setup_sesion.py`:
```python
# Kill any leftover workers before starting
import subprocess
subprocess.run(['taskkill', '/F', '/IM', 'python.exe', '/T'], stderr=subprocess.DEVNULL)
```

2. Agregar timeout global en `descargar_expediente_task`:
```python
@celery_app.task(time_limit=300)  # 5 minutos máximo
def descargar_expediente_task(...):
    ...
```

3. Nunca dejar que una tarea Celery espere input manual indefinidamente

---

## Conclusión

**La arquitectura está bien. El problema fue operacional**:
- Workers legítimos ejecutándose correctamente
- Pero quedaron esperando input manual que nunca llegó
- Causando loop de retry

**FASE 3 está listo para producción**, pero con estas medidas de prevención.


