"""
Corpus local de jurisprudencia STJER (SQLite + FTS5)
====================================================

Artefacto portable y autocontenido: un solo archivo .sqlite que el buscador
abre directo, sin Flask, sin Postgres y sin red.

Decisiones de diseño
--------------------
* **El sumario es la unidad de busqueda y de cita**, no el fallo. Por eso los
  modelos que ya existen (Fallo / FalloTexto) no sirven: guardan todos los
  sumarios de un fallo como un blob JSON en una columna, con lo cual rankear
  por sumario —que es el punto— es imposible.

* **Capa `documentos` desnormalizada** entre los datos y el indice FTS5, en
  vez de triggers directos sobre `sumarios` y `sumario_voces`. La columna de
  voces depende de un join N:M, asi que un trigger sobre la tabla de union
  tendria que re-agregar; serian tres triggers fragiles sobre tres tablas. Con
  `documentos` cambia una sola tabla y el FTS la sigue.

* **`remove_diacritics 2`** en el tokenizador pliega los diacriticos de los dos
  lados, indice y consulta, asi que "prescripcion" (escrito sin tilde, como se
  escribe apurado) encuentra "prescripción". Como el plegado lo hace el
  tokenizador, el indice guarda el texto ORIGINAL y los fragmentos que se le
  muestran al usuario salen legibles.

* **Todo upsert es idempotente** por `clave_natural`: re-correr un mes entero
  nunca duplica.
"""

import gzip
import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .normalizacion import colapsar, normalizar_expediente, normalizar_texto

logger = logging.getLogger(__name__)

ESQUEMA_VERSION = 2

ESTADOS_TAREA = ("pendiente", "en_curso", "ok", "error", "omitido")


# ═══════════════════════════════════════════════════════════════════════════
#  Esquema
# ═══════════════════════════════════════════════════════════════════════════

ESQUEMA = """
CREATE TABLE IF NOT EXISTS meta (
    clave TEXT PRIMARY KEY,
    valor TEXT
);

-- Un fallo = una sentencia.
CREATE TABLE IF NOT EXISTS fallos (
    id              INTEGER PRIMARY KEY,
    clave_natural   TEXT NOT NULL UNIQUE,
    id_sitio        TEXT,
    fuero           TEXT,
    jurisdiccion    TEXT,
    organismo       TEXT,
    caratula        TEXT,
    caratula_norm   TEXT,
    nro_expediente  TEXT,
    expediente_norm TEXT,
    fecha_fallo     TEXT,              -- ISO 'YYYY-MM-DD' (SQLite no tiene DATE)
    anio            INTEGER,
    mes             TEXT,              -- 'YYYY-MM', para reconciliar la cosecha
    pagina          INTEGER,           -- pagina del listado donde aparecio, para reabrir el detalle
    enlace_pdf      TEXT,
    pdf_ruta        TEXT,
    pdf_sha256      TEXT,
    pdf_bytes       INTEGER,
    ref_detalle     TEXT,
    detalle_ok      INTEGER NOT NULL DEFAULT 0,
    capturado_en    TEXT NOT NULL,
    actualizado_en  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fallos_fecha   ON fallos(fecha_fallo);
CREATE INDEX IF NOT EXISTS ix_fallos_fuero   ON fallos(fuero);
CREATE INDEX IF NOT EXISTS ix_fallos_org     ON fallos(organismo);
CREATE INDEX IF NOT EXISTS ix_fallos_mes     ON fallos(mes);
CREATE INDEX IF NOT EXISTS ix_fallos_detalle ON fallos(detalle_ok);
CREATE INDEX IF NOT EXISTS ix_fallos_exp     ON fallos(expediente_norm);

CREATE TABLE IF NOT EXISTS sumarios (
    id         INTEGER PRIMARY KEY,
    fallo_id   INTEGER NOT NULL REFERENCES fallos(id) ON DELETE CASCADE,
    orden      INTEGER NOT NULL DEFAULT 0,
    texto      TEXT NOT NULL,
    truncado   INTEGER NOT NULL DEFAULT 0,  -- 1 = extracto del listado
    UNIQUE(fallo_id, orden)
);
CREATE INDEX IF NOT EXISTS ix_sumarios_fallo ON sumarios(fallo_id);

CREATE TABLE IF NOT EXISTS voces (
    id            INTEGER PRIMARY KEY,
    materia       TEXT,
    voz_principal TEXT,
    voz           TEXT NOT NULL,
    ruta          TEXT NOT NULL UNIQUE,   -- 'MATERIA > VOZ PRINCIPAL > VOZ'
    ruta_norm     TEXT NOT NULL,
    origen        TEXT NOT NULL DEFAULT 'tesauro',  -- 'tesauro' | 'corpus'
    frecuencia    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_voces_voz  ON voces(voz);
CREATE INDEX IF NOT EXISTS ix_voces_norm ON voces(ruta_norm);

CREATE TABLE IF NOT EXISTS sumario_voces (
    sumario_id INTEGER NOT NULL REFERENCES sumarios(id) ON DELETE CASCADE,
    voz_id     INTEGER NOT NULL REFERENCES voces(id)    ON DELETE CASCADE,
    PRIMARY KEY (sumario_id, voz_id)
);
CREATE INDEX IF NOT EXISTS ix_sv_voz ON sumario_voces(voz_id);

CREATE TABLE IF NOT EXISTS votos (
    id        INTEGER PRIMARY KEY,
    fallo_id  INTEGER NOT NULL REFERENCES fallos(id) ON DELETE CASCADE,
    juez      TEXT NOT NULL,
    juez_norm TEXT NOT NULL,
    tipo_voto TEXT,
    orden     INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_votos_juez  ON votos(juez_norm);
CREATE INDEX IF NOT EXISTS ix_votos_fallo ON votos(fallo_id);

-- Capa desnormalizada: 1 fila por sumario. Se reconstruye entera.
--
-- Guarda el texto ORIGINAL, con tildes y mayusculas. El tokenizador
-- `remove_diacritics 2` ya pliega los diacriticos de los dos lados (indice y
-- consulta), asi que buscar "dano moral" encuentra "daño moral" igual — pero
-- ademas snippet() devuelve algo legible para mostrarle al usuario, en vez de
-- «dano» «moral». Por eso tampoco hace falta guardar una copia normalizada de
-- cada sumario: serian ~45 MB para nada.
CREATE TABLE IF NOT EXISTS documentos (
    id        INTEGER PRIMARY KEY,   -- = sumarios.id
    fallo_id  INTEGER NOT NULL,
    texto     TEXT NOT NULL,
    voces     TEXT NOT NULL DEFAULT '',
    caratula  TEXT NOT NULL DEFAULT '',
    organismo TEXT NOT NULL DEFAULT ''
);

CREATE VIRTUAL TABLE IF NOT EXISTS documentos_fts USING fts5(
    texto, voces, caratula, organismo,
    content='documentos',
    content_rowid='id',
    tokenize="unicode61 remove_diacritics 2"
);

CREATE TRIGGER IF NOT EXISTS documentos_ai AFTER INSERT ON documentos BEGIN
    INSERT INTO documentos_fts(rowid, texto, voces, caratula, organismo)
    VALUES (new.id, new.texto, new.voces, new.caratula, new.organismo);
END;
CREATE TRIGGER IF NOT EXISTS documentos_ad AFTER DELETE ON documentos BEGIN
    INSERT INTO documentos_fts(documentos_fts, rowid, texto, voces, caratula, organismo)
    VALUES ('delete', old.id, old.texto, old.voces, old.caratula, old.organismo);
END;
CREATE TRIGGER IF NOT EXISTS documentos_au AFTER UPDATE ON documentos BEGIN
    INSERT INTO documentos_fts(documentos_fts, rowid, texto, voces, caratula, organismo)
    VALUES ('delete', old.id, old.texto, old.voces, old.caratula, old.organismo);
    INSERT INTO documentos_fts(rowid, texto, voces, caratula, organismo)
    VALUES (new.id, new.texto, new.voces, new.caratula, new.organismo);
END;

-- Cola de trabajo durable: es lo que hace la cosecha reanudable.
CREATE TABLE IF NOT EXISTS cosecha_tareas (
    id             INTEGER PRIMARY KEY,
    tipo           TEXT NOT NULL,
    clave          TEXT NOT NULL,
    prioridad      INTEGER NOT NULL DEFAULT 0,
    estado         TEXT NOT NULL DEFAULT 'pendiente',
    intentos       INTEGER NOT NULL DEFAULT 0,
    http_status    INTEGER,
    ultimo_error   TEXT,
    tomada_en      TEXT,
    actualizado_en TEXT,
    UNIQUE(tipo, clave)
);
CREATE INDEX IF NOT EXISTS ix_tareas_pend
    ON cosecha_tareas(tipo, estado, prioridad DESC, clave DESC);

-- Archivo del HTML crudo. ~60 MB comprimidos que permiten re-parsear 14.800
-- registros en dos minutos en vez de re-cosechar cuarenta horas.
CREATE TABLE IF NOT EXISTS respuestas_crudas (
    id          INTEGER PRIMARY KEY,
    tipo        TEXT NOT NULL,
    clave       TEXT NOT NULL,
    obtenido_en TEXT NOT NULL,
    http_status INTEGER,
    cuerpo_gz   BLOB NOT NULL,
    UNIQUE(tipo, clave)
);

-- Conteo declarado por el sitio ("Se encontraron N registros") por mes.
-- Comparar contra lo realmente guardado es lo que detecta que la paginacion
-- perdio filas en silencio.
CREATE TABLE IF NOT EXISTS reconciliacion (
    mes         TEXT PRIMARY KEY,
    esperados   INTEGER,
    verificado_en TEXT
);
"""


# ═══════════════════════════════════════════════════════════════════════════
#  Conexion
# ═══════════════════════════════════════════════════════════════════════════

def ahora() -> str:
    """Timestamp ISO en UTC, que es como se guarda todo aca."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@contextmanager
def transaccion(con: sqlite3.Connection):
    """
    BEGIN IMMEDIATE ... COMMIT, anidable.

    Si ya hay una transaccion abierta, no abre otra y deja que la de afuera
    decida: asi `reemplazar_sumarios` es atomico por si solo y tambien cuando
    lo llama la cosecha dentro de la transaccion de una tarea.
    """
    if con.in_transaction:
        yield con
        return
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except Exception:
        con.execute("ROLLBACK")
        raise
    else:
        con.execute("COMMIT")


def abrir(ruta, solo_lectura: bool = False) -> sqlite3.Connection:
    """
    Abre (y crea si hace falta) el corpus.

    WAL para que una busqueda pueda leer mientras la cosecha escribe.
    """
    ruta = Path(ruta)
    if solo_lectura and not ruta.exists():
        raise FileNotFoundError(
            f"No existe el corpus en {ruta}. Cosechalo primero con:\n"
            f"    python -m scripts.stjer cosechar listas"
        )
    ruta.parent.mkdir(parents=True, exist_ok=True)

    # isolation_level=None -> autocommit. Las transacciones se abren a mano
    # con transaccion(), que es lo unico que permite un BEGIN IMMEDIATE
    # explicito (el modo deferred de sqlite3 ya tiene una abierta y choca).
    if solo_lectura:
        con = sqlite3.connect(f"file:{ruta}?mode=ro", uri=True, isolation_level=None)
    else:
        con = sqlite3.connect(str(ruta), isolation_level=None)

    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    if not solo_lectura:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        crear_esquema(con)
    return con


def crear_esquema(con: sqlite3.Connection) -> None:
    """Crea el esquema si falta y deja registrada la version."""
    con.executescript(ESQUEMA)
    actual = leer_meta(con, "esquema_version")
    if actual is None:
        escribir_meta(con, "esquema_version", str(ESQUEMA_VERSION))
        escribir_meta(con, "creado_en", ahora())
    con.commit()
    migrar(con)


def migrar(con: sqlite3.Connection) -> None:
    """Migraciones entre versiones de esquema."""
    version = int(leer_meta(con, "esquema_version") or ESQUEMA_VERSION)
    if version > ESQUEMA_VERSION:
        raise RuntimeError(
            f"El corpus fue escrito con esquema v{version} y este codigo "
            f"entiende hasta v{ESQUEMA_VERSION}. Actualiza el codigo."
        )

    if version < 2:
        # v2: agrega fallos.pagina (pagina del listado donde aparecio cada
        # fallo), necesaria para poder reabrir su detalle mas adelante.
        con.execute("ALTER TABLE fallos ADD COLUMN pagina INTEGER")
        con.commit()
        escribir_meta(con, "esquema_version", "2")


def leer_meta(con: sqlite3.Connection, clave: str, defecto=None):
    fila = con.execute("SELECT valor FROM meta WHERE clave=?", (clave,)).fetchone()
    return fila["valor"] if fila else defecto


def escribir_meta(con: sqlite3.Connection, clave: str, valor) -> None:
    con.execute(
        "INSERT INTO meta(clave, valor) VALUES(?,?) "
        "ON CONFLICT(clave) DO UPDATE SET valor=excluded.valor",
        (clave, str(valor)),
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Clave natural
# ═══════════════════════════════════════════════════════════════════════════

def clave_natural(datos: dict) -> str:
    """
    Identidad estable de un fallo, para que re-cosechar sea idempotente.

    Se prefiere el id propio del sitio cuando el listado lo expone (Toba casi
    siempre lo lleva en el onclick de la fila). Si no hay, se cae a un sha1 de
    expediente + caratula + fecha, que es lo mas estable que queda.
    """
    id_sitio = (datos.get("id_sitio") or "").strip()
    if id_sitio:
        return f"sitio:{id_sitio}"

    semilla = "|".join(
        (
            normalizar_expediente(datos.get("nro_expediente") or ""),
            normalizar_texto(datos.get("caratula") or ""),
            (datos.get("fecha_fallo") or ""),
        )
    )
    return "sha1:" + hashlib.sha1(semilla.encode("utf-8")).hexdigest()


# ═══════════════════════════════════════════════════════════════════════════
#  Upserts
# ═══════════════════════════════════════════════════════════════════════════

_CAMPOS_FALLO = (
    "id_sitio", "fuero", "jurisdiccion", "organismo", "caratula",
    "nro_expediente", "fecha_fallo", "enlace_pdf", "ref_detalle",
)


def upsert_fallo(con: sqlite3.Connection, datos: dict) -> int:
    """
    Inserta o actualiza un fallo y devuelve su id.

    Los campos que llegan vacios NO pisan lo que ya habia: la pasada de
    listados trae menos datos que la de detalles, y no queremos que un
    re-listado borre las voces ya cosechadas.
    """
    clave = datos.get("clave_natural") or clave_natural(datos)
    fecha = datos.get("fecha_fallo") or None

    valores = {
        "clave_natural": clave,
        "caratula_norm": normalizar_texto(datos.get("caratula") or ""),
        "expediente_norm": normalizar_expediente(datos.get("nro_expediente") or ""),
        "anio": int(fecha[:4]) if fecha and len(fecha) >= 4 and fecha[:4].isdigit() else None,
        "mes": fecha[:7] if fecha and len(fecha) >= 7 else None,
        "pagina": int(datos["pagina"]) if datos.get("pagina") else None,
        "capturado_en": ahora(),
        "actualizado_en": ahora(),
    }
    for campo in _CAMPOS_FALLO:
        valores[campo] = colapsar(datos.get(campo) or "") or None
    if datos.get("detalle_ok"):
        valores["detalle_ok"] = 1

    columnas = list(valores)
    marcadores = ",".join("?" for _ in columnas)
    # COALESCE(excluded.x, x): un valor nuevo vacio no pisa el que ya estaba.
    asignaciones = ",".join(
        f"{c}=COALESCE(excluded.{c}, {c})"
        for c in columnas
        if c not in ("clave_natural", "capturado_en")
    )

    con.execute(
        f"INSERT INTO fallos({','.join(columnas)}) VALUES({marcadores}) "
        f"ON CONFLICT(clave_natural) DO UPDATE SET {asignaciones}",
        [valores[c] for c in columnas],
    )
    fila = con.execute(
        "SELECT id FROM fallos WHERE clave_natural=?", (clave,)
    ).fetchone()
    return fila["id"]


def marcar_detalle_ok(con: sqlite3.Connection, fallo_id: int) -> None:
    con.execute(
        "UPDATE fallos SET detalle_ok=1, actualizado_en=? WHERE id=?",
        (ahora(), fallo_id),
    )


def upsert_voz(
    con: sqlite3.Connection,
    materia: str = "",
    voz_principal: str = "",
    voz: str = "",
    origen: str = "tesauro",
) -> int:
    """Inserta una voz del tesauro (o vista en un fallo) y devuelve su id."""
    materia = colapsar(materia)
    voz_principal = colapsar(voz_principal)
    voz = colapsar(voz)
    if not voz:
        raise ValueError("Una voz necesita al menos el nivel hoja")

    ruta = " > ".join(p for p in (materia, voz_principal, voz) if p)
    con.execute(
        "INSERT INTO voces(materia, voz_principal, voz, ruta, ruta_norm, origen) "
        "VALUES(?,?,?,?,?,?) "
        # Si ya existia por corpus y ahora llega del tesauro, gana el tesauro.
        "ON CONFLICT(ruta) DO UPDATE SET "
        "  origen=CASE WHEN excluded.origen='tesauro' THEN 'tesauro' ELSE origen END",
        (materia or None, voz_principal or None, voz, ruta,
         normalizar_texto(ruta), origen),
    )
    return con.execute("SELECT id FROM voces WHERE ruta=?", (ruta,)).fetchone()["id"]


def reemplazar_sumarios(
    con: sqlite3.Connection,
    fallo_id: int,
    sumarios: list,
    truncado: bool = False,
) -> int:
    """
    Reemplaza los sumarios de un fallo (borra e inserta en una transaccion).

    Que sea reemplazo total y no merge es lo que hace que un detalle mal
    parseado se auto-cure al reprocesarlo.

    `sumarios` es una lista de dicts {texto, voces:[{materia, voz_principal,
    voz}]} o de strings sueltos.
    """
    # Un extracto del listado no debe pisar el sumario completo ya cosechado.
    if truncado:
        ya_completo = con.execute(
            "SELECT 1 FROM sumarios WHERE fallo_id=? AND truncado=0 LIMIT 1",
            (fallo_id,),
        ).fetchone()
        if ya_completo:
            return 0

    insertados = 0
    with transaccion(con):
        con.execute("DELETE FROM sumarios WHERE fallo_id=?", (fallo_id,))

        for orden, item in enumerate(sumarios):
            if isinstance(item, str):
                item = {"texto": item}
            texto = colapsar(item.get("texto") or "")
            if not texto:
                continue

            cur = con.execute(
                "INSERT INTO sumarios(fallo_id, orden, texto, truncado) "
                "VALUES(?,?,?,?)",
                (fallo_id, orden, texto, int(truncado)),
            )
            sumario_id = cur.lastrowid
            insertados += 1

            for v in item.get("voces") or []:
                voz_id = upsert_voz(
                    con,
                    materia=v.get("materia", ""),
                    voz_principal=v.get("voz_principal", ""),
                    voz=v.get("voz", ""),
                    origen="corpus",
                )
                con.execute(
                    "INSERT OR IGNORE INTO sumario_voces(sumario_id, voz_id) VALUES(?,?)",
                    (sumario_id, voz_id),
                )

    return insertados


def agregar_extracto(con: sqlite3.Connection, fallo_id: int, texto: str) -> bool:
    """
    Agrega un extracto de sumario del listado sin borrar los ya existentes.

    A diferencia de reemplazar_sumarios, esta funcion ACUMULA: un fallo con N
    sumarios aparece N veces en el listado (una fila por sumario), y cada
    llamada agrega el extracto si todavia no esta. Asi se preservan todos los
    sumarios del fallo al paginar el listado.

    No toca sumarios completos (truncado=0): si ya llego el detalle, no pisa.
    """
    texto = colapsar(texto or "")
    if not texto:
        return False
    ya_completo = con.execute(
        "SELECT 1 FROM sumarios WHERE fallo_id=? AND truncado=0 LIMIT 1",
        (fallo_id,),
    ).fetchone()
    if ya_completo:
        return False
    ya_existe = con.execute(
        "SELECT 1 FROM sumarios WHERE fallo_id=? AND texto=? LIMIT 1",
        (fallo_id, texto),
    ).fetchone()
    if ya_existe:
        return False
    max_orden = con.execute(
        "SELECT COALESCE(MAX(orden)+1, 0) FROM sumarios WHERE fallo_id=?",
        (fallo_id,),
    ).fetchone()[0]
    con.execute(
        "INSERT INTO sumarios(fallo_id, orden, texto, truncado) VALUES(?,?,?,1)",
        (fallo_id, max_orden, texto),
    )
    return True


def reemplazar_votos(con: sqlite3.Connection, fallo_id: int, votos: list) -> int:
    """Reemplaza los votos de un fallo. Mismo criterio que los sumarios."""
    n = 0
    with transaccion(con):
        con.execute("DELETE FROM votos WHERE fallo_id=?", (fallo_id,))
        for orden, voto in enumerate(votos or []):
            juez = colapsar(voto.get("juez") or "")
            if not juez:
                continue
            con.execute(
                "INSERT INTO votos(fallo_id, juez, juez_norm, tipo_voto, orden) "
                "VALUES(?,?,?,?,?)",
                (fallo_id, juez, normalizar_texto(juez),
                 colapsar(voto.get("tipo_voto") or "") or None, orden),
            )
            n += 1
    return n


# ═══════════════════════════════════════════════════════════════════════════
#  Indice de busqueda
# ═══════════════════════════════════════════════════════════════════════════

def reconstruir_documentos(con: sqlite3.Connection, fallo_id=None) -> int:
    """
    Regenera la capa `documentos` (y por los triggers, el indice FTS5).

    Con fallo_id regenera solo ese fallo; sin el, todo el corpus. En 34.500
    sumarios tarda segundos.
    """
    with transaccion(con):
        if fallo_id is None:
            con.execute("DELETE FROM documentos")
            filtro, params = "", ()
        else:
            con.execute(
                "DELETE FROM documentos WHERE id IN "
                "(SELECT id FROM sumarios WHERE fallo_id=?)",
                (fallo_id,),
            )
            filtro, params = "WHERE s.fallo_id=?", (fallo_id,)

        cur = con.execute(
            f"""
            INSERT INTO documentos(id, fallo_id, texto, voces, caratula, organismo)
            SELECT s.id,
                   s.fallo_id,
                   s.texto,
                   COALESCE((SELECT group_concat(v.ruta, ' ')
                               FROM sumario_voces sv
                               JOIN voces v ON v.id = sv.voz_id
                              WHERE sv.sumario_id = s.id), ''),
                   COALESCE(f.caratula, ''),
                   COALESCE(f.organismo, '')
              FROM sumarios s
              JOIN fallos f ON f.id = s.fallo_id
            {filtro}
            """,
            params,
        )
        insertados = cur.rowcount

        if fallo_id is None:
            actualizar_frecuencias_voces(con)
            escribir_meta(con, "reindexado_en", ahora())

    if fallo_id is None:
        # optimize no puede ir dentro de la transaccion de arriba.
        con.execute("INSERT INTO documentos_fts(documentos_fts) VALUES('optimize')")
    return insertados


def actualizar_frecuencias_voces(con: sqlite3.Connection) -> None:
    """Cachea cuantos sumarios usa cada voz (lo usa el ranking de sugerencias)."""
    con.execute(
        "UPDATE voces SET frecuencia = COALESCE("
        "  (SELECT COUNT(*) FROM sumario_voces sv WHERE sv.voz_id = voces.id), 0)"
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Cola de tareas
# ═══════════════════════════════════════════════════════════════════════════

def encolar(con: sqlite3.Connection, tipo: str, clave: str, prioridad: int = 0) -> bool:
    """
    Agrega una tarea si no existe. Devuelve True si la creo.

    INSERT OR IGNORE sobre (tipo, clave): replanificar es gratis y no pisa el
    progreso de una corrida anterior.
    """
    cur = con.execute(
        "INSERT OR IGNORE INTO cosecha_tareas(tipo, clave, prioridad, estado, "
        "actualizado_en) VALUES(?,?,?, 'pendiente', ?)",
        (tipo, clave, prioridad, ahora()),
    )
    return cur.rowcount > 0


def tomar_tarea(con: sqlite3.Connection, tipo: str, orden: str = "reciente"):
    """
    Reclama la proxima tarea pendiente y la marca 'en_curso'.

    Un solo proceso cosecha a la vez, asi que alcanza con BEGIN IMMEDIATE.
    orden='reciente' prioriza las claves mas altas (los años nuevos primero),
    que es lo que uno quiere si va a cortar la corrida por la mitad.
    """
    direccion = "DESC" if orden == "reciente" else "ASC"
    with transaccion(con):
        fila = con.execute(
            f"SELECT * FROM cosecha_tareas WHERE tipo=? AND estado='pendiente' "
            f"ORDER BY prioridad DESC, clave {direccion} LIMIT 1",
            (tipo,),
        ).fetchone()
        if fila is None:
            return None
        con.execute(
            "UPDATE cosecha_tareas SET estado='en_curso', tomada_en=?, "
            "actualizado_en=? WHERE id=?",
            (ahora(), ahora(), fila["id"]),
        )
        # Se relee para devolver la fila ya marcada 'en_curso' y no una copia
        # obsoleta del estado anterior.
        fila = con.execute(
            "SELECT * FROM cosecha_tareas WHERE id=?", (fila["id"],)
        ).fetchone()
    return fila


def cerrar_tarea(
    con: sqlite3.Connection,
    tarea_id: int,
    estado: str,
    error: str = None,
    http_status: int = None,
) -> None:
    if estado not in ESTADOS_TAREA:
        raise ValueError(f"Estado invalido: {estado}")
    con.execute(
        "UPDATE cosecha_tareas SET estado=?, ultimo_error=?, http_status=?, "
        "intentos=intentos+1, actualizado_en=? WHERE id=?",
        (estado, (error or None), http_status, ahora(), tarea_id),
    )
    con.commit()


def devolver_tarea(con: sqlite3.Connection, tarea_id: int, error: str = None) -> None:
    """
    Devuelve una tarea a la cola SIN contarla como intento.

    Es para el muro de captcha: la tarea no fallo, se corto la sesion.
    """
    con.execute(
        "UPDATE cosecha_tareas SET estado='pendiente', ultimo_error=?, "
        "actualizado_en=? WHERE id=?",
        (error, ahora(), tarea_id),
    )
    con.commit()


def liberar_huerfanas(con: sqlite3.Connection, minutos: int = 30) -> int:
    """
    Devuelve a 'pendiente' las tareas que quedaron 'en_curso' de una corrida
    que se murio. Un Ctrl-C o un cuelgue cuestan, como mucho, un item.
    """
    limite = datetime.now(timezone.utc).timestamp() - minutos * 60
    limite_iso = datetime.fromtimestamp(limite, timezone.utc).isoformat(timespec="seconds")
    cur = con.execute(
        "UPDATE cosecha_tareas SET estado='pendiente', actualizado_en=? "
        "WHERE estado='en_curso' AND (tomada_en IS NULL OR tomada_en < ?)",
        (ahora(), limite_iso),
    )
    con.commit()
    return cur.rowcount


def reencolar_errores(con: sqlite3.Connection, max_intentos: int = 4, tipo=None) -> int:
    """Vuelve a poner en cola las tareas en error que todavia tienen intentos."""
    params = [max_intentos]
    filtro_tipo = ""
    if tipo:
        filtro_tipo = " AND tipo=?"
        params.append(tipo)
    cur = con.execute(
        f"UPDATE cosecha_tareas SET estado='pendiente' "
        f"WHERE estado='error' AND intentos < ?{filtro_tipo}",
        params,
    )
    con.commit()
    return cur.rowcount


# ═══════════════════════════════════════════════════════════════════════════
#  Archivo crudo y reconciliacion
# ═══════════════════════════════════════════════════════════════════════════

def guardar_crudo(
    con: sqlite3.Connection, tipo: str, clave: str, cuerpo: str, http_status: int = None
) -> None:
    """Archiva la respuesta comprimida para poder re-parsear sin re-cosechar."""
    if not cuerpo:
        return
    con.execute(
        "INSERT INTO respuestas_crudas(tipo, clave, obtenido_en, http_status, cuerpo_gz) "
        "VALUES(?,?,?,?,?) "
        "ON CONFLICT(tipo, clave) DO UPDATE SET "
        "  obtenido_en=excluded.obtenido_en, http_status=excluded.http_status, "
        "  cuerpo_gz=excluded.cuerpo_gz",
        (tipo, clave, ahora(), http_status,
         gzip.compress(cuerpo.encode("utf-8"), compresslevel=6)),
    )


def leer_crudo(con: sqlite3.Connection, tipo: str, clave: str):
    fila = con.execute(
        "SELECT cuerpo_gz FROM respuestas_crudas WHERE tipo=? AND clave=?",
        (tipo, clave),
    ).fetchone()
    if fila is None:
        return None
    return gzip.decompress(fila["cuerpo_gz"]).decode("utf-8")


def iterar_crudos(con: sqlite3.Connection, tipo: str):
    """Itera (clave, html) del archivo crudo, para re-parsear en lote."""
    for fila in con.execute(
        "SELECT clave, cuerpo_gz FROM respuestas_crudas WHERE tipo=? ORDER BY clave",
        (tipo,),
    ):
        yield fila["clave"], gzip.decompress(fila["cuerpo_gz"]).decode("utf-8")


def registrar_esperados(con: sqlite3.Connection, mes: str, esperados: int) -> None:
    """Guarda el 'Se encontraron N registros' que declaro el sitio para un mes."""
    con.execute(
        "INSERT INTO reconciliacion(mes, esperados, verificado_en) VALUES(?,?,?) "
        "ON CONFLICT(mes) DO UPDATE SET esperados=excluded.esperados, "
        "  verificado_en=excluded.verificado_en",
        (mes, esperados, ahora()),
    )


def diferencias_reconciliacion(con: sqlite3.Connection) -> list:
    """
    Meses donde lo guardado no coincide con lo que el sitio dijo que habia.

    Esta es la defensa contra la forma numero uno en que un scraper pierde el
    20% de un corpus sin que nadie se entere: un bug de paginacion silencioso.
    """
    filas = con.execute(
        """
        SELECT r.mes, r.esperados,
               (SELECT COUNT(*) FROM fallos f WHERE f.mes = r.mes) AS guardados
          FROM reconciliacion r
         WHERE r.esperados IS NOT NULL
         ORDER BY r.mes
        """
    ).fetchall()
    return [
        {"mes": f["mes"], "esperados": f["esperados"], "guardados": f["guardados"]}
        for f in filas
        if f["esperados"] != f["guardados"]
    ]


# ═══════════════════════════════════════════════════════════════════════════
#  Estadisticas
# ═══════════════════════════════════════════════════════════════════════════

def estadisticas(con: sqlite3.Connection) -> dict:
    """Resumen del corpus y del progreso de la cosecha."""
    def uno(sql, params=()):
        fila = con.execute(sql, params).fetchone()
        return fila[0] if fila else 0

    tareas = {}
    for fila in con.execute(
        "SELECT tipo, estado, COUNT(*) n FROM cosecha_tareas GROUP BY tipo, estado"
    ):
        tareas.setdefault(fila["tipo"], {})[fila["estado"]] = fila["n"]

    rango = con.execute(
        "SELECT MIN(fecha_fallo) a, MAX(fecha_fallo) b FROM fallos "
        "WHERE fecha_fallo IS NOT NULL"
    ).fetchone()

    return {
        "ruta": leer_meta(con, "ruta", ""),
        "esquema_version": leer_meta(con, "esquema_version"),
        "creado_en": leer_meta(con, "creado_en"),
        "reindexado_en": leer_meta(con, "reindexado_en"),
        "fallos": uno("SELECT COUNT(*) FROM fallos"),
        "fallos_con_detalle": uno("SELECT COUNT(*) FROM fallos WHERE detalle_ok=1"),
        "fallos_con_pdf": uno("SELECT COUNT(*) FROM fallos WHERE pdf_ruta IS NOT NULL"),
        "sumarios": uno("SELECT COUNT(*) FROM sumarios"),
        "sumarios_truncados": uno("SELECT COUNT(*) FROM sumarios WHERE truncado=1"),
        "voces": uno("SELECT COUNT(*) FROM voces"),
        "voces_del_tesauro": uno("SELECT COUNT(*) FROM voces WHERE origen='tesauro'"),
        "votos": uno("SELECT COUNT(*) FROM votos"),
        "documentos_indexados": uno("SELECT COUNT(*) FROM documentos"),
        "fecha_min": rango["a"] if rango else None,
        "fecha_max": rango["b"] if rango else None,
        "tareas": tareas,
        "reconciliacion_pendiente": len(diferencias_reconciliacion(con)),
    }


def exportar_estadisticas_json(con: sqlite3.Connection) -> str:
    return json.dumps(estadisticas(con), ensure_ascii=False, indent=2)
