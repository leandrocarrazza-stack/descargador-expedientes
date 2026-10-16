#!/usr/bin/env python3
"""
Envía el mail promocional a los clientes, uno por uno, desde asistenciafoja@gmail.com.

Cada cliente recibe su propio mail, con su nombre y su link de baja personal. Los
destinatarios son los mismos que arma exportar_destinatarios_promo.py: clientes con
crédito disponible, que no se dieron de baja y con un email válido.

Cómo funciona (pensado para no equivocarse):
- Por defecto NO envía nada: muestra cuántos mails saldrían (simulacro).
- Para enviar de verdad hay que agregar --enviar, y el script pide confirmación.
- Manda de a poco: 25 mails por corrida (--limite) con una pausa entre cada uno
  (--pausa), para que Gmail no lo tome por envío masivo.
- Lleva un registro (envios_promo.csv): si lo corrés de nuevo, sigue donde quedó y
  no le repite el mail a nadie.
- Si Gmail frena el envío (límite diario, problema de la cuenta), se detiene solo.

Uso (con las variables de entorno de producción cargadas: FLASK_ENV=production,
PLAZOS_SYNC_AUTO=false, BASE_URL, DATABASE_URL y SECRET_KEY):

    1) Probar con tu propia cuenta (manda UN mail a esa dirección):
       python scripts/enviar_promo.py --prueba tu@email.com

    2) Ver qué saldría, sin enviar:
       python scripts/enviar_promo.py

    3) Enviar una tanda de 25:
       python scripts/enviar_promo.py --enviar
       (repetir los días siguientes hasta terminar la lista)

Contraseña: Gmail no deja usar la contraseña normal. Hay que crear una "contraseña de
aplicación" (Cuenta de Google de asistenciafoja → Seguridad → Verificación en 2 pasos
→ Contraseñas de aplicaciones). El script la pide al empezar, sin mostrarla; también
puede cargarse en la variable GMAIL_APP_PASSWORD. No se guarda en ningún archivo.
"""

import argparse
import csv
import getpass
import os
import random
import smtplib
import ssl
import sys
import time
from datetime import datetime
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

SMTP_HOST, SMTP_PORT = 'smtp.gmail.com', 465
LIMITE_POR_DEFECTO = 25
PAUSA_POR_DEFECTO = 15           # segundos; se le suma un poco al azar
MAX_ERRORES_SEGUIDOS = 3         # si fallan 3 seguidos, algo anda mal: se frena
LOG_POR_DEFECTO = 'envios_promo.csv'
ESTADOS_TERMINADOS = ('enviado', 'rechazado')   # a estos no se les reintenta


def _norm(email):
    return (email or '').strip().lower()


def leer_registrados(ruta):
    """Emails a los que ya no hay que mandarles (enviados, o que Gmail rechazó)."""
    ruta = Path(ruta)
    if not ruta.exists():
        return set()
    with open(ruta, newline='', encoding='utf-8-sig') as f:
        return {_norm(r.get('email')) for r in csv.DictReader(f) if r.get('estado') in ESTADOS_TERMINADOS}


def registrar(ruta, email, estado, detalle=''):
    """Agrega una línea al registro de envíos (no hace nada si ruta es None)."""
    if ruta is None:
        return
    ruta = Path(ruta)
    nuevo = not ruta.exists()
    with open(ruta, 'a', newline='', encoding='utf-8-sig' if nuevo else 'utf-8') as f:
        w = csv.writer(f)
        if nuevo:
            w.writerow(['email', 'fecha_hora', 'estado', 'detalle'])
        w.writerow([email, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), estado, detalle])


def construir_mensaje(mail, destinatario, link_baja):
    """Arma el correo (texto + HTML) listo para enviar por SMTP."""
    msg = EmailMessage()
    msg['Subject'] = mail['asunto']
    msg['From'] = formataddr(('Foja', mail['remitente']))
    msg['To'] = destinatario
    msg['Reply-To'] = mail['remitente']
    msg['Date'] = formatdate(localtime=True)
    msg['Message-ID'] = make_msgid(domain='foja.com.ar')
    # Gmail y Outlook muestran un enlace "cancelar suscripción" arriba del mail con esto
    msg['List-Unsubscribe'] = f'<{link_baja}>'
    msg.set_content(mail['texto'])
    msg.add_alternative(mail['html'], subtype='html')
    return msg


def enviar_lote(smtp, usuarios, campana, log, limite, pausa, dormir=time.sleep):
    """
    Envía a `usuarios` (hasta `limite`) por la conexión `smtp`. Debe llamarse dentro
    de un contexto de aplicación Flask. Devuelve un resumen:
    {'enviados', 'rechazados', 'errores', 'detenido'} (detenido = motivo, o None).
    """
    from modulos.promos import renderizar_promo, url_baja

    resumen = {'enviados': 0, 'rechazados': 0, 'errores': 0, 'detenido': None}
    intentos = errores_seguidos = 0
    total = min(limite, len(usuarios))

    for u in usuarios:
        if intentos >= limite:
            break
        if intentos > 0:
            dormir(pausa + random.uniform(0, pausa / 2))
        intentos += 1
        email = (u.email or '').strip()
        try:
            mail = renderizar_promo(campana, u)
            smtp.send_message(construir_mensaje(mail, email, url_baja(u.id)))
        except smtplib.SMTPRecipientsRefused as e:
            registrar(log, email, 'rechazado', str(e)[:200])
            resumen['rechazados'] += 1
            errores_seguidos = 0
            print(f'[{intentos}/{total}] rechazado por Gmail → {email}')
        except (smtplib.SMTPDataError, smtplib.SMTPSenderRefused, smtplib.SMTPServerDisconnected,
                smtplib.SMTPAuthenticationError) as e:
            # Probable límite diario o problema de la cuenta: no se registra, para reintentar mañana
            resumen['detenido'] = f'Gmail frenó el envío ({e.__class__.__name__}: {str(e)[:150]})'
            break
        except Exception as e:  # noqa: BLE001 - un mail con problemas no debe frenar a los demás
            registrar(log, email, 'error', f'{e.__class__.__name__}: {str(e)[:150]}')
            resumen['errores'] += 1
            errores_seguidos += 1
            print(f'[{intentos}/{total}] ERROR → {email}: {e}')
            if errores_seguidos >= MAX_ERRORES_SEGUIDOS:
                resumen['detenido'] = f'{MAX_ERRORES_SEGUIDOS} errores seguidos'
                break
        else:
            registrar(log, email, 'enviado')
            resumen['enviados'] += 1
            errores_seguidos = 0
            print(f'[{intentos}/{total}] enviado → {email}')
    return resumen


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--campana', default='contador_plazos', help='campaña (plantilla en templates/emails/)')
    ap.add_argument('--enviar', action='store_true', help='enviar de verdad (sin esto es solo un simulacro)')
    ap.add_argument('--prueba', metavar='EMAIL', help='manda UN mail de prueba a esta cuenta de Foja')
    ap.add_argument('--limite', type=int, default=LIMITE_POR_DEFECTO, help=f'máximo de mails por corrida (default {LIMITE_POR_DEFECTO})')
    ap.add_argument('--pausa', type=int, default=PAUSA_POR_DEFECTO, help=f'segundos entre mails (default {PAUSA_POR_DEFECTO})')
    ap.add_argument('--log', default=LOG_POR_DEFECTO, help='registro de envíos (CSV)')
    args = ap.parse_args()

    from servidor import crear_app
    from modulos.models import User
    from modulos.promos import REMITENTE
    from scripts.exportar_destinatarios_promo import destinatarios, usuario_por_email

    app = crear_app()
    with app.app_context():
        if args.prueba:
            u = usuario_por_email(User, args.prueba)
            if u is None:
                sys.exit(f'No hay ningún usuario con el email {args.prueba}. No se envió nada.')
            pendientes, log, limite = [u], None, 1     # la prueba no se anota en el registro
        else:
            lista = destinatarios(User)
            ya = leer_registrados(args.log)
            pendientes = [u for u in lista if _norm(u.email) not in ya]
            log, limite = args.log, max(1, args.limite)
            print(f'Destinatarios: {len(lista)} · ya enviados: {len(lista) - len(pendientes)} · '
                  f'pendientes: {len(pendientes)} · esta corrida: {min(limite, len(pendientes))}')
            if not pendientes:
                print('No queda nadie por enviar.')
                return
            if not args.enviar:
                print('\nSIMULACRO: no se envió nada. Los primeros de esta corrida:')
                for u in pendientes[:min(limite, 5)]:
                    print('  -', u.email)
                print('\nPara enviar de verdad, volvé a correrlo agregando --enviar')
                return
            if input(f'¿Enviar {min(limite, len(pendientes))} mails ahora desde {REMITENTE}? (s/n): ').strip().lower() != 's':
                print('Cancelado. No se envió nada.')
                return

        clave = (os.environ.get('GMAIL_APP_PASSWORD') or
                 getpass.getpass(f'Contraseña de aplicación de Gmail ({REMITENTE}): ')).replace(' ', '')
        try:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=ssl.create_default_context(), timeout=60) as smtp:
                smtp.login(REMITENTE, clave)
                resumen = enviar_lote(smtp, pendientes, args.campana, log, limite, args.pausa)
        except smtplib.SMTPAuthenticationError:
            sys.exit('Gmail rechazó la contraseña. Tiene que ser una "contraseña de aplicación" (16 letras) '
                     'de la cuenta asistenciafoja, con la verificación en 2 pasos activada. No se envió nada.')

        print(f'\nListo: {resumen["enviados"]} enviados · {resumen["rechazados"]} rechazados · {resumen["errores"]} con error.')
        if resumen['detenido']:
            print('El envío se detuvo:', resumen['detenido'])
            print('Podés volver a correrlo más tarde (o mañana): sigue donde quedó.')


if __name__ == '__main__':
    main()
