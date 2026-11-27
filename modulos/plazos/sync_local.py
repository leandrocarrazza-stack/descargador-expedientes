"""
Actualiza el calendario SIN servidor ni base de datos: lee el sitio oficial del
STJER (jusentrerios.gov.ar), interpreta las novedades con las mismas funciones
que usa Foja (sync_stjer.py) y guarda el resultado en un JSON que el plugin
carga con exportacion.calendario_desde_exportacion().

Hace lo mismo que el sincronizador del servidor, con estas diferencias:
- No tiene memoria entre corridas: cada vez relee todo (página, feria y noticias
  de los últimos 400 días), así que conviene correrlo cada tanto (cada 15 días
  alcanza), no en cada cálculo. El buscador de noticias del sitio es lento
  (~13 s por pedido): una corrida completa puede tardar uno o dos minutos.
- No incluye lo que el admin de Foja carga a mano (entradas 'manual').
- Las noticias que no puede interpretar con certeza NO se aplican: quedan
  listadas en "para_revisar" (dentro del JSON y en pantalla).

Requiere: pip install beautifulsoup4 requests
Archivos del módulo que necesita (además de los de exportacion.py):
sync_local.py, sync_stjer.py, textos.py.

Uso desde el plugin:
    from modulos.plazos.sync_local import actualizar_calendario_local
    resultado = actualizar_calendario_local('calendario_foja.json')

Desde la terminal:
    python -m modulos.plazos.sync_local calendario_foja.json

Códigos de salida: 0 todo bien · 2 se guardó pero alguna fuente falló (mirar el
aviso) · 1 no se guardó nada (el archivo anterior, si había, queda intacto).
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone

from modulos.plazos import exportacion
from modulos.plazos.exportacion import ErrorExportacion

PAUSA_SEG = 0.5          # espera entre pedidos al sitio oficial (no abusar)
PAGINAS_RSS = 8
DIAS_RSS_LOCAL = 400     # noticias de los últimos 400 días (el servidor, con memoria, mira 150 y la 1.ª vez 800)
CONSULTAS_RSS = ('inh%C3%A1bil', 'feria+judicial')


def _sync():
    """sync_stjer pide beautifulsoup4 y requests: si faltan, se explica cómo instalarlos."""
    try:
        from modulos.plazos import sync_stjer
        return sync_stjer
    except ImportError as exc:
        raise ErrorExportacion(
            f'Falta una librería ({exc.name}). Instalala con:  pip install beautifulsoup4 requests')


def _firma(e):
    return (e.desde, e.hasta, e.tipo, e.motivo, e.alcance, e.departamento, e.localidad, e.fuero, e.descuenta)


def _es_404(exc):
    return getattr(getattr(exc, 'response', None), 'status_code', None) == 404


def construir_exportacion(hoy=None, descargar=None, dias_rss=None, pausa=PAUSA_SEG, ahora=None, avisar=None):
    """
    Lee las tres fuentes y devuelve (datos, informe).

    descargar(url) → texto; por defecto el de sync_stjer (solo https del dominio oficial,
    redirecciones validadas, tope de tamaño). Se puede inyectar uno falso para probar sin red.
    avisar(texto): se llama antes de cada pedido (para mostrar el avance).
    informe: {'fuentes': {nombre: {'resultado', 'detalle'?}}, 'problemas': [...], 'para_revisar': [...]}
    """
    s = _sync()
    hoy = hoy or s.hoy_argentina()
    descargar = descargar or s._get
    dias_rss = DIAS_RSS_LOCAL if dias_rss is None else dias_rss
    ahora = ahora or datetime.now(timezone.utc)
    marca = ahora.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    entradas, fuentes, problemas, para_revisar = [], {}, [], []
    pedidos = [0]

    def bajar(url):
        if pedidos[0] and pausa:
            time.sleep(pausa)
        pedidos[0] += 1
        if avisar:
            avisar(f'  leyendo {url}')
        return descargar(url)

    def resultado(nombre, estado, detalle=None):
        fuentes[nombre] = {'fecha': marca, 'resultado': estado}
        if detalle:
            fuentes[nombre]['detalle'] = str(detalle)[:300]

    # 1) Página "Inhábiles y feriados"
    try:
        ents, probs = s.parsear_pagina(bajar(s.URL_PAGINA), hoy.year)
        entradas += ents
        problemas += probs
        resultado('pagina', 'ok')
    except Exception as exc:
        resultado('pagina', 'rechazado', exc)

    # 2) Página de la feria judicial
    try:
        texto = s.BeautifulSoup(bajar(s.URL_FERIA), 'html.parser').get_text(' ', strip=True)
        feria = s.feria_en_texto(texto)
        if feria:
            entradas.append(s._con_fuente(feria, s.URL_FERIA))
            resultado('feria', 'ok')
        else:
            resultado('feria', 'sin_cambios', 'La página de la feria no trae fechas')
    except Exception as exc:
        resultado('feria', 'error', exc)

    # 3) Noticias (RSS): inhábiles por acuerdo y feria de julio
    items, fallos = [], []
    try:
        xmls = [bajar(s.URL_FEED)]
        for q in CONSULTAS_RSS:
            for p in range(1, PAGINAS_RSS + 1):
                try:
                    xmls.append(bajar(s.URL_BUSQUEDA_RSS.format(q=q, p=p)))
                except Exception as exc:
                    if p > 1 and _es_404(exc):
                        break   # WordPress responde 404 cuando se acaban las páginas de resultados
                    fallos.append(f'{q} p{p}: {exc}')
                    break
                pagina = s.parsear_rss(xmls[-1])
                items += pagina
                # los resultados vienen del más nuevo al más viejo: si toda la página ya es más vieja que la
                # ventana, las que siguen también, y no vale la pena pedirlas (el buscador es lento)
                if pagina and all((hoy - it['fecha'].date()).days > dias_rss for it in pagina):
                    break
        items += s.parsear_rss(xmls[0])
    except Exception as exc:
        resultado('rss', 'error', exc)
        items = None
    if items is not None:
        por_link = {}
        for it in items:
            por_link.setdefault(it['link'], it)
        nuevos = 0
        for link, it in sorted(por_link.items(), key=lambda kv: kv[1]['fecha']):
            if (hoy - it['fecha'].date()).days > dias_rss:
                continue
            try:
                r = s.clasificar_noticia(it['titulo'], it['texto'], it['fecha'], link)
            except Exception as exc:
                r = {'estado': 'ambiguo', 'entradas': [], 'nota': f'Error al interpretarla ({type(exc).__name__})'}
            if r['estado'] == 'ignorar':
                continue
            nuevos += 1
            if r['estado'] == 'ok':
                entradas += r['entradas']
            else:
                para_revisar.append({'titulo': it['titulo'], 'link': link, 'nota': r['nota']})
        if fallos:
            # Se usa lo que se pudo leer, pero queda como error para que se note y se reintente
            resultado('rss', 'error', '; '.join(fallos))
        else:
            resultado('rss', 'ok' if nuevos else 'sin_cambios')

    unicas = {}
    for e in entradas:
        unicas.setdefault(_firma(e), e)
    datos = exportacion.armar_exportacion(list(unicas.values()), fuentes, generado=ahora)
    datos['para_revisar'] = para_revisar   # informativo: el lector del calendario lo ignora
    return datos, {'fuentes': fuentes, 'problemas': problemas, 'para_revisar': para_revisar}


def actualizar_calendario_local(destino, descargar=None, hoy=None, dias_rss=None, pausa=PAUSA_SEG, avisar=None, ahora=None):
    """
    Lee el sitio del STJER y guarda el calendario en `destino`. Devuelve un dict con
    'codigo' (0 / 2 / 1, ver el docstring del módulo), 'guardado', 'datos' e 'informe'.
    Nunca pisa con algo peor un archivo anterior: si la página de feriados no se pudo
    leer y ya había un archivo, se conserva el anterior.
    """
    datos, informe = construir_exportacion(hoy=hoy, descargar=descargar, dias_rss=dias_rss, pausa=pausa, avisar=avisar,
                                           ahora=ahora)
    pagina_ok = informe['fuentes']['pagina']['resultado'] == 'ok'
    todo_ok = all(f['resultado'] in ('ok', 'sin_cambios') for f in informe['fuentes'].values())
    ninguna_ok = not any(f['resultado'] in ('ok', 'sin_cambios') for f in informe['fuentes'].values())
    hay_previo = os.path.exists(destino)
    if ninguna_ok:   # sin internet o sitio caído: un calendario vacío no le sirve a nadie
        return {'codigo': 1, 'guardado': False, 'datos': datos, 'informe': informe,
                'motivo': 'No se pudo leer ninguna fuente del STJER (¿sin internet?): no se guardó nada.'
                          + (' Se conserva el archivo anterior.' if hay_previo else '')}
    if not pagina_ok and hay_previo:
        return {'codigo': 1, 'guardado': False, 'datos': datos, 'informe': informe,
                'motivo': 'No se pudo leer la página de feriados e inhábiles: se conserva el archivo anterior.'}
    exportacion.calendario_desde_exportacion(datos)   # que lo escrito siempre se pueda leer
    exportacion.guardar_atomico(datos, destino)
    return {'codigo': 0 if todo_ok else 2, 'guardado': True, 'datos': datos, 'informe': informe, 'motivo': None}


def main(argv=None, descargar=None, hoy=None, pausa=PAUSA_SEG):
    ap = argparse.ArgumentParser(description='Actualiza el calendario de plazos leyendo el sitio del STJER.')
    ap.add_argument('destino', nargs='?', default='calendario_foja.json', help='archivo JSON de salida')
    ap.add_argument('--dias', type=int, default=None,
                    help=f'cuántos días hacia atrás leer las noticias (por defecto {DIAS_RSS_LOCAL}; menos días = más rápido)')
    args = ap.parse_args(argv)
    try:
        print('Leyendo el sitio del STJER (puede tardar uno o dos minutos)...', flush=True)
        r = actualizar_calendario_local(args.destino, descargar=descargar, hoy=hoy, dias_rss=args.dias, pausa=pausa,
                                        avisar=lambda t: print(t, flush=True))
    except ErrorExportacion as exc:
        print(f'Error: {exc}')
        return 1
    inf = r['informe']
    print('Fuentes del STJER:')
    for nombre, f in inf['fuentes'].items():
        print(f'  {nombre:7s} {f["resultado"]}' + (f' — {f["detalle"]}' if f.get('detalle') else ''))
    if r['guardado']:
        print(f'Calendario guardado en {args.destino}: {len(r["datos"]["entradas"])} entradas.')
    else:
        print(f'No se guardó nada. {r["motivo"]}')
    if r['codigo'] == 2:
        print('Aviso: alguna fuente falló; el calendario puede estar incompleto. Volvé a correrlo más tarde.')
    for p in inf['problemas']:
        print(f'  (fila de la página sin interpretar) {p}')
    if inf['para_revisar']:
        print(f'{len(inf["para_revisar"])} noticia(s) que no se pudieron interpretar con certeza (no se aplicaron):')
        for n in inf['para_revisar']:
            print(f'  - {n["titulo"]} ({n["link"]})')
    return r['codigo']


if __name__ == '__main__':
    sys.exit(main())
