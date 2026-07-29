---
name: stjer
description: Buscador de jurisprudencia del Superior Tribunal de Justicia de Entre Ríos (STJER). Usar cuando el usuario pida buscar fallos, sentencias, sumarios o jurisprudencia entrerriana, o mencione STJER, jur.jusentrerios.gov.ar, "jurisprudencia de Entre Ríos" o "buscador jur". No usar para Mesa Virtual / expedientes propios (ver skill descargar-expediente) ni para SAIJ/CSJN/JUBA (sistemas nacionales/bonaerenses).
---

# STJER — Jurisprudencia de Entre Ríos

Buscás en un **corpus local ya cosechado**, no manejando el navegador. Cada
búsqueda es un comando que responde en menos de un segundo.

> **No abras el navegador para buscar.** La versión anterior de esta skill lo
> hacía y costaba entre 150k y 600k tokens por consulta, tardaba minutos y
> normalmente terminaba sin resultados: el captcha se leía de un screenshot de
> página completa, las filas se clickeaban por coordenadas y no había ningún
> dato del tesauro. Todo eso lo resuelve ahora el CLI.

Todos los comandos se corren desde la raíz del repo `descargador-expedientes`.

## 1. Verificá que haya corpus

```bash
python -m scripts.stjer estado
```

Te dice cuántos fallos hay, hasta qué fecha llegan y cuántos tienen el detalle
cosechado. Si dice que no hay corpus, andá a "Puesta en marcha" al final.

## 2. Traducí la consulta a voces del tesauro

Antes de buscar, mirá con qué voces jurídicas nombra el STJER el tema. Es el
paso que antes fallaba siempre.

```bash
python -m scripts.stjer voces "responsabilidad del Estado por caída de un árbol"
```

Devuelve voces con su ruta `Materia > Voz Principal > Voz` y un puntaje. El
origen de cada sugerencia importa:

- `corpus` — sale de co-ocurrencia sobre los fallos ya cosechados. Es la señal
  buena: descubre que "delito tentado" se indexa como `TENTATIVA`.
- `etiqueta` — parecido de nombre. Es lo único disponible si el corpus todavía
  está vacío.

Elegí una o dos voces plausibles y usalas en el paso siguiente. Si ninguna
convence, buscá solo por texto: es perfectamente válido.

## 3. Buscá

```bash
python -m scripts.stjer buscar "prescripción liberatoria" --fuero civil --desde 2018 -n 10
```

Opciones que importan:

| Opción | Para qué |
|---|---|
| `--fuero civil` | Coincidencia laxa: `civil` matchea "Civil y Comercial" |
| `--organismo "Sala I"` | Acota al tribunal |
| `--juez González` | Fallos donde votó esa persona |
| `--voz "RESPONSABILIDAD OBJETIVA"` | Repetible. Filtra por voz del tesauro |
| `--desde 2015 --hasta 2020` | Acepta `2015`, `2015-06` o `2015-06-30` |
| `--formato json` | Para procesar; `markdown` (default) para leer |
| `--completo` | Sumario entero en vez del fragmento resaltado |

Detalles del comportamiento, para que no te sorprenda:

- Entrecomillar busca la frase exacta: `'"daño moral"'` ≠ `'daño moral'`.
- Las tildes dan igual en los dos sentidos: `prescripcion` encuentra
  `prescripción`.
- Si la búsqueda con AND no trae nada, reintenta sola con OR y avisa por
  stderr.
- Si un resultado dice *"extracto del listado"*, ese sumario está truncado
  porque todavía no se cosechó su detalle. Sirve para ubicar el fallo, pero no
  lo cites como si fuera el sumario completo.

## 4. Abrí un fallo o bajá el PDF

```bash
python -m scripts.stjer fallo sitio:88124        # ficha completa en JSON
python -m scripts.stjer pdf --fallo sitio:88124  # descarga el PDF
```

Bajá el PDF **solo** de los fallos que vayas a leer enteros. Los sumarios —que
es lo que se cita— ya están en el corpus, y bajar los ~14.800 PDF son ~3,7 GB.

## 5. Presentá los resultados

Para cada fallo relevante, dá como mínimo: **carátula, organismo/jurisdicción,
N° de expediente, fecha y el sumario o el fragmento pertinente**.

Y lo más importante: si la consulta es para un caso concreto, decí
explícitamente si el precedente es **favorable, adverso o distinguible**
respecto del caso del usuario. No te limites a listar resultados sin análisis.

## Qué cubre este buscador (y qué no)

- Solo fallos y sumarios **seleccionados por los Tribunales de Apelación y las
  Salas del STJ**, desde 2004: ~14.800 fallos y ~34.500 sumarios. No es toda la
  jurisprudencia de la provincia.
- El resto de los expedientes está en **Mesa Virtual**, que es otro sistema:
  para eso está la skill `descargar-expediente`.
- Si el usuario pide algo posterior a la última cosecha, actualizá el corpus:
  `python -m scripts.stjer cosechar listas --desde <último-mes>`.

## Si algo falla

| Síntoma | Qué hacer |
|---|---|
| "Todavía no hay corpus local" | Ver "Puesta en marcha" |
| "quedó vacía después de sacar las palabras vacías" | La consulta era toda preposiciones; usá términos específicos |
| "el sitio pide verificación" (salida 3) | Se venció la sesión: `python -m scripts.stjer sesion --abrir` y repetí el comando, que retoma donde iba |
| La cosecha cortó por "fallos seguidos" | Puede ser bloqueo o caída del sitio. Esperá y relanzá: no se perdió trabajo |
| Meses descuadrados en `estado` | `python -m scripts.stjer reparar` los re-encola |

## Puesta en marcha (solo la primera vez)

Requiere red hacia `jur.jusentrerios.gov.ar` y correrse en la máquina del
usuario. **Preguntale antes de lanzar una cosecha completa**: son horas de
corrida.

```bash
pip install -r requirements-stjer.txt
python -m scripts.stjer descubrir          # revisa la Fase 0 (docs/STJER_FASE0.md)
python -m scripts.stjer sesion --abrir     # UN captcha, a mano, y queda la sesión
python -m scripts.stjer tesauro --cosechar # el tesauro real
python -m scripts.stjer cosechar listas    # pasada A: ~3-6 h, ya deja todo buscable
python -m scripts.stjer cosechar detalles --limite 500   # pasada B: incremental
```

La pasada B se puede cortar cuando sea: agrega voces, votos y sumarios completos
de forma acumulativa, priorizando los años más recientes. Cortar nunca pierde
trabajo, solo demora.

## Modo en vivo (excepcional)

Si el usuario necesita algo que con seguridad no está en el corpus (un fallo de
esta semana) y no quiere esperar una cosecha, se puede consultar el sitio con
`--motor navegador`. Es lento y hay que resolver el captcha. **Preferí siempre
actualizar el corpus**: sirve para todas las consultas siguientes, no para una.
