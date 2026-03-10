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

from modulos.mercado_pago import crear_orden_pago, procesar_webhook, MercadoPagoError
from modulos.database import db
from modulos.models import CompraCreditos, User
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

        # Crear orden en Mercado Pago (en ARS)
        orden = crear_orden_pago(
            user_id=current_user.id,
            plan=plan,
            descripcion=plan_info['descripcion'],
            monto=plan_info['precio_ars'],
            moneda='ARS',
            email_usuario=current_user.email
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
        logger.error(f"Error de Mercado Pago: {str(e)}")
        return jsonify({
            'success': False,
            'mensaje': f'Error al procesar el pago: {str(e)}'
        }), 500

    except Exception as e:
        logger.error(f"Error en crear_orden: {str(e)}")
        return jsonify({
            'success': False,
            'mensaje': 'Error inesperado al crear la orden'
        }), 500


@pagos_bp.route('/webhook', methods=['POST'])
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
        # Mercado Pago envía el webhook de dos formas:
        # 1. application/x-www-form-urlencoded (parámetros query)
        # 2. application/json (en FASE 2 avanzada)

        # Por ahora, manejamos la forma más simple: query parameters
        action = request.args.get('action')
        data_id = request.args.get('data.id')

        if not action or not data_id:
            # Si viene JSON en el body
            data = request.get_json() or {}
            action = data.get('action')
            data_id = data.get('data', {}).get('id')

        logger.info(f"Webhook recibido: action={action}, data_id={data_id}")

        # Solo procesar pagos completados
        if action == 'payment.created' or action == 'payment.updated':
            # En Mercado Pago, el status se encuentra en los datos
            # Este es un flujo simplificado

            # Buscar la compra pendiente por orden ID
            compra = CompraCreditos.query.filter_by(
                stripe_payment_id=str(data_id),
                estado='pending'
            ).first()

            if compra:
                # Actualizar estado a confirmado
                compra.estado = 'completed'
                db.session.commit()

                # Actualizar créditos del usuario
                usuario = User.query.get(compra.user_id)
                if usuario:
                    usuario.creditos_disponibles += compra.creditos_comprados
                    db.session.commit()

                    logger.info(
                        f"Pago confirmado: Usuario {usuario.email} recibió "
                        f"+{compra.creditos_comprados} créditos"
                    )

        # Responder rápidamente a Mercado Pago (200 OK)
        return jsonify({'status': 'received'}), 200

    except Exception as e:
        logger.error(f"Error en webhook_mercado_pago: {str(e)}")
        # Seguir devolviendo 200 para que Mercado Pago no reintente
        return jsonify({'status': 'error', 'message': str(e)}), 200


@pagos_bp.route('/pago-confirmado', methods=['GET'])
@login_required
def pago_confirmado():
    """
    Página que se muestra después de un pago exitoso.
    El usuario es redirigido aquí por Mercado Pago.
    """

    try:
        # Obtener parámetros que Mercado Pago devuelve en la URL
        collection_id = request.args.get('collection_id')
        collection_status = request.args.get('collection_status')
        payment_id = request.args.get('payment_id')
        status = request.args.get('status')

        # Buscar la última compra del usuario
        compra = CompraCreditos.query.filter_by(user_id=current_user.id).order_by(
            CompraCreditos.fecha_creacion.desc()
        ).first()

        contexto = {
            'usuario': current_user,
            'compra': compra,
            'collection_id': collection_id,
            'payment_id': payment_id,
            'status': status
        }

        return render_template('pago-confirmado.html', **contexto), 200

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
            CompraCreditos.fecha_creacion.desc()
        ).all()

        return render_template('historial-compras.html', compras=compras), 200

    except Exception as e:
        logger.error(f"Error en historial_compras: {str(e)}")
        return render_template('error.html', mensaje='Error al cargar el historial'), 500
