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
# Checkout Pro (Preferences API) - estándar para webs con redirección al checkout de MP
MP_PREFERENCES_ENDPOINT = f'{MP_API_BASE}/checkout/preferences'


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

    # Payload para Checkout Pro (Preferences API)
    # Documentación: https://www.mercadopago.com.ar/developers/es/docs/checkout-pro
    payload = {
        "items": [
            {
                "title": f"{creditos} créditos - Descargador de Expedientes",
                "description": descripcion,
                "quantity": 1,
                "unit_price": float(monto),
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
        "auto_return": "approved",  # Redirige automáticamente solo si el pago fue aprobado
        "external_reference": f"user_{user_id}_plan_{plan}",
        "statement_descriptor": "Descargador Expedientes",
    }

    # Agregar notification_url solo si está configurada (en producción)
    notif_url = os.getenv('MERCADO_PAGO_NOTIFICATION_URL')
    if notif_url:
        payload["notification_url"] = notif_url

    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {MP_ACCESS_TOKEN}'
    }

    try:
        # Crear preferencia de pago en Mercado Pago
        response = requests.post(MP_PREFERENCES_ENDPOINT, json=payload, headers=headers)
        response.raise_for_status()

        data = response.json()

        # La respuesta de Preferences tiene init_point (producción) y sandbox_init_point (testing)
        # init_point es la URL a la que redirigir al usuario para pagar
        checkout_url = data.get('init_point') or data.get('sandbox_init_point')

        orden = {
            'id': data.get('id'),
            'external_reference': data.get('external_reference'),
            'status': 'pending',
            'total_amount': monto,
            'checkout_url': checkout_url,
        }

        logger.info(f"Preferencia creada exitosamente: {orden['id']} para usuario {user_id}")
        return orden

    except requests.exceptions.RequestException as e:
        error_msg = f"Error al crear orden en Mercado Pago: {str(e)}"
        logger.error(error_msg)
        raise MercadoPagoError(error_msg)


def obtener_pago(payment_id: str) -> Dict[str, Any]:
    """
    Obtiene los detalles de un PAGO específico por su ID.

    Importante: en MP, una "preferencia" (preference) y un "pago" (payment)
    son objetos distintos con IDs distintos.
    - La preferencia se crea cuando el usuario hace click en "Comprar"
    - El pago se crea cuando el usuario efectivamente paga
    El webhook envía el ID del PAGO, no de la preferencia.

    Args:
        payment_id: ID del pago (lo que envía el webhook en data.id)

    Returns:
        Dict con datos del pago, incluyendo 'status' y 'external_reference'

    Raises:
        MercadoPagoError: Si falla la consulta
    """
    url = f'{MP_API_BASE}/v1/payments/{payment_id}'
    headers = {
        'Authorization': f'Bearer {MP_ACCESS_TOKEN}'
    }

    try:
        response = requests.get(url, headers=headers)
        response.raise_for_status()
        return response.json()
    except requests.exceptions.RequestException as e:
        error_msg = f"Error al obtener pago {payment_id}: {str(e)}"
        logger.error(error_msg)
        raise MercadoPagoError(error_msg)


def obtener_orden(orden_id: str) -> Dict[str, Any]:
    """
    Obtiene los detalles de una preferencia existente.

    Args:
        orden_id: ID de la preferencia en Mercado Pago

    Returns:
        Dict con datos de la preferencia

    Raises:
        MercadoPagoError: Si falla la consulta
    """

    url = f'{MP_PREFERENCES_ENDPOINT}/{orden_id}'
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
