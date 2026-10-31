"""
Baja de cuenta
==============

Borra los datos de un usuario a su pedido (derecho de supresión, art. 16 de la
Ley 25.326; ver el artículo 25.6 de los Términos). La usa
`scripts/eliminar_cuenta.py`.

Qué hace `dar_de_baja`:
- Borra los PDF del usuario: del storage persistente (R2 o local, también los
  que quedaron huérfanos bajo `usuarios/<id>/`) y del caché local de `output/`.
- Borra su historial de expedientes, la sesión de Mesa Virtual, los tokens de
  recuperación de contraseña y los mensajes de contacto.
- Si la cuenta tiene compras, las conserva (obligaciones contables e impositivas,
  art. 16 inc. 5 de la Ley 25.326) y ANONIMIZA la cuenta: la fila de `users` queda
  solo como ancla de esas compras, sin correo, nombre ni IP, y sin forma de
  iniciar sesión. Con `borrar_compras=True`, o si no hay compras, borra la
  cuenta entera.

El storage se borra ANTES que la base: si fallara la base, solo quedaría un
historial sin archivo; al revés, quedarían archivos sin ningún registro que
permita encontrarlos.

No toca los registros técnicos (logs), las copias de seguridad del proveedor
ni los datos que tengan Mercado Pago o el proveedor de correo.
"""

import logging
import secrets
import shutil
from pathlib import Path

from sqlalchemy import func
from werkzeug.security import generate_password_hash

import config
from modulos.database import db
from modulos.models import (
    CompraCreditos, ExpedienteDescargado, MensajeContacto,
    SesionUsuarioMV, TokenResetPassword, User,
)
from modulos.storage import StorageLocal, StorageR2, storage_pdf

logger = logging.getLogger(__name__)


class BajaNoPermitida(Exception):
    """La cuenta no se puede dar de baja con este procedimiento."""


def _prefijo(user_id):
    return f"usuarios/{user_id}/"


def _keys_en_storage(storage, user_id):
    """Todas las keys bajo usuarios/<id>/ (incluye archivos sin fila en la base)."""
    prefijo = _prefijo(user_id)
    if isinstance(storage, StorageLocal):
        base = storage.base_dir / prefijo
        if not base.exists():
            return []
        return [f"{prefijo}{p.relative_to(base).as_posix()}" for p in base.rglob('*') if p.is_file()]
    if isinstance(storage, StorageR2):
        keys = []
        paginas = storage._cliente.get_paginator('list_objects_v2').paginate(
            Bucket=storage.bucket, Prefix=prefijo)
        for pagina in paginas:
            keys.extend(o['Key'] for o in pagina.get('Contents', []))
        return keys
    return []


def _archivos_locales(expedientes, output_dir):
    """PDF en el caché local de output/ de esos expedientes (solo dentro de output_dir)."""
    output_dir = Path(output_dir).resolve()
    candidatos = []
    for exp in expedientes:
        if exp.pdf_ruta_temporal:
            candidatos.append(Path(exp.pdf_ruta_temporal))
        candidatos.append(output_dir / f"recuperado_{exp.id}.pdf")
    existentes = []
    for ruta in candidatos:
        try:
            r = ruta.resolve()
        except OSError:
            continue
        if r.is_file() and output_dir in r.parents:
            existentes.append(r)
    return sorted(set(existentes))


def _mensajes(user):
    return MensajeContacto.query.filter(
        (MensajeContacto.user_id == user.id) |
        (func.lower(MensajeContacto.email) == (user.email or '').lower())
    )


def resumen_baja(user, storage=None, output_dir=None):
    """Qué se borraría para este usuario, sin tocar nada."""
    storage = storage or storage_pdf()
    output_dir = output_dir or config.OUTPUT_DIR
    expedientes = ExpedienteDescargado.query.filter_by(user_id=user.id).all()
    compras = CompraCreditos.query.filter_by(user_id=user.id).count()
    return {
        'user_id': user.id,
        'es_admin': bool(user.is_admin),
        'expedientes': len(expedientes),
        'keys_storage': _keys_en_storage(storage, user.id),
        'archivos_locales': _archivos_locales(expedientes, output_dir),
        'sesion_mv': SesionUsuarioMV.query.filter_by(user_id=user.id).count(),
        'tokens_reset': TokenResetPassword.query.filter_by(user_id=user.id).count(),
        'mensajes_contacto': _mensajes(user).count(),
        'compras': compras,
    }


def dar_de_baja(user, borrar_compras=False, storage=None, output_dir=None):
    """
    Da de baja la cuenta (ver el docstring del módulo). Devuelve un dict con lo
    que se hizo: 'resumen', 'resultado' ('anonimizada' o 'eliminada') y
    'keys_restantes' (keys que siguen existiendo en el storage tras el borrado).
    """
    if user.is_admin:
        raise BajaNoPermitida("La cuenta es administradora: quitale el rol antes de darla de baja.")

    storage = storage or storage_pdf()
    output_dir = output_dir or config.OUTPUT_DIR
    user_id, email = user.id, user.email
    resumen = resumen_baja(user, storage, output_dir)

    # 1) Storage y caché local (antes que la base: ver el docstring del módulo)
    for key in resumen['keys_storage']:
        storage.borrar(key)
    for ruta in resumen['archivos_locales']:
        ruta.unlink(missing_ok=True)
    restantes = [k for k in resumen['keys_storage'] if storage.existe(k)]
    if isinstance(storage, StorageLocal):
        # Al final: StorageLocal.existe() recrea la carpeta padre como efecto secundario
        shutil.rmtree(storage.base_dir / _prefijo(user_id), ignore_errors=True)

    # 2) Base de datos
    ExpedienteDescargado.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    SesionUsuarioMV.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    TokenResetPassword.query.filter_by(user_id=user_id).delete(synchronize_session=False)
    _mensajes(user).delete(synchronize_session=False)
    db.session.expire_all()

    if resumen['compras'] and not borrar_compras:
        resultado = 'anonimizada'
        user = db.session.get(User, user_id)
        user.email = f"baja-{user_id}@foja.invalid"
        user.nombre = None
        # Hash de una clave aleatoria que nadie conoce: la cuenta no puede iniciar sesión
        user.password_hash = generate_password_hash(secrets.token_urlsafe(32), method='pbkdf2:sha256')
        user.plan = 'free'
        user.plan_max_comprado = None
        user.creditos_disponibles = 0
        user.notificar_email = False
        user.recibir_promociones = False
        user.terminos_aceptados_ip = None
    else:
        resultado = 'eliminada'
        db.session.delete(db.session.get(User, user_id))
    db.session.commit()

    logger.info(f"[BAJA] Cuenta {user_id} {resultado} (compras: {resumen['compras']}, "
                f"expedientes: {resumen['expedientes']}, PDF: {len(resumen['keys_storage'])})")
    return {'resumen': resumen, 'resultado': resultado, 'keys_restantes': restantes, 'email': email}
