#!/usr/bin/env python3
"""
Genera los dos mails del aumento de precios (aviso y recordatorio) en HTML para MailerLite.
Los precios se leen de config.PLANES_ANTERIORES / PLANES_NUEVOS. No toca la base de datos.

Uso:
    python scripts/generar_mail_aumento.py --carpeta mails
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--carpeta', default='mails', help='carpeta de salida')
    args = ap.parse_args()

    import config
    config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'   # no se usa la base real
    from servidor import crear_app
    from modulos.promos import renderizar_promo_mailerlite, ASUNTOS
    carpeta = Path(args.carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    app = crear_app()
    with app.app_context():
        for campana in ('aumento_precios', 'aumento_recordatorio'):
            html = renderizar_promo_mailerlite(campana)
            ruta = carpeta / f'{campana}.html'
            ruta.write_text(html, encoding='utf-8')
            print(f'{ruta} ({len(html)} caracteres) - asunto: {ASUNTOS[campana]}')


if __name__ == '__main__':
    main()
