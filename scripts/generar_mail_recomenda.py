#!/usr/bin/env python3
"""
Genera el mail "Recomendá Foja" para pegar en el editor de HTML personalizado de MailerLite.

Los datos de cada persona quedan como marcadores que MailerLite reemplaza al enviar:
    {$name|default('colega')}   saludo con el primer nombre
    {$link_referido}            campo personalizado con el link personal de cada cliente
    {$link_whatsapp}            campo personalizado con el link de "Compartir por WhatsApp"
    {$unsubscribe}              link de baja
Los dos campos personalizados se cargan en MailerLite con las columnas url_referido y
url_whatsapp del CSV de scripts/exportar_destinatarios_promo.py (--campana recomenda --todos).
No toca la base de datos.

Uso:
    python scripts/generar_mail_recomenda.py --salida mail_recomenda.html
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--salida', default='mail_recomenda.html', help='archivo HTML de salida')
    args = ap.parse_args()

    import config
    config.SQLALCHEMY_DATABASE_URI = 'sqlite:///:memory:'   # no se usa la base real
    from servidor import crear_app
    from modulos.promos import renderizar_promo_mailerlite, ASUNTOS
    app = crear_app()
    with app.app_context():
        html = renderizar_promo_mailerlite('recomenda')
    Path(args.salida).write_text(html, encoding='utf-8')
    print(f'Listo: {args.salida} ({len(html)} caracteres)')
    print(f'Asunto sugerido: {ASUNTOS["recomenda"]}')


if __name__ == '__main__':
    main()
