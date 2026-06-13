"""
Tests de autorización — red de seguridad para regresiones.

Si alguno de estos tests falla después de un cambio de código, significa
que se introdujo una vulnerabilidad de acceso no autorizado.
"""
import pytest
from tests.conftest import login_como


# ── Rutas que exigen login ─────────────────────────────────────────────────

@pytest.mark.parametrize("method,path", [
    ('GET', '/descargas/expediente'),
    ('GET', '/descargas/historial'),
    ('GET', '/auth/mv-estado'),
    ('GET', '/auth/mv-login'),
    ('GET', '/pagos/historial'),
    ('GET', '/admin/'),
])
def test_rutas_requieren_login(client, method, path):
    """Un usuario anónimo recibe redirect (302) o 401/403 en rutas protegidas."""
    resp = client.open(path, method=method)
    assert resp.status_code in (302, 401, 403), (
        f"{method} {path} debería exigir login, devolvió {resp.status_code}"
    )


# ── Panel de administración ────────────────────────────────────────────────

def test_usuario_normal_bloqueado_en_admin(client):
    """Un usuario autenticado sin is_admin recibe 403 en /admin/."""
    login_como(client, 'normal')
    resp = client.get('/admin/')
    assert resp.status_code == 403


def test_admin_puede_ver_panel(client):
    """Un admin puede acceder al panel."""
    login_como(client, 'admin')
    resp = client.get('/admin/')
    assert resp.status_code == 200


def test_otorgar_creditos_requiere_admin(client):
    """Un usuario normal no puede otorgar créditos."""
    login_como(client, 'normal')
    resp = client.post('/admin/otorgar-creditos',
                       json={'email': 'otro@test.com', 'creditos': 999})
    assert resp.status_code == 403


# ── Descarga requiere sesión MV ────────────────────────────────────────────

def test_descarga_sin_sesion_mv_redirige(client):
    """Un usuario sin sesión de Mesa Virtual es redirigido (no ve el form de descarga)."""
    login_como(client, 'normal')
    resp = client.get('/descargas/expediente')
    assert resp.status_code == 302


# ── Descarga POST verifica créditos ────────────────────────────────────────

def test_descarga_sin_creditos_devuelve_402(client):
    """Un usuario sin créditos disponibles recibe HTTP 402."""
    login_como(client, 'broke')
    resp = client.post('/descargas/expediente',
                       json={'numero_expediente': '1/24'})
    assert resp.status_code == 402
    data = resp.get_json()
    assert data['tipo_error'] == 'creditos_insuficientes'
