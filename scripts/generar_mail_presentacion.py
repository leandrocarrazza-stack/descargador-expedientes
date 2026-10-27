#!/usr/bin/env python3
"""
Genera el mail de presentación de Foja.

Plantillas (--plantilla):
    colegio    para difundir entre los colegas (lo reenvía el Colegio). Es la de siempre.
    instituto  mail personal para la directora del Instituto de Derecho Procesal del CAER,
               que acompaña al informe en PDF.

Escribe un HTML listo para pegar en el editor de HTML personalizado de MailerLite
(el link de baja queda como {$unsubscribe}, que MailerLite reemplaza al enviar).
Si el HTML se va a pegar en Gmail u otro cliente que no reemplaza esa variable, usar --sin-baja.
No toca la base de datos ni envía nada. Los precios se leen de config.PLANES, así nunca quedan desactualizados.

Uso:
    python scripts/generar_mail_presentacion.py --salida mail_presentacion.html
    python scripts/generar_mail_presentacion.py --plantilla instituto --sin-baja --salida mail_instituto.html
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


PLANTILLAS = {
    'colegio': 'emails/presentacion_colegio.html',
    'instituto': 'emails/presentacion_instituto.html',
}


def contexto(config, base_url, con_baja=True):
    base = base_url.rstrip('/')
    return {
        'url_registro': f'{base}/auth/signup',
        'url_sitio': base,
        'url_baja': '{$unsubscribe}' if con_baja else '',
        'remitente': 'asistenciafoja@gmail.com',
        'anio': 2026,
        'asunto': 'Foja: la forma más simple de tener todo el expediente a mano',
    }


def renderizar(app, plantilla='colegio', con_baja=True):
    import config
    from flask import render_template
    with app.app_context():
        return render_template(PLANTILLAS[plantilla], **contexto(config, config.BASE_URL, con_baja))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--salida', default='mail_presentacion.html', help='archivo HTML de salida')
    ap.add_argument('--plantilla', choices=sorted(PLANTILLAS), default='colegio', help='qué mail generar (por defecto: colegio)')
    ap.add_argument('--sin-baja', action='store_true', help='omitir el link de baja de MailerLite (para pegar el HTML en Gmail)')
    args = ap.parse_args()

    import config
    config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'   # no se usa la base real
    from servidor import crear_app
    html = renderizar(crear_app(), args.plantilla, con_baja=not args.sin_baja)
    Path(args.salida).write_text(html, encoding='utf-8')
    print(f'Listo: {args.salida} ({len(html)} caracteres)')


if __name__ == '__main__':
    main()
