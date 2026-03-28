"""
Modelos de Base de Datos con SQLAlchemy
========================================

Define la estructura de las tablas:
- User: Información de usuarios
- ExpedienteDescargado: Historial de descargas
- Creditos: Sistema de créditos prepagados

Uso:
    from modulos.models import User, ExpedienteDescargado
    from modulos.database import db

    # Crear usuario
    usuario = User(email='test@example.com', nombre='Juan')
    usuario.establecer_password('contraseña123')
    db.session.add(usuario)
    db.session.commit()

    # Consultar
    usuario = User.query.filter_by(email='test@example.com').first()
"""

from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from modulos.database import db


class User(db.Model):
    """Modelo de usuario de la aplicación."""

    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    nombre = db.Column(db.String(255), nullable=True)

    # Plan: free, pro, premium (ver config.py)
    plan = db.Column(db.String(50), default='free', nullable=False)

    # Admin: puede descargar sin gastar créditos y otorgar créditos a otros
    is_admin = db.Column(db.Boolean, default=False, nullable=False)

    # Créditos disponibles (modelo prepagado)
    creditos_disponibles = db.Column(db.Integer, default=0, nullable=False)

    # Créditos usados este mes
    creditos_usados_mes = db.Column(db.Integer, default=0, nullable=False)

    # Fecha de reset de créditos mensuales
    fecha_reset_creditos = db.Column(db.DateTime, default=datetime.utcnow)

    # Timestamps
    creado_en = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    actualizado_en = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relaciones
    expedientes = db.relationship('ExpedienteDescargado', backref='usuario', lazy=True, cascade='all, delete-orphan')
    compras = db.relationship('CompraCreditos', backref='usuario', lazy=True, cascade='all, delete-orphan')

    def __repr__(self):
        return f'<User {self.email}>'

    def establecer_password(self, password):
        """Hashea y guarda la contraseña."""
        self.password_hash = generate_password_hash(password, method='pbkdf2:sha256')

    def verificar_password(self, password):
        """Verifica que la contraseña sea correcta."""
        return check_password_hash(self.password_hash, password)

    def tiene_creditos(self, cantidad=1):
        """Verifica si el usuario tiene suficientes créditos."""
        return self.creditos_disponibles >= cantidad

    def usar_creditos(self, cantidad=1):
        """Usa créditos (si tiene suficientes)."""
        if self.tiene_creditos(cantidad):
            self.creditos_disponibles -= cantidad
            self.creditos_usados_mes += cantidad
            return True
        return False

    def obtener_info(self):
        """Retorna dict con info del usuario (para JSON)."""
        return {
            'id': self.id,
            'email': self.email,
            'nombre': self.nombre,
            'plan': self.plan,
            'creditos_disponibles': self.creditos_disponibles,
            'creditos_usados_mes': self.creditos_usados_mes,
            'creado_en': self.creado_en.isoformat()
        }


class ExpedienteDescargado(db.Model):
    """Modelo de expediente descargado (historial de usuario)."""

    __tablename__ = 'expedientes_descargados'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

    # Información del expediente
    numero = db.Column(db.String(50), nullable=False)
    caratula = db.Column(db.String(500), nullable=True)
    tribunal = db.Column(db.String(255), nullable=True)

    # Rutas del archivo (descarga directa al PC, no almacenar en servidor)
    pdf_ruta_temporal = db.Column(db.String(500), nullable=True)  # Solo para tracking

    # Estado de la descarga
    estado = db.Column(db.String(50), default='pending', nullable=False)  # pending, processing, completed, failed
    porcentaje = db.Column(db.Integer, default=0)  # 0-100%

    # Si falló
    error_msg = db.Column(db.Text, nullable=True)

    # Timestamps
    creado_en = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    completado_en = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f'<ExpedienteDescargado {self.numero} - {self.estado}>'

    def obtener_info(self):
        """Retorna dict con info del expediente (para JSON)."""
        return {
            'id': self.id,
            'numero': self.numero,
            'caratula': self.caratula,
            'tribunal': self.tribunal,
            'estado': self.estado,
            'porcentaje': self.porcentaje,
            'error': self.error_msg,
            'creado_en': self.creado_en.isoformat(),
            'completado_en': self.completado_en.isoformat() if self.completado_en else None
        }


class CompraCreditos(db.Model):
    """Modelo de compra de créditos (historial de pago con Stripe)."""

    __tablename__ = 'compras_creditos'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)

    # Información de Stripe
    stripe_payment_id = db.Column(db.String(255), unique=True, nullable=False, index=True)
    stripe_session_id = db.Column(db.String(255), unique=True, nullable=True)

    # Detalles de la compra
    creditos_comprados = db.Column(db.Integer, nullable=False)
    monto_pagado = db.Column(db.Float, nullable=False)  # En USD/ARS
    plan = db.Column(db.String(50), nullable=False)  # free, pro, premium

    # Estado
    estado = db.Column(db.String(50), default='pending', nullable=False)  # pending, completed, failed

    # Timestamps
    creado_en = db.Column(db.DateTime, default=datetime.utcnow, nullable=False, index=True)
    completado_en = db.Column(db.DateTime, nullable=True)

    def __repr__(self):
        return f'<CompraCreditos {self.stripe_payment_id} - {self.estado}>'

    def obtener_info(self):
        """Retorna dict con info de la compra (para JSON)."""
        return {
            'id': self.id,
            'creditos_comprados': self.creditos_comprados,
            'monto_pagado': self.monto_pagado,
            'plan': self.plan,
            'estado': self.estado,
            'creado_en': self.creado_en.isoformat(),
            'completado_en': self.completado_en.isoformat() if self.completado_en else None
        }
