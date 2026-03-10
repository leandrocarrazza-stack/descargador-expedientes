# modulos/mercado_pago.py
"""
Módulo para integración con Mercado Pago (API de Orders).
Maneja creación de órdenes, procesamiento de webhooks y actualización de créditos.
"""

import os
import logging
from typing import Dict, Any
from dotenv import load_dotenv
import requests

# Cargar variables de entorno
load_dotenv()

logger = logging.getLogger(__name__)

# Configuración de Mercado Pago
MP_ACCESS_TOKEN = os.getenv('MERCADO_PAGO_ACCESS_TOKEN')
MP_PUBLIC_KEY = os.getenv('MERCADO_PAGO_PUBLIC_KEY')
MP_SUCCESS_URL = os.getenv('MERCADO_PAGO_SUCCESS_URL', 'http://localhost:5000/pago-confirmado')
MP_FAILURE_URL = os.getenv('MERCADO_PAGO_FAILURE_URL', 'http://localhost:5000/pago-fallido')
MP_PENDING_URL = os.getenv('MERCADO_PAGO_PENDING_URL', 'http://localhost:5000/pago-pendiente')

# URLs de API de Mercado Pago
MP_API_BASE = 'https://api.mercadopago.com'
MP_ORDERS_ENDPOINT = f'{MP_API_BASE}/v2/orders'


class MercadoPagoError(Exception):
    """Excepción personalizada para errores de Mercado Pago"""
    pass


def crear_orden_pago(
    user_id: int,
    plan: str,
    descripcion: str,
    monto: float,
    email_usuario: str,
    moneda: str = 'ARS'
) -> Dict[str, Any]:
    """
    Crea una orden de pago en Mercado Pago.

    Args:
        user_id: ID del usuario en la BD
        plan: Nombre del plan (basico, ahorro, profesional)
        descripcion: Descripción del producto
        monto: Monto a pagar
        email_usuario: Email del usuario
        moneda: Moneda de pago (ARS o USD) - por defecto ARS

    Returns:
        Dict con datos de la orden (id, link de pago, etc.)

    Raises:
        MercadoPagoError: Si falla la creación de la orden
    """

    # Mapeo de créditos por plan
    CREDITOS_POR_PLAN = {
        'basico': 1,
        'ahorro': 5,
        'profesional': 20
    }

    creditos = CREDITOS_POR_PLAN.get(plan, 0)

    # Cuerpo de la solicitud según API de Orders
    payload = {
        "title": f"Compra de {creditos} créditos - {descripcion}",
        "description": descripcion,
        "external_reference": f"user_{user_id}_plan_{plan}",
        "total_amount": monto,
        "currency": moneda,
        "items": [
            {
                "title": f"{creditos} créditos para descargar expedientes",
                "description": descripcion,
                "quantity": 1,
                "unit_price": monto,
                "currency_id": moneda
            }
        ],
        "payer": {
            "email": email_usuario
        },
        "back_urls": {
            "success": MP_SUCCESS_URL,
            "failure": MP_FAILURE_URL,
            "pending": MP_PENDING_URL
        },
        "statement_descriptor": "Descargador Expedientes",
        "notification_url": "https://tu-servidor.com/webhook-mercado-pago",  # Se actualiza en producción
        "expiration_date": None  # Sin vencimiento
    }

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {MP_ACCESS_TOKEN}'
    }

    try:
        # Crear orden en Mercado Pago
        response = requests.post(MP_ORDERS_ENDPOINT, json=payload, headers=headers)
        response.raise_for_status()

        data = response.json()

        # Extraer datos importantes
        orden = {
            'id': data.get('id'),
            'external_reference': data.get('external_reference'),
            'status': data.get('status'),
            'total_amount': data.get('total_amount'),
            'items': data.get('items', []),
            'payer_email': data.get('payer', {}).get('email'),
            # Link de pago (varía según la respuesta)
            'checkout_url': None
        }

        # Buscar link de pago en las opciones de transacción
        if 'processing_modes' in data:
            for mode in data.get('processing_modes', []):
                if mode.get('type') == 'online':
                    orden['checkout_url'] = mode.get('url')

        logger.info(f"Orden creada exitosamente: {orden['id']} para usuario {user_id}")
        return orden

    except requests.exceptions.RequestException as e:
        error_msg = f"Error al crear orden en Mercado Pago: {str(e)}"
        logger.error(error_msg)
        raise MercadoPagoError(error_msg)


def obtener_orden(orden_id: str) -> Dict[str, Any]:
    """
    Obtiene los detalles de una orden existente.

    Args:
        orden_id: ID de la orden en Mercado Pago

    Returns:
        Dict con datos de la orden

    Raises:
        MercadoPagoError: Si falla la consulta
    """

    url = f'{MP_ORDERS_ENDPOINT}/{orden_id}'
    headers = {
        'Authorization': f'Bearer {MP_ACCESS_TOKEN}'
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        error_msg = f"Error al obtener orden {orden_id}: {str(e)}"
        logger.error(error_msg)
        raise MercadoPagoError(error_msg)


def procesar_webhook(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Procesa un webhook de Mercado Pago.
    Extrae la información del pago confirmado.

    Args:
        data: Datos del webhook (JSON del request)

    Returns:
        Dict con detalles extraídos del webhook

    Raises:
        MercadoPagoError: Si los datos están incompletos
    """

    try:
        # La estructura del webhook varía según el evento
        # Para orders, buscamos la información de la transacción

        resultado = {
            'orden_id': data.get('id'),
            'external_reference': data.get('external_reference'),
            'status': data.get('status'),
            'monto': data.get('total_amount'),
            'email': data.get('payer', {}).get('email'),
            'timestamp': data.get('date_created')
        }

        # Extraer datos del usuario desde external_reference (user_123_plan_pro)
        ext_ref = resultado['external_reference']
        if ext_ref and '_plan_' in ext_ref:
            partes = ext_ref.split('_')
            resultado['user_id'] = int(partes[1])
            resultado['plan'] = partes[3] if len(partes) > 3 else None

        logger.info(f"Webhook procesado: orden {resultado['orden_id']}, estado {resultado['status']}")
        return resultado

    except (KeyError, IndexError, ValueError) as e:
        error_msg = f"Error al procesar webhook: {str(e)}"
        logger.error(error_msg)
        raise MercadoPagoError(error_msg)


def validar_firma_webhook(signature: str, request_id: str, timestamp: str) -> bool:
    """
    Valida la firma del webhook (opcional pero recomendado en producción).

    Args:
        signature: Firma enviada por Mercado Pago
        request_id: ID del request
        timestamp: Timestamp del request

    Returns:
        True si la firma es válida, False en caso contrario
    """
    # En producción, implementar validación de firma HMAC-SHA256
    # Por ahora, permitimos todos los webhooks (no recomendado en prod)
    logger.warning("Validación de firma de webhook deshabilitada. Activar en producción.")
    return True
