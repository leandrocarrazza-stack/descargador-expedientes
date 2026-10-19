#!/usr/bin/env python3
"""
Genera el mail de presentación de Foja para difundir entre los colegas (lo reenvía el Colegio).

Escribe un HTML listo para pegar en el editor de HTML personalizado de MailerLite
(el link de baja queda como {$unsubscribe}, que MailerLite reemplaza al enviar).
No toca la base de datos. Los precios se leen de config.PLANES, así nunca quedan desactualizados.

Uso:
    python scripts/generar_mail_presentacion.py --salida mail_presentacion.html
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def contexto(config, base_url):
    base = base_url.rstrip('/')
    return {
        'url_registro': f'{base}/auth/signup',
        'url_sitio': base,
        'url_baja': '{$unsubscribe}',
        'remitente': 'asistenciafoja@gmail.com',
        'anio': 2026,
        'asunto': 'Foja: la forma más simple de tener todo el expediente a mano',
    }


def renderizar(app):
    import config
    from flask import render_template
    with app.app_context():
        return render_template('emails/presentacion_colegio.html', **contexto(config, config.BASE_URL))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--salida', default='mail_presentacion.html', help='archivo HTML de salida')
    args = ap.parse_args()

    import config
    config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'   # no se usa la base real
    from servidor import crear_app
    html = renderizar(crear_app())
    Path(args.salida).write_text(html, encoding='utf-8')
    print(f'Listo: {args.salida} ({len(html)} caracteres)')


if __name__ == '__main__':
    main()
