#!/usr/bin/env python3
"""
Exporta la lista de destinatarios de una promoción (solo lectura sobre la base).

Incluye a los usuarios que:
- tienen crédito disponible (creditos_disponibles > 0),
- no se dieron de baja de las promociones (recibir_promociones distinto de False),
- tienen un email con formato válido.

Escribe un CSV (email, nombre, creditos, url_baja) y, con --html-dir, un
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


def destinatarios(User):
    """Usuarios a los que corresponde mandarles la promoción."""
    q = User.query.filter(User.creditos_disponibles > 0).order_by(User.id)
    return [u for u in q.all()
            if u.recibir_promociones is not False and EMAIL_RE.match((u.email or '').strip())]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--salida', default='destinatarios_promo.csv', help='archivo CSV de salida')
    ap.add_argument('--campana', default='contador_plazos', help='campaña (plantilla en templates/emails/)')
    ap.add_argument('--html-dir', help='si se indica, guarda un .html personalizado por destinatario')
    args = ap.parse_args()

    from servidor import crear_app
    from modulos.models import User
    from modulos.promos import renderizar_promo, url_baja

    app = crear_app()
    with app.app_context():
        lista = destinatarios(User)
        with open(args.salida, 'w', newline='', encoding='utf-8-sig') as f:
            w = csv.writer(f)
            w.writerow(['email', 'nombre', 'creditos', 'url_baja'])
            for u in lista:
                w.writerow([u.email, u.nombre or '', u.creditos_disponibles, url_baja(u.id)])
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
