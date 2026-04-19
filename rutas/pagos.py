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

from modulos.mercado_pago import crear_orden_pago, procesar_webhook, obtener_pago, validar_firma_webhook, MercadoPagoError
from modulos.database import db
from modulos.models import CompraCreditos, User
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


@pagos_bp.route('/webhook', methods=['POST'])
@csrf.exempt
@limiter.limit("60 per minute")
def webhook_mercado_pago():
    """
    Webhook de Mercado Pago.
    Recibe confirmación de pagos completados.

    Mercado Pago envía POST a esta URL cuando:
    - Un pago se completa
    - Un pago falla
    - Un pago está pendiente

    Respuesta: 200 OK (Mercado Pago espera respuesta rápida)
    """

    try:
        # Validar firma HMAC-SHA256 antes de procesar el contenido
        x_signature = request.headers.get('x-signature', '')
        x_request_id = request.headers.get('x-request-id', '')
        # data_id se necesita para reconstruir el string firmado
        _body = request.get_json(silent=True) or {}
        _data_id = _body.get('data', {}).get('id') or request.args.get('data.id') or ''

        if not validar_firma_webhook(x_signature, x_request_id, str(_data_id)):
            logger.warning("[SECURITY] Webhook rechazado por firma inválida.")
            return jsonify({'status': 'invalid_signature'}), 400

        # MP envía el webhook como JSON en el body
        data = _body
        # También puede venir como query param
        action = data.get('action') or request.args.get('action')
        # El ID que viene en el webhook es el ID del PAGO (no de la preferencia)
        data_id = _data_id

        logger.info(f"Webhook recibido: action={action}, data_id={data_id}")

        # Solo procesar eventos de pago
        if action in ('payment.created', 'payment.updated') and data_id:
            # 1. Consultar el pago a la API de MP para obtener status y external_reference
            #    (el webhook solo trae el ID, no el estado ni la referencia)
            pago = obtener_pago(str(data_id))
            status_pago = pago.get('status')           # 'approved', 'rejected', 'pending', etc.
            external_ref = pago.get('external_reference')  # 'user_1_plan_basico'

            logger.info(f"Pago {data_id}: status={status_pago}, ref={external_ref}")

            # 2. Solo acreditar si el pago fue aprobado
            if status_pago == 'approved' and external_ref:
                # Buscar la compra por external_reference (guardada en stripe_session_id)
                compra = CompraCreditos.query.filter_by(
                    stripe_session_id=external_ref,
                    estado='pending'
                ).first()

                if compra:
                    _confirmar_compra(compra)
                else:
                    logger.info(f"Compra con ref={external_ref} ya fue procesada o no existe")

        # MP requiere 200 OK rápido — siempre responder 200
        return jsonify({'status': 'received'}), 200

    except MercadoPagoError as e:
        logger.error(f"Error MP en webhook: {str(e)}")
        return jsonify({'status': 'error'}), 200  # Igual 200 para que MP no reintente

    except Exception as e:
        logger.error(f"Error inesperado en webhook: {str(e)}")
        return jsonify({'status': 'error'}), 200


def _confirmar_compra(compra: CompraCreditos) -> None:
    """
    Marca una compra como completada y acredita los créditos al usuario.
    Función auxiliar usada tanto por el webhook como por pago_confirmado.
    """
    from datetime import datetime

    compra.estado = 'completed'
    compra.completado_en = datetime.utcnow()
    db.session.commit()

    usuario = User.query.get(compra.user_id)
    if usuario:
        usuario.creditos_disponibles += compra.creditos_comprados
        db.session.commit()
        logger.info(
            f"Créditos acreditados: {usuario.email} recibió "
            f"+{compra.creditos_comprados} créditos (total: {usuario.creditos_disponibles})"
        )


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

        # Si el pago fue aprobado, intentar acreditar créditos
        # (el webhook puede llegar después que el redirect, así que lo hacemos acá también)
        if status == 'approved' and external_ref:
            compra = CompraCreditos.query.filter_by(
                stripe_session_id=external_ref,
                estado='pending'
            ).first()

            if compra:
                _confirmar_compra(compra)
                logger.info(f"Créditos acreditados via redirect (antes que el webhook)")
            else:
                # Ya fue procesado por el webhook, buscar la compra completada
                compra = CompraCreditos.query.filter_by(
                    stripe_session_id=external_ref
                ).first()

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
