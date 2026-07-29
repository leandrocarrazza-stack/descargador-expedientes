#!/usr/bin/env python3
"""
CLI de jurisprudencia STJER
===========================

    python -m scripts.stjer <subcomando>

Flujo tipico (la primera vez):

    1. descubrir              revisa los artefactos de la Fase 0
    2. sesion --abrir         resuelve el captcha UNA vez y guarda las cookies
    3. tesauro --cosechar     trae el tesauro real
    4. cosechar listas        pasada A: el corpus ya queda buscable
    5. cosechar detalles      pasada B: voces, votos y sumarios completos

Y despues, todos los dias:

    python -m scripts.stjer buscar "prescripcion liberatoria" --fuero civil

Las busquedas son locales: no tocan la red, responden en milisegundos y no
necesitan Playwright ni requests instalados.
"""

import argparse
import json
import logging
import sys
from datetime import date
from pathlib import Path

# Permite ejecutar el archivo directo (python scripts/stjer.py) ademas de
# como modulo (python -m scripts.stjer).
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from modulos.jurisprudencia.stjer import ajustes, busqueda, corpus  # noqa: E402

logger = logging.getLogger("stjer")

CODIGO_CAPTCHA = 3  # salida distinta para que un script sepa que reaccionar


# Comandos de solo lectura: su salida la lee un humano (o Claude), asi que el
# log no tiene que ensuciarla. Los de cosecha si loguean progreso: son corridas
# de horas y hay que poder ver que estan haciendo.
COMANDOS_SILENCIOSOS = {"buscar", "voces", "fallo", "estado", "descubrir"}


def _configurar_log(comando: str, verboso: bool) -> None:
    if verboso:
        nivel = logging.DEBUG
    elif comando in COMANDOS_SILENCIOSOS:
        nivel = logging.WARNING
    else:
        nivel = logging.INFO

    logging.basicConfig(
        level=nivel,
        format="%(asctime)s %(levelname)-7s %(message)s",
        datefmt="%H:%M:%S",
        stream=sys.stderr,
    )
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def _abrir_corpus(solo_lectura: bool = False):
    ajustes.asegurar_directorios()
    return corpus.abrir(ajustes.CORPUS_PATH, solo_lectura=solo_lectura)


def _mes_a_fecha(texto: str, inicio: bool) -> date:
    """'2019' o '2019-03' -> date. Es como se escriben los rangos en el CLI."""
    partes = texto.split("-")
    a = int(partes[0])
    if len(partes) == 1:
        return date(a, 1, 1) if inicio else date(a, 12, 31)
    m = int(partes[1])
    if inicio:
        return date(a, m, 1)
    import calendar

    return date(a, m, calendar.monthrange(a, m)[1])


def _construir_cliente(args):
    """
    Arma el cliente segun la rama que haya decidido la Fase 0.

    Esta es la unica funcion que cambia entre las ramas A, B y C.
    """
    from modulos.jurisprudencia.stjer.cliente import (
        ClienteHTTP, ClienteNavegador, FormatoConsulta, Regulador,
    )
    from modulos.jurisprudencia.stjer.sesion import SesionSTJER, cargar_credenciales

    regulador = Regulador(espera=args.espera)

    if args.motor == "navegador":
        sesion = SesionSTJER(headless=not args.visible)
        sesion.__enter__()
        sesion.abrir()
        return ClienteNavegador(sesion, regulador=regulador), sesion

    credenciales = cargar_credenciales()
    if not credenciales.get("cookies"):
        print(
            "\n  No hay una sesion guardada. Abrila primero con:\n"
            "      python -m scripts.stjer sesion --abrir\n",
            file=sys.stderr,
        )
        sys.exit(CODIGO_CAPTCHA)

    cliente = ClienteHTTP(
        cookies=credenciales["cookies"],
        ah=credenciales.get("ah"),
        formato=FormatoConsulta.cargar(),
        regulador=regulador,
        ah_fijo=args.motor == "http-fijo",
    )
    return cliente, None


# ═══════════════════════════════════════════════════════════════════════════
#  descubrir
# ═══════════════════════════════════════════════════════════════════════════

PREGUNTAS_FASE0 = [
    ("captura.har", "Captura de red completa (DevTools -> Save all as HAR)"),
    ("00_inicial.html", "HTML de la pagina ANTES de resolver el captcha"),
    ("01_listado.html", "HTML de una pagina de resultados"),
    ("02_listado_p2.html", "HTML de la segunda pagina (para ver la paginacion)"),
    ("03_detalle.html", "HTML de la vista de detalle de un fallo"),
    ("04_tesauro.html", "HTML del panel del Tesauro"),
    ("vida_sesion.txt", "A los cuantos minutos de inactividad vuelve a pedir captcha"),
    ("regla_pdf.txt", "20 pares (caratula -> nombre del PDF) y la regla inferida"),
]

AJUSTABLES = [
    ("perfil.json", "Encabezados y patrones del parser"),
    ("selectores.json", "Selectores CSS del formulario"),
    ("formato_consulta.json", "Nombres de los campos del POST de busqueda"),
]


def cmd_descubrir(args) -> int:
    """Revisa que artefactos de la Fase 0 estan y cuales faltan."""
    d = ajustes.DESCUBRIMIENTO_DIR
    d.mkdir(parents=True, exist_ok=True)

    print(f"\nArtefactos de la Fase 0 en {d}\n")
    faltan = 0
    for archivo, descripcion in PREGUNTAS_FASE0:
        ruta = d / archivo
        if ruta.exists() and ruta.stat().st_size > 0:
            print(f"  [OK]    {archivo:24} {ruta.stat().st_size:>9,} bytes")
        else:
            faltan += 1
            print(f"  [FALTA] {archivo:24} {descripcion}")

    print("\nAjustes derivados (opcionales, se generan a partir de lo de arriba):\n")
    for archivo, descripcion in AJUSTABLES:
        ruta = d / archivo
        marca = "[OK]   " if ruta.exists() else "[-]    "
        print(f"  {marca} {archivo:24} {descripcion}")

    # Comprobacion de entorno: FTS5 es un requisito duro.
    import sqlite3

    try:
        c = sqlite3.connect(":memory:")
        c.execute(
            'CREATE VIRTUAL TABLE t USING fts5(x, tokenize="unicode61 remove_diacritics 2")'
        )
        print(f"\n  [OK]    FTS5 disponible (SQLite {sqlite3.sqlite_version})")
    except sqlite3.OperationalError as e:
        print(f"\n  [ERROR] Tu Python no trae FTS5: {e}")
        return 1

    if faltan:
        print(
            f"\n  Faltan {faltan} artefactos. El procedimiento esta en "
            f"docs/STJER_FASE0.md\n"
        )
        return 1

    print("\n  Fase 0 completa. Siguiente: python -m scripts.stjer sesion --abrir\n")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
#  sesion
# ═══════════════════════════════════════════════════════════════════════════

def cmd_sesion(args) -> int:
    """Abre el navegador, resuelve el captcha y exporta las cookies."""
    from modulos.jurisprudencia.stjer.sesion import (
        ErrorSesion, ResolvedorArchivo, ResolvedorManual, SesionSTJER,
    )

    ajustes.asegurar_directorios()
    resolvedor = ResolvedorArchivo() if args.captcha_por_archivo else ResolvedorManual()

    try:
        with SesionSTJER(
            headless=not args.visible,
            resolvedor=resolvedor,
            har=ajustes.DESCUBRIMIENTO_DIR / "captura_playwright.har" if args.har else None,
        ) as s:
            s.abrir()
            destino = s.exportar_credenciales()
            print(f"\n  Sesion lista. Credenciales en {destino}")
            print(f"  Token ah: {s.token_ah() or '(no encontrado)'}")

            if args.verificar:
                from modulos.jurisprudencia.stjer.parser import parsear_listado

                listado = parsear_listado(s.html())
                print(f"  Formulario detectado: {'si' if not s.hay_captcha() else 'no'}")
                print(f"  Filas visibles ahora: {len(listado)}")
            print()
        return 0
    except ErrorSesion as e:
        print(f"\n  No se pudo abrir la sesion: {e}\n", file=sys.stderr)
        return CODIGO_CAPTCHA


# ═══════════════════════════════════════════════════════════════════════════
#  tesauro
# ═══════════════════════════════════════════════════════════════════════════

def cmd_tesauro(args) -> int:
    from modulos.jurisprudencia.stjer import tesauro_stjer as T

    if args.desde_corpus:
        con = _abrir_corpus()
        try:
            t = T.importar_desde_corpus(con)
        finally:
            con.close()
    elif args.cosechar:
        cliente, sesion = _construir_cliente(args)
        try:
            t = T.cosechar_arbol(cliente, max_nodos=args.max_nodos)
        finally:
            if sesion:
                sesion.__exit__(None, None, None)
    else:
        t = T.Tesauro.cargar()

    if not t:
        print(
            "\n  No hay tesauro. Cosechalo con:\n"
            "      python -m scripts.stjer tesauro --cosechar\n",
            file=sys.stderr,
        )
        return 1

    print(f"\n  Tesauro v{t.version}")
    print(f"  Materias: {len(t.materias)}")
    print(f"  Voces:    {len(t)}")
    if len(t) <= 10:
        print(
            "\n  Ojo: 10 voces o menos es el tamaño del placeholder viejo.\n"
            "  Si esto salio de una cosecha, el parseo del arbol no funciono.\n"
        )
    if args.listar:
        for m in t.materias:
            print(f"\n  {m['nombre']}")
            for vp in m.get("voces_principales", []):
                print(f"    {vp['nombre']}")
                for voz in vp.get("voces", [])[:20]:
                    print(f"      - {voz}")
    print()
    return 0


def cmd_voces(args) -> int:
    """Traduce una consulta en castellano a voces del tesauro."""
    con = _abrir_corpus(solo_lectura=ajustes.CORPUS_PATH.exists())
    try:
        sugerencias = busqueda.BuscadorCorpus(con).sugerir_voces(
            args.consulta, n=args.limite
        )
    finally:
        con.close()

    if args.formato == "json":
        print(json.dumps(sugerencias, ensure_ascii=False, indent=1))
        return 0

    if not sugerencias:
        print(
            "\n  Ninguna voz coincide. Si todavia no cosechaste el tesauro:\n"
            "      python -m scripts.stjer tesauro --cosechar\n"
        )
        return 0

    print(f"\n  Voces sugeridas para: {args.consulta!r}\n")
    for s in sugerencias:
        print(f"  {s['puntaje']:.3f}  [{s['origen']:15}]  {s['ruta']}")
    print()
    return 0


# ═══════════════════════════════════════════════════════════════════════════
#  cosechar
# ═══════════════════════════════════════════════════════════════════════════

def cmd_cosechar(args) -> int:
    from modulos.jurisprudencia.stjer.cosecha import (
        TIPO_DETALLE, TIPO_LISTA, Cosechadora,
    )

    con = _abrir_corpus()
    cliente, sesion = _construir_cliente(args)
    try:
        cos = Cosechadora(cliente, con, guardar_crudo=not args.sin_crudo)

        if args.que == "listas":
            desde = _mes_a_fecha(args.desde, inicio=True)
            hasta = _mes_a_fecha(args.hasta, inicio=False)
            cos.planificar_listados(desde, hasta)
            tipo = TIPO_LISTA
        else:
            cos.planificar_detalles(limite=args.limite)
            tipo = TIPO_DETALLE

        if args.seco:
            pendientes = con.execute(
                "SELECT COUNT(*) FROM cosecha_tareas WHERE tipo=? AND estado!='ok'",
                (tipo,),
            ).fetchone()[0]
            print(f"\n  [modo seco] {pendientes} tareas de tipo {tipo} pendientes\n")
            return 0

        resumen = cos.ejecutar(tipo, limite=args.limite, orden=args.orden)
    finally:
        if sesion:
            sesion.__exit__(None, None, None)
        corpus.reconstruir_documentos(con)
        con.close()

    print("\n  " + json.dumps(resumen.como_dict(), ensure_ascii=False, indent=2))
    if resumen.abortada_por:
        print(f"\n  {resumen.abortada_por}\n", file=sys.stderr)
        return CODIGO_CAPTCHA if "verificacion" in resumen.abortada_por else 1
    print()
    return 0


def cmd_reparar(args) -> int:
    from modulos.jurisprudencia.stjer.cosecha import Cosechadora

    con = _abrir_corpus()
    try:
        # No hace falta cliente para reencolar.
        resultado = Cosechadora(None, con).reparar()
    finally:
        con.close()
    print("\n  " + json.dumps(resultado, ensure_ascii=False, indent=2) + "\n")
    return 0


def cmd_reparsear(args) -> int:
    """Vuelve a parsear las respuestas archivadas. No toca la red."""
    from modulos.jurisprudencia.stjer.cosecha import reparsear_crudos

    con = _abrir_corpus()
    try:
        resultado = reparsear_crudos(con, tipo=args.tipo)
    finally:
        con.close()
    print("\n  " + json.dumps(resultado, ensure_ascii=False, indent=2) + "\n")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
#  buscar
# ═══════════════════════════════════════════════════════════════════════════

def cmd_buscar(args) -> int:
    if not ajustes.CORPUS_PATH.exists():
        print(
            "\n  Todavia no hay corpus local. Construilo con:\n"
            "      python -m scripts.stjer sesion --abrir\n"
            "      python -m scripts.stjer cosechar listas\n",
            file=sys.stderr,
        )
        return 1

    con = _abrir_corpus(solo_lectura=True)
    try:
        b = busqueda.BuscadorCorpus(con)
        try:
            resultados = b.buscar(
                args.consulta,
                voces=args.voz,
                fuero=args.fuero,
                organismo=args.organismo,
                juez=args.juez,
                desde=args.desde,
                hasta=args.hasta,
                limite=args.limite,
            )
        except busqueda.ErrorBusqueda as e:
            print(f"\n  {e}\n", file=sys.stderr)
            return 1
    finally:
        con.close()

    if args.formato == "json":
        print(busqueda.a_json(resultados, compacto=not args.completo))
    else:
        print(busqueda.a_markdown(resultados, compacto=not args.completo))
    return 0


def cmd_fallo(args) -> int:
    con = _abrir_corpus(solo_lectura=True)
    try:
        identificador = int(args.id) if args.id.isdigit() else args.id
        datos = busqueda.BuscadorCorpus(con).obtener_fallo(identificador)
    finally:
        con.close()

    if not datos:
        print(f"\n  No hay ningun fallo con id/clave {args.id!r}\n", file=sys.stderr)
        return 1
    print(json.dumps(datos, ensure_ascii=False, indent=1, default=str))
    return 0


# ═══════════════════════════════════════════════════════════════════════════
#  pdf y estado
# ═══════════════════════════════════════════════════════════════════════════

def cmd_pdf(args) -> int:
    from modulos.jurisprudencia.stjer import pdf as P

    con = _abrir_corpus()
    try:
        if args.todos:
            resultado = P.descargar_faltantes(
                con, limite=args.limite, concurrencia=args.concurrencia
            )
            print("\n  " + json.dumps(resultado, ensure_ascii=False, indent=2) + "\n")
            return 0 if resultado["errores"] == 0 else 1

        if not args.fallo:
            print("\n  Indica --fallo <id|clave> o --todos\n", file=sys.stderr)
            return 1

        identificador = int(args.fallo) if args.fallo.isdigit() else args.fallo
        r = P.asegurar_pdf(con, identificador, forzar=args.forzar)
    finally:
        con.close()

    if not r.ok:
        print(f"\n  {r.error}\n", file=sys.stderr)
        return 1
    print(f"\n  {r.ruta}  ({r.bytes:,} bytes)\n")
    return 0


def cmd_estado(args) -> int:
    if not ajustes.CORPUS_PATH.exists():
        print(
            f"\n  Todavia no hay corpus en {ajustes.CORPUS_PATH}\n"
            f"  Empezá por: python -m scripts.stjer descubrir\n"
        )
        return 1

    con = _abrir_corpus(solo_lectura=True)
    try:
        stats = corpus.estadisticas(con)
        diferencias = corpus.diferencias_reconciliacion(con)
    finally:
        con.close()

    if args.formato == "json":
        stats["reconciliacion"] = diferencias[:50]
        print(json.dumps(stats, ensure_ascii=False, indent=2))
        return 0

    print(f"\n  Corpus: {ajustes.CORPUS_PATH}")
    tam = ajustes.CORPUS_PATH.stat().st_size / 1e6
    print(f"  Tamaño: {tam:,.1f} MB\n")
    print(f"  Fallos:              {stats['fallos']:>8,}")
    print(f"    con detalle:       {stats['fallos_con_detalle']:>8,}")
    print(f"    con PDF local:     {stats['fallos_con_pdf']:>8,}")
    print(f"  Sumarios:            {stats['sumarios']:>8,}")
    print(f"    solo extracto:     {stats['sumarios_truncados']:>8,}")
    print(f"  Voces:               {stats['voces']:>8,}")
    print(f"  Votos:               {stats['votos']:>8,}")
    print(f"  Indexados (FTS5):    {stats['documentos_indexados']:>8,}")
    if stats["fecha_min"]:
        print(f"  Rango:               {stats['fecha_min']} a {stats['fecha_max']}")

    if stats["tareas"]:
        print("\n  Cola de cosecha:")
        for tipo, estados in sorted(stats["tareas"].items()):
            total = sum(estados.values())
            ok = estados.get("ok", 0)
            resto = ", ".join(
                f"{e}={n}" for e, n in sorted(estados.items()) if e != "ok"
            )
            print(f"    {tipo:12} {ok:>6,}/{total:<6,} ok" + (f"   ({resto})" if resto else ""))

    if diferencias:
        print(
            f"\n  {len(diferencias)} meses donde lo guardado no coincide con lo "
            f"que declaro el sitio."
        )
        for d in diferencias[:5]:
            print(f"    {d['mes']}: guardados {d['guardados']} de {d['esperados']}")
        print("    Re-encolalos con: python -m scripts.stjer reparar")
    print()
    return 0


def cmd_reindexar(args) -> int:
    con = _abrir_corpus()
    try:
        n = corpus.reconstruir_documentos(con)
    finally:
        con.close()
    print(f"\n  Reindexados {n:,} sumarios\n")
    return 0


# ═══════════════════════════════════════════════════════════════════════════
#  Parser de argumentos
# ═══════════════════════════════════════════════════════════════════════════

def construir_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="stjer",
        description="Cosecha y busqueda de jurisprudencia del STJER (Entre Rios)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("-v", "--verboso", action="store_true")
    sub = p.add_subparsers(dest="comando", required=True)

    def agregar_red(sp):
        """Opciones comunes a los subcomandos que salen a la red."""
        sp.add_argument(
            "--motor", choices=["http", "http-fijo", "navegador"], default="http",
            help="http = rama B (default) | http-fijo = rama A | navegador = rama C",
        )
        sp.add_argument("--espera", type=float, default=ajustes.ESPERA_SEG,
                        help="segundos entre requests (default: %(default)s)")
        sp.add_argument("--visible", action="store_true",
                        help="no usar headless (solo con --motor navegador)")

    # descubrir
    sub.add_parser("descubrir", help="Revisa los artefactos de la Fase 0").set_defaults(
        func=cmd_descubrir
    )

    # sesion
    sp = sub.add_parser("sesion", help="Abre la sesion y resuelve el captcha")
    sp.add_argument("--abrir", action="store_true", default=True)
    sp.add_argument("--verificar", action="store_true",
                    help="ademas, informa que se ve en la pagina")
    sp.add_argument("--visible", action="store_true")
    sp.add_argument("--har", action="store_true", help="graba un HAR de la sesion")
    sp.add_argument("--captcha-por-archivo", action="store_true",
                    help="lee el codigo de un archivo en vez de la consola")
    sp.set_defaults(func=cmd_sesion)

    # tesauro
    sp = sub.add_parser("tesauro", help="Cosecha o inspecciona el tesauro real")
    sp.add_argument("--cosechar", action="store_true")
    sp.add_argument("--desde-corpus", action="store_true",
                    help="arma el tesauro con las voces vistas en los fallos")
    sp.add_argument("--listar", action="store_true")
    sp.add_argument("--max-nodos", type=int, default=2000)
    agregar_red(sp)
    sp.set_defaults(func=cmd_tesauro)

    # voces
    sp = sub.add_parser("voces", help="Traduce una consulta a voces del tesauro")
    sp.add_argument("consulta")
    sp.add_argument("-n", "--limite", type=int, default=8)
    sp.add_argument("--formato", choices=["texto", "json"], default="texto")
    sp.set_defaults(func=cmd_voces)

    # cosechar
    sp = sub.add_parser("cosechar", help="Trae datos del sitio")
    sp.add_argument("que", choices=["listas", "detalles"])
    sp.add_argument("--desde", default=str(ajustes.ANIO_INICIO_CORPUS),
                    help="'2004' o '2004-01' (default: %(default)s)")
    sp.add_argument("--hasta", default=date.today().strftime("%Y-%m"))
    sp.add_argument("--limite", type=int,
                    help="cuantas tareas procesar (para trocear la corrida)")
    sp.add_argument("--orden", choices=["reciente", "antiguo"], default="reciente")
    sp.add_argument("--sin-crudo", action="store_true",
                    help="no archivar el HTML (ahorra ~60 MB, pero re-parsear "
                         "obliga a re-cosechar)")
    sp.add_argument("--seco", action="store_true",
                    help="planifica y muestra cuanto falta, sin pedir nada")
    agregar_red(sp)
    sp.set_defaults(func=cmd_cosechar)

    sub.add_parser("reparar", help="Re-encola errores y meses descuadrados").set_defaults(
        func=cmd_reparar
    )

    sp = sub.add_parser("reparsear", help="Re-parsea el HTML archivado, sin red")
    sp.add_argument("--tipo", default="detalle", choices=["detalle", "lista_mes"])
    sp.set_defaults(func=cmd_reparsear)

    # buscar
    sp = sub.add_parser("buscar", help="Busca en el corpus local")
    sp.add_argument("consulta")
    sp.add_argument("--fuero")
    sp.add_argument("--organismo")
    sp.add_argument("--juez")
    sp.add_argument("--voz", action="append", help="se puede repetir")
    sp.add_argument("--desde", help="'2015' o '2015-06'")
    sp.add_argument("--hasta")
    sp.add_argument("-n", "--limite", type=int, default=10)
    sp.add_argument("--formato", choices=["markdown", "json"], default="markdown")
    sp.add_argument("--completo", action="store_true",
                    help="incluye el sumario entero, no solo el fragmento")
    sp.set_defaults(func=cmd_buscar)

    sp = sub.add_parser("fallo", help="Devuelve un fallo completo en JSON")
    sp.add_argument("id", help="id numerico o clave natural")
    sp.set_defaults(func=cmd_fallo)

    # pdf
    sp = sub.add_parser("pdf", help="Descarga PDFs (sin sesion ni captcha)")
    sp.add_argument("--fallo", help="id numerico o clave natural")
    sp.add_argument("--todos", action="store_true", help="modo archivo offline (~3,7 GB)")
    sp.add_argument("--limite", type=int)
    sp.add_argument("--concurrencia", type=int, default=4)
    sp.add_argument("--forzar", action="store_true")
    sp.set_defaults(func=cmd_pdf)

    # estado
    sp = sub.add_parser("estado", help="Progreso de la cosecha y tamaño del corpus")
    sp.add_argument("--formato", choices=["texto", "json"], default="texto")
    sp.set_defaults(func=cmd_estado)

    sub.add_parser("reindexar", help="Reconstruye el indice FTS5").set_defaults(
        func=cmd_reindexar
    )

    return p


def main(argv=None) -> int:
    args = construir_parser().parse_args(argv)
    _configurar_log(args.comando, args.verboso)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        # La cola es durable: cortar no pierde trabajo.
        print("\n  Interrumpido. Volvé a lanzar el mismo comando para retomar.\n")
        return 130


if __name__ == "__main__":
    sys.exit(main())
