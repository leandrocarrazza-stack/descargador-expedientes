"""Extensiones Flask compartidas para evitar circular imports."""

import os
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect
from flask_mail import Mail

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri='memory://',
)

csrf = CSRFProtect()
mail = Mail()
