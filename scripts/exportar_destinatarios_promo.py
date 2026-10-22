#!/usr/bin/env python3
"""
Exporta la lista de destinatarios de una promoción (solo lectura sobre la base).

Incluye a los usuarios que:
- tienen crédito disponible (creditos_disponibles > 0),
- no se dieron de baja de las promociones (recibir_promociones distinto de False),
- tienen un email con formato válido.

Con --todos incluye también a los de 0 créditos (útil para la campaña "recomenda").

Escribe un CSV (email, nombre, creditos, archivo, url_baja, url_referido) y, con --html-dir, un
archivo .html ya personalizado por destinatario (con su link de baja). Cada archivo
se llama como el email del cliente (ej. mails/cliente@gmail.com.html); la columna
"archivo" del CSV lo indica.

Uso (con las variables de entorno de producción cargadas: DATABASE_URL, SECRET_KEY, BASE_URL):
    python scripts/exportar_destinatarios_promo.py --salida destinatarios.csv
    python scripts/exportar_destinatarios_promo.py --salida destinatarios.csv --html-dir mails/

Para probar el mail con tu propia cuenta antes de la tanda (genera UN solo archivo,
mails/promo_prueba.html, con TU link de baja; no escribe el CSV ni toca a nadie más):
    python scripts/exportar_destinatarios_promo.py --solo tu@email.com

Para enviar: desde asistenciafoja@gmail.com, un mail por destinatario (así el
link de baja es personal). Una cuenta de Gmail personal permite unos 500
envíos por día; si la lista crece, conviene una herramienta de envío.
"""

import argparse
import csv
import re
import sys
from pathlib import Path

from sqlalchemy import func

sys.path.insert(0, str(Path(__file__).parent.parent))

EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def destinatarios(User, todos=False):
    """
    Usuarios a los que corresponde mandarles la promoción. Por defecto solo los
    que tienen crédito; con todos=True también los de 0 créditos (siguen
    excluidos los que se dieron de baja).
    """
    q = User.query.order_by(User.id)
    if not todos:
        q = q.filter(User.creditos_disponibles > 0)
    return [u for u in q.all()
            if u.recibir_promociones is not False and EMAIL_RE.match((u.email or '').strip())]


def nombre_archivo(email):
    """Nombre del .html de un destinatario: su email (así se sabe a quién mandarle cada archivo)."""
    return re.sub(r'[^A-Za-z0-9@._+-]', '_', (email or '').strip().lower()) + '.html'


def usuario_por_email(User, email):
    """El usuario con ese email (sin distinguir mayúsculas), o None."""
    email = (email or '').strip().lower()
    return User.query.filter(func.lower(User.email) == email).first() if email else None


def generar_solo(User, email, carpeta, campana):
    """
    Genera un único mail de prueba para un usuario (con su link de baja personal).
    Devuelve (ruta, mail, motivos), donde motivos lista por qué esa cuenta NO
    entraría en la tanda real (vacío si entra). Sirve igual para probar.
    """
    from modulos.promos import renderizar_promo
    u = usuario_por_email(User, email)
    if u is None:
        return None, None, []
    motivos = []
    if not (u.creditos_disponibles or 0) > 0:
        motivos.append('no tiene crédito disponible')
    if u.recibir_promociones is False:
        motivos.append('se dio de baja de las promociones')
    if not EMAIL_RE.match((u.email or '').strip()):
        motivos.append('el email no tiene formato válido')
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    mail = renderizar_promo(campana, u)
    ruta = carpeta / 'promo_prueba.html'
    ruta.write_text(mail['html'], encoding='utf-8')
    return ruta, mail, motivos


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--salida', default='destinatarios_promo.csv', help='archivo CSV de salida')
    ap.add_argument('--campana', default='contador_plazos', help='campaña (plantilla en templates/emails/)')
    ap.add_argument('--html-dir', help='si se indica, guarda un .html personalizado por destinatario')
    ap.add_argument('--todos', action='store_true', help='incluir también a los usuarios con 0 créditos')
    ap.add_argument('--solo', metavar='EMAIL',
                    help='genera solo el mail de prueba de esta cuenta (promo_prueba.html), sin CSV ni el resto de la lista')
    args = ap.parse_args()

    from servidor import crear_app
    from modulos.models import User
    from modulos.promos import renderizar_promo, url_baja
    from modulos.referidos import url_referido

    app = crear_app()
    with app.app_context():
        if args.solo:
            ruta, mail, motivos = generar_solo(User, args.solo, args.html_dir or 'mails', args.campana)
            if ruta is None:
                sys.exit(f'No hay ningún usuario con el email {args.solo}. No se generó nada.')
            print(f'Mail de prueba → {ruta}  (asunto: {mail["asunto"]})')
            if motivos:
                print('Aviso: esta cuenta no entraría en la tanda real porque ' + ' y '.join(motivos) + '.')
            return
        lista = destinatarios(User, todos=args.todos)
        with open(args.salida, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(['email', 'nombre', 'creditos', 'archivo', 'url_baja', 'url_referido'])
            for u in lista:
                w.writerow([u.email, u.nombre or '', u.creditos_disponibles,
                            nombre_archivo(u.email) if args.html_dir else '', url_baja(u.id), url_referido(u.id)])
        print(f'{len(lista)} destinatarios → {args.salida}')

        if args.html_dir:
            carpeta = Path(args.html_dir)
            carpeta.mkdir(parents=True, exist_ok=True)
            for u in lista:
                mail = renderizar_promo(args.campana, u)
                (carpeta / nombre_archivo(u.email)).write_text(mail['html'], encoding='utf-8')
            print(f'{len(lista)} mails personalizados → {carpeta}/  (asunto: {mail["asunto"] if lista else ""})')


if __name__ == '__main__':
    main()
