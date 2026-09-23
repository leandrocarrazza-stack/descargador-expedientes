# Skill: analizar-expediente

## Cuándo usar
Cuando el usuario invoca `/analizar-expediente` o pide analizar el estado procesal de un expediente judicial que ya tiene en formato Markdown (generado con `convertir.py`).

---

## Input esperado
El usuario debe proporcionar el **path al archivo `.md`** del expediente. Si no lo indica, preguntar.

Ejemplo:
```
/analizar-expediente Expediente_21_24_UNIFICADO.md
```

---

## Pasos de ejecución

### Paso 1 — Leer el inicio (carátula y contexto inicial)

Usar el tool `Read` sobre las primeras **80 líneas** del archivo `.md`:
```
Read(file_path="<ruta>", limit=80)
```

Extraer:
- Número de expediente
- Carátula (partes: actor / demandado)
- Tribunal / juzgado
- Número total de páginas
- Cuántas páginas están escaneadas (buscar el comentario `AVISO:` al final)

Detectar **tipo de causa** por palabras clave en la carátula:

| Tipo | Keywords |
|------|----------|
| Ejecución | ejecutivo, ejecución hipotecaria, ejecución prendaria, cobro ejecutivo, pagaré |
| Civil ordinario / daños | ordinario, daños y perjuicios, cobro de pesos, incumplimiento, resolución de contrato |
| Familia | alimentos, divorcio, tenencia, régimen de visitas, adopción, filiación |
| Laboral | laboral, despido, accidente de trabajo, ART, indemnización, SECLO |
| Amparo / cautelar | amparo, medida cautelar autónoma, tutela anticipada |

Si la causa no encaja claramente, aplicar el checklist de Civil ordinario.

---

### Paso 2 — Leer la cola (estado procesal actual)

Leer las **últimas 120 líneas** del archivo:
```
Read(file_path="<ruta>", offset=<total_lineas - 120>, limit=120)
```

Para estimar `total_lineas`, se puede usar:
```bash
wc -l <ruta>
```
o leer en bloques de 200 líneas desde el final.

Extraer:
- Última actuación (fecha y tipo)
- Última resolución o providencia
- Plazos mencionados en las últimas actuaciones
- Si hay audiencias programadas o vencimientos próximos

---

### Paso 3 — Búsquedas selectivas por tipo de causa

Usar el tool `Grep` para localizar secciones relevantes. Para cada término encontrado, leer **±25 líneas de contexto** con `Read`.

#### Términos universales (buscar siempre):
```
embargo, inhibición general, medida cautelar, oficio, mandamiento,
informe, plazo, vence, traslado, providencia, sentencia, resolución
```

#### Términos adicionales por tipo de causa:

**Ejecución:**
```
traba, levantamiento, subasta, remate, excepciones, sentencia de remate,
mandamiento de pago, citación de venta, liquidación, tasa de justicia,
depósito judicial
```

**Civil ordinario / daños:**
```
contestación de demanda, reconvención, apertura a prueba, cédula,
designación de perito, pericia, informe pericial, audiencia preliminar,
vista de causa, alegatos, autos para sentencia
```

**Familia:**
```
alimentos provisorios, cuota alimentaria, régimen de comunicación,
homologación, acuerdo, incidente de aumento, cuota definitiva,
convenio regulador
```

**Laboral:**
```
SECLO, conciliación, liquidación, acuerdo homologado, medida cautelar salarial,
retroactivo, intereses, tasa activa, BCR
```

**Estrategia de lectura:** no leer todo el archivo. Grep primero → leer solo los bloques con matches. Si un término genera muchos matches (>10), leer los 5 más recientes (últimos en el archivo = más actuales).

---

### Paso 4 — Análisis y armado de tablas

Con todo lo relevado, completar las **6 tablas** en Markdown.

**Reglas:**
- Si un ítem no se encontró pero debería estar según el tipo de causa → marcarlo como `⚠️ No encontrado`
- Si hay información ambigua → consignarla con `(ver pág. N)`
- No inventar ni inferir más allá de lo que dice el texto
- Fechas: formato DD/MM/AAAA

---

## Formato de salida: 6 tablas

### Tabla 1 — Encabezado

| Campo | Valor |
|-------|-------|
| Número | |
| Carátula | |
| Tribunal | |
| Tipo de causa | |
| Última actuación | |
| Días sin movimiento | (calcular desde hoy: 2026-09-09) |
| Páginas totales / escaneadas | |

---

### Tabla 2 — Estado procesal

| Campo | Detalle |
|-------|---------|
| Etapa actual | (ej: "Apertura a prueba", "Traslado de demanda", "Autos para sentencia") |
| Instancia | (1ª / 2ª / casación) |
| Última resolución | (tipo + fecha) |
| Próxima actuación del juzgado | (si surge del expediente) |
| Observaciones | |

---

### Tabla 3 — Medidas cautelares

| Medida | Fecha | Trabada | Diligenciada | Incontestada | Observaciones |
|--------|-------|---------|--------------|--------------|---------------|
| (ej: Embargo sobre inmueble) | | Sí/No | Sí/No/⚠️ | Sí/No/— | |

Si no hay medidas cautelares: indicarlo explícitamente.

---

### Tabla 4 — Diligencias y oficios

| N° | Destinatario | Tipo | Fecha libramiento | Fecha devolución | Estado | Observaciones |
|----|-------------|------|------------------|-----------------|--------|---------------|
| | (AFIP, Registro Prop., banco, etc.) | (oficio / mandamiento / cédula) | | | Diligenciado / Pendiente / **Incontestado** | |

**Marcar en negrita** los ítems incontestados o sin devolución que superan 60 días desde el libramiento.

---

### Tabla 5 — Plazos corrientes

| Tipo de plazo | Fecha inicio | Días | Vence | Estado |
|--------------|-------------|------|-------|--------|
| (ej: Traslado de demanda 30 días) | | | | 🔴 Vencido / 🟡 Próximo (<15 días) / 🟢 En curso |

Si no hay plazos corrientes identificables, indicarlo.

---

### Tabla 6 — Pendientes prioritarios

Lista ordenada de mayor a menor urgencia:

1. 🔴 **[URGENTE]** — descripción concreta de la acción + fundamento
2. 🟡 **[PRÓXIMO]** — descripción + por qué
3. 🟢 **[PENDIENTE]** — descripción + por qué

**Incluir siempre:**
- Oficios incontestados con fecha de libramiento
- Plazos próximos a vencer o ya vencidos
- Medidas no diligenciadas
- Actuaciones del juzgado sin respuesta del letrado (si las hay)
- Próximas audiencias o fechas fijadas

---

## Nota sobre páginas escaneadas

Si el análisis detecta que páginas clave (una resolución reciente, un oficio, un mandamiento) corresponden a páginas escaneadas sin texto, indicarlo al usuario:

> ⚠️ Las páginas N–M están escaneadas. Para analizar su contenido, compartir esas páginas del PDF original directamente en el chat.

---

## Cierre

Después de entregar las 6 tablas, agregar un párrafo de **resumen ejecutivo** (3–5 líneas) con:
- Estado general del caso en una frase
- Punto más urgente a resolver
- Tiempo estimado sin movimiento relevante
