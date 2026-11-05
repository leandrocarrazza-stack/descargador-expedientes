# rutas/pagos.py
"""
Rutas para gestión de pagos con Mercado Pago.
Incluye compra de créditos, creación de órdenes, webhooks y confirmaciones.

Modelo: Usuario compra créditos en ARS, cada descarga cuesta $3000 ARS
"""

import os
import logging
from flask import Blueprint, render_template, request, jsonify, redirect, url_for
from flask_login import login_required, current_user
from dotenv import load_dotenv

from modulos.mercado_pago import (
    crear_orden_pago, obtener_pago, buscar_pagos_por_referencia,
    validar_firma_webhook, MercadoPagoError,
)
from modulos.database import db
from modulos.models import CompraCreditos, User
from modulos.referidos import premiar_referidor_si_corresponde
from modulos.emails import enviar_email_premio_referido
from modulos.extensions import limiter, csrf
import config

# Cargar variables de entorno
load_dotenv()

logger = logging.getLogger(__name__)

# Crear blueprint
pagos_bp = Blueprint('pagos', __name__, url_prefix='/pagos')

# Usar planes de config.py (en ARS)
PLANES = config.PLANES
PRECIO_DESCARGA = config.PRECIO_DESCARGA_ARS


@pagos_bp.route('/planes', methods=['GET'])
def mostrar_planes():
    """
    Muestra los planes disponibles para compra.
    Accesible para usuarios autenticados.
    """
    try:
        return render_template('planes.html', planes=PLANES)
    except Exception as e:
        logger.error(f"Error al mostrar planes: {str(e)}")
        return render_template('error.html', mensaje='Error al cargar los planes'), 500


@pagos_bp.route('/crear-orden', methods=['POST'])
@login_required
@csrf.exempt
def crear_orden():
    """
    Crea una orden de pago en Mercado Pago.

    Body JSON esperado:
    {
        "plan": "pro" | "premium"
    }

    Respuesta:
    {
        "success": true/false,
        "checkout_url": "https://...",  // URL para pagar
        "orden_id": "123456",
        "mensaje": "..."
    }
    """

    try:
        data = request.get_json()
        plan = data.get('plan', '').lower()

        # Validar que el plan existe
        if plan not in PLANES:
            return jsonify({
                'success': False,
                'mensaje': f'Plan inválido. Opciones: {", ".join(PLANES.keys())}'
            }), 400

        plan_info = PLANES[plan]

        # Generar URLs de retorno del servidor actual.
        # url_for con _external=True produce la URL completa (https://servidor.com/ruta).
        # Esto funciona en producción sin configurar variables de entorno adicionales.
        # Si el admin configura MERCADO_PAGO_SUCCESS_URL etc. en Render, esas toman prioridad
        # (el módulo mercado_pago.py las usa si no se pasan parámetros).
        success_url = os.getenv('MERCADO_PAGO_SUCCESS_URL') or url_for('pagos.pago_confirmado', _external=True)
        failure_url = os.getenv('MERCADO_PAGO_FAILURE_URL') or url_for('pagos.pago_fallido', _external=True)
        pending_url = os.getenv('MERCADO_PAGO_PENDING_URL') or url_for('pagos.pago_pendiente', _external=True)

        logger.info(f"Back URLs de MP → success: {success_url}")

        # Crear orden en Mercado Pago (en ARS)
        orden = crear_orden_pago(
            user_id=current_user.id,
            plan=plan,
            descripcion=plan_info['descripcion'],
            monto=plan_info['precio_ars'],
            moneda='ARS',
            email_usuario=current_user.email,
            success_url=success_url,
            failure_url=failure_url,
            pending_url=pending_url,
        )

        # Guardar referencia de la orden en BD (estado: pending)
        compra = CompraCreditos(
            user_id=current_user.id,
            stripe_payment_id=orden['id'],  # ID de orden de Mercado Pago
            stripe_session_id=orden.get('external_reference'),  # External reference
            creditos_comprados=plan_info['creditos'],
            monto_pagado=plan_info['precio_ars'],
            plan=plan,
            estado='pending'
        )
        db.session.add(compra)
        db.session.commit()

        logger.info(f"Orden creada: {orden['id']} para usuario {current_user.id}")

        return jsonify({
            'success': True,
            'checkout_url': orden.get('checkout_url'),
            'orden_id': orden['id'],
            'mensaje': f'Orden creada. Redirigiendo a Mercado Pago...'
        }), 200

    except MercadoPagoError as e:
        msg = str(e)
        logger.error(f"Error de Mercado Pago: {msg}")
        # Mensaje amigable para el usuario (sin exponer detalles técnicos internos)
        if 'ACCESS_TOKEN' in msg:
            mensaje_usuario = 'El servicio de pagos no está configurado. Contactá al administrador.'
        else:
            mensaje_usuario = 'No se pudo conectar con Mercado Pago. Intentá de nuevo en unos minutos.'
        return jsonify({
            'success': False,
            'mensaje': mensaje_usuario
        }), 500

    except Exception as e:
        logger.error(f"Error en crear_orden: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'mensaje': 'Error inesperado al crear la orden'
        }), 500


def _extraer_id_pago_webhook(body: dict) -> str:
    """
    Saca el ID del pago de la notificación, en cualquiera de los formatos que
    usa MP: Webhooks (`?data.id=...&type=payment` + body {"data": {"id": ...}})
    o IPN (`?topic=payment&id=...`). MP firma con el `data.id` de la URL, así
    que ése tiene prioridad.
    """
    data_id = (
        request.args.get('data.id')
        or (body.get('data') or {}).get('id')
    )
    if not data_id and (request.args.get('topic') or body.get('topic')) == 'payment':
        data_id = request.args.get('id') or body.get('resource') or body.get('id')
    data_id = str(data_id or '').strip()
    # IPN a veces manda la URL completa del recurso (".../v1/payments/123").
    if '/' in data_id:
        data_id = data_id.rstrip('/').rsplit('/', 1)[-1]
    return data_id


def _es_notificacion_de_pago(body: dict) -> bool:
    action = body.get('action') or request.args.get('action') or ''
    tipo = body.get('type') or request.args.get('type') or request.args.get('topic') or body.get('topic') or ''
    return action.startswith('payment.') or tipo == 'payment'


@pagos_bp.route('/webhook', methods=['POST'])
@csrf.exempt
@limiter.limit("60 per minute")
def webhook_mercado_pago():
    """
    Webhook de Mercado Pago.

    La notificación sólo trae el ID del pago; el estado, el monto y el
    external_reference se consultan SIEMPRE a la API de MP con nuestro
    access token. Por eso la acreditación nunca depende de lo que diga el
    cuerpo de la notificación, y una firma inválida (o un secreto mal cargado
    en Render) no puede hacer perder un pago real: se registra el aviso de
    seguridad y se sigue con la verificación contra la API.

    Respuesta: 200 OK (MP espera respuesta rápida).
    """
    try:
        body = request.get_json(silent=True) or {}
        data_id = _extraer_id_pago_webhook(body)

        firma_ok = validar_firma_webhook(
            request.headers.get('x-signature', ''),
            request.headers.get('x-request-id', ''),
            data_id,
        )
        if not firma_ok:
            logger.warning(
                f"[SECURITY] Webhook con firma inválida (data_id={data_id}). "
                "Se verifica igual contra la API de MP antes de acreditar nada."
            )

        logger.info(f"Webhook recibido: args={dict(request.args)}, data_id={data_id}")

        if _es_notificacion_de_pago(body) and data_id and data_id.isdigit():
            pago = obtener_pago(data_id)
            _acreditar_pago_si_corresponde(pago)

        return jsonify({'status': 'received'}), 200

    except MercadoPagoError as e:
        # Error consultando la API: devolvemos 500 para que MP reintente más
        # tarde (si respondiéramos 200 el pago quedaría sin acreditar).
        logger.error(f"Error MP en webhook: {str(e)}")
        return jsonify({'status': 'error'}), 500

    except Exception as e:
        logger.error(f"Error inesperado en webhook: {str(e)}", exc_info=True)
        return jsonify({'status': 'error'}), 500


def _acreditar_pago_si_corresponde(pago: dict):
    """
    Acredita la compra asociada a un pago de MP si (y sólo si) MP dice que
    está aprobado y el monto cubre el precio de la compra.

    `pago` debe venir de la API de MP (obtener_pago / buscar_pagos_por_referencia),
    nunca de parámetros que controle el usuario.

    Devuelve la CompraCreditos encontrada (acreditada ahora o antes), o None.
    """
    status_pago = pago.get('status')
    external_ref = pago.get('external_reference')
    logger.info(f"Pago {pago.get('id')}: status={status_pago}, ref={external_ref}")

    if not external_ref:
        return None

    compra = CompraCreditos.query.filter_by(stripe_session_id=external_ref).first()
    if not compra:
        logger.warning(f"Pago {pago.get('id')} aprobado con ref={external_ref} sin compra asociada en la BD")
        return None

    if status_pago != 'approved' or compra.estado != 'pending':
        return compra

    monto = pago.get('transaction_amount')
    try:
        monto_ok = monto is not None and float(monto) + 0.01 >= float(compra.monto_pagado)
    except (TypeError, ValueError):
        monto_ok = False
    if not monto_ok or (pago.get('currency_id') and pago.get('currency_id') != 'ARS'):
        logger.error(
            f"[PAGOS] Pago {pago.get('id')} para compra {compra.id} con monto/moneda inesperados "
            f"({monto} {pago.get('currency_id')}, esperado {compra.monto_pagado} ARS) — no se acredita"
        )
        return compra

    _confirmar_compra(compra)
    return compra


# Orden de "mayor" plan comprado, para gatear la actualización incremental
# (rutas/descargas.py): sólo estudio/matricula la habilitan. El índice en
# esta lista es el rango — no hace falta que sea el precio ni los créditos.
ORDEN_PLANES = ['individual', 'estudio', 'matricula']


def _actualizar_plan_max_comprado(usuario: User, plan_comprado: str) -> None:
    """
    Sube usuario.plan_max_comprado si `plan_comprado` es superior al que ya
    tenía registrado (o si todavía no tenía ninguno). Nunca lo baja: comprar
    un paquete Individual después de uno Estudio no le quita a alguien la
    actualización incremental que ya se ganó.
    """
    if plan_comprado not in ORDEN_PLANES:
        return
    rango_nuevo = ORDEN_PLANES.index(plan_comprado)
    rango_actual = ORDEN_PLANES.index(usuario.plan_max_comprado) if usuario.plan_max_comprado in ORDEN_PLANES else -1
    if rango_nuevo > rango_actual:
        usuario.plan_max_comprado = plan_comprado


def _confirmar_compra(compra: CompraCreditos) -> None:
    """
    Marca una compra como completada y acredita los créditos al usuario.
    Función auxiliar usada tanto por el webhook como por pago_confirmado.
    """
    from datetime import datetime

    # Reclamo atómico: sólo un proceso (webhook, redirect o reconciliación)
    # puede pasar la compra de 'pending' a 'completed'. Si dos llegan a la vez,
    # el segundo ve rowcount 0 y no vuelve a sumar créditos.
    ahora = datetime.utcnow()
    filas = CompraCreditos.query.filter_by(id=compra.id, estado='pending').update(
        {'estado': 'completed', 'completado_en': ahora}, synchronize_session=False
    )
    if filas != 1:
        db.session.rollback()
        logger.info(f"Compra {compra.id} ya estaba acreditada — no se suma de nuevo")
        return

    usuario = db.session.get(User, compra.user_id)
    if usuario:
        usuario.creditos_disponibles += compra.creditos_comprados
        _actualizar_plan_max_comprado(usuario, compra.plan)

        # Programa de referidos: si es su primera compra y vino recomendado, el
        # que lo recomendó gana créditos. Nunca debe impedir que el comprador
        # reciba los suyos, por eso va aparte y con su propio try/except.
        referidor = None
        try:
            referidor = premiar_referidor_si_corresponde(usuario)
        except Exception:
            logger.warning("Error al premiar al referidor", exc_info=True)
            referidor = None

        db.session.commit()
        logger.info(
            f"Créditos acreditados: {usuario.email} recibió "
            f"+{compra.creditos_comprados} créditos (total: {usuario.creditos_disponibles})"
        )
        if referidor:
            logger.info(f"Referido: user {referidor.id} ganó créditos por la compra de user {usuario.id}")
            enviar_email_premio_referido(referidor, usuario)
    else:
        db.session.commit()
    db.session.refresh(compra)


@pagos_bp.route('/pago-confirmado', methods=['GET'])
@login_required
def pago_confirmado():
    """
    Página que se muestra después de un pago exitoso.
    MP redirige aquí con los parámetros del pago en la URL.

    MP envía en la URL:
    - payment_id: ID del pago
    - status: 'approved', 'rejected', 'pending'
    - external_reference: nuestra referencia (user_1_plan_basico)
    - collection_status: igual que status
    """

    try:
        status = request.args.get('status') or request.args.get('collection_status')
        external_ref = request.args.get('external_reference')
        payment_id = request.args.get('payment_id') or request.args.get('collection_id')

        logger.info(f"Retorno de MP: status={status}, ref={external_ref}, payment_id={payment_id}")

        compra = None

        # Nunca se acredita por lo que diga la URL (cualquiera puede escribir
        # ?status=approved): se consulta el pago a la API de MP. Esto además
        # cubre el caso en que el webhook no llegó.
        try:
            pago = None
            if payment_id and str(payment_id).isdigit():
                pago = obtener_pago(str(payment_id))
            elif external_ref:
                resultados = buscar_pagos_por_referencia(external_ref)
                pago = next((p for p in resultados if p.get('status') == 'approved'), None)
            if pago:
                candidata = CompraCreditos.query.filter_by(
                    stripe_session_id=pago.get('external_reference')
                ).first()
                # Sólo el dueño de la compra puede dispararla desde su sesión.
                if candidata and candidata.user_id == current_user.id:
                    compra = _acreditar_pago_si_corresponde(pago)
                    if compra is not None:
                        db.session.refresh(compra)
                    status = pago.get('status') or status
        except MercadoPagoError as e:
            logger.error(f"No se pudo verificar el pago {payment_id} en el retorno: {e}")

        # Si no tenemos compra todavía, buscar la más reciente del usuario
        if not compra:
            compra = CompraCreditos.query.filter_by(
                user_id=current_user.id
            ).order_by(CompraCreditos.creado_en.desc()).first()

        return render_template('pago-confirmado.html',
            usuario=current_user,
            compra=compra,
            payment_id=payment_id,
            status=status
        ), 200

    except Exception as e:
        logger.error(f"Error en pago_confirmado: {str(e)}")
        return render_template('error.html', mensaje='Error al procesar confirmación'), 500


@pagos_bp.route('/pago-fallido', methods=['GET'])
@login_required
def pago_fallido():
    """
    Página que se muestra si el pago falla o es cancelado.
    """

    razon = request.args.get('reason', 'Pago cancelado o rechazado')

    return render_template('pago-fallido.html', razon=razon), 200


@pagos_bp.route('/pago-pendiente', methods=['GET'])
@login_required
def pago_pendiente():
    """
    Página que se muestra cuando el pago está pendiente
    (ej: transferencia bancaria, efectivo).
    """

    return render_template('pago-pendiente.html'), 200


@pagos_bp.route('/historial', methods=['GET'])
@login_required
def historial_compras():
    """
    Muestra el historial de compras del usuario.
    """

    try:
        compras = CompraCreditos.query.filter_by(user_id=current_user.id).order_by(
            CompraCreditos.creado_en.desc()
        ).all()

        return render_template('historial-compras.html', compras=compras), 200

    except Exception as e:
        logger.error(f"Error en historial_compras: {str(e)}")
        return render_template('error.html', mensaje='Error al cargar el historial'), 500
