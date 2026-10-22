#!/usr/bin/env python3
"""
Exporta la lista de destinatarios de una promoción (solo lectura sobre la base).

Incluye a los usuarios que:
- tienen crédito disponible (creditos_disponibles > 0),
- no se dieron de baja de las promociones (recibir_promociones distinto de False),
- tienen un email con formato válido.

Con --todos incluye también a los de 0 créditos (útil para la campaña "recomenda").

Escribe un CSV (email, nombre, creditos, url_baja, url_referido) y, con --html-dir, un
archivo .html ya personalizado por destinatario (con su link de baja).

Uso (con las variables de entorno de producción cargadas: DATABASE_URL, SECRET_KEY, BASE_URL):
    python scripts/exportar_destinatarios_promo.py --salida destinatarios.csv
    python scripts/exportar_destinatarios_promo.py --salida destinatarios.csv --html-dir mails/

Para enviar: desde asistenciafoja@gmail.com, un mail por destinatario (así el
link de baja es personal). Una cuenta de Gmail personal permite unos 500
envíos por día; si la lista crece, conviene una herramienta de envío.
"""

import argparse
import csv
import re
import sys
from pathlib import Path

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


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--salida', default='destinatarios_promo.csv', help='archivo CSV de salida')
    ap.add_argument('--campana', default='contador_plazos', help='campaña (plantilla en templates/emails/)')
    ap.add_argument('--html-dir', help='si se indica, guarda un .html personalizado por destinatario')
    ap.add_argument('--todos', action='store_true', help='incluir también a los usuarios con 0 créditos')
    args = ap.parse_args()

    from servidor import crear_app
    from modulos.models import User
    from modulos.promos import renderizar_promo, url_baja
    from modulos.referidos import url_referido

    app = crear_app()
    with app.app_context():
        lista = destinatarios(User, todos=args.todos)
        with open(args.salida, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(['email', 'nombre', 'creditos', 'url_baja', 'url_referido'])
            for u in lista:
                w.writerow([u.email, u.nombre or '', u.creditos_disponibles, url_baja(u.id), url_referido(u.id)])
        print(f'{len(lista)} destinatarios → {args.salida}')

        if args.html_dir:
            carpeta = Path(args.html_dir)
            carpeta.mkdir(parents=True, exist_ok=True)
            for u in lista:
                mail = renderizar_promo(args.campana, u)
                (carpeta / f'promo_{u.id}.html').write_text(mail['html'], encoding='utf-8')
            print(f'{len(lista)} mails personalizados → {carpeta}/  (asunto: {mail["asunto"] if lista else ""})')


if __name__ == '__main__':
    main()
