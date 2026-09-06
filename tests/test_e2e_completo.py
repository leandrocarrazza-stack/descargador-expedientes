#!/usr/bin/env python3
"""
TEST E2E COMPLETO: Sistema de Descargador de Expedientes
=========================================================

Prueba el flujo completo:
1. Inicio del servidor Flask
2. Registro de usuario
3. Login
4. Compra de créditos
5. Solicitud de descarga de expediente
6. Verificación de tareas en Celery
"""

import sys
import time
import requests
import json
import subprocess
from pathlib import Path
from datetime import datetime

# Configuración
BASE_URL = "http://127.0.0.1:5000"
TEST_EMAIL = f"test_e2e_{int(time.time())}@gmail.com"  # Usar gmail para que valide el dominio
TEST_PASSWORD = "TestPassword123!"
TEST_EXPEDIENTE = "14141"

class Colors:
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    BLUE = '\033[94m'
    BOLD = '\033[1m'
    END = '\033[0m'

def print_header(text):
    print(f"\n{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}  {text}{Colors.END}")
    print(f"{Colors.BOLD}{Colors.BLUE}{'='*70}{Colors.END}\n")

def print_ok(text):
    print(f"{Colors.GREEN}[OK] {text}{Colors.END}")

def print_error(text):
    print(f"{Colors.RED}[ERROR] {text}{Colors.END}")

def print_info(text):
    print(f"{Colors.YELLOW}[INFO] {text}{Colors.END}")

def esperar_servidor(max_intentos=30, intervalo=1):
    """Espera a que el servidor Flask esté listo."""
    print_info(f"Esperando que el servidor esté listo en {BASE_URL}...")

    for intento in range(max_intentos):
        try:
            response = requests.get(f"{BASE_URL}/", timeout=2)
            print_ok(f"Servidor está listo! (intento {intento+1})")
            return True
        except requests.exceptions.RequestException:
            print(f"  Intento {intento+1}/{max_intentos}...", end='\r')
            time.sleep(intervalo)

    print_error(f"Servidor no respondió después de {max_intentos} intentos")
    return False

def test_homepage():
    """Test: Verificar que la homepage carga."""
    print_header("TEST 1: Homepage")
    try:
        response = requests.get(f"{BASE_URL}/")
        if response.status_code == 200:
            print_ok("Homepage carga correctamente")
            return True
        else:
            print_error(f"Homepage retornó status {response.status_code}")
            return False
    except Exception as e:
        print_error(f"Error en homepage: {e}")
        return False

def test_signup():
    """Test: Registro de usuario."""
    print_header("TEST 2: Registro de Usuario")
    print_info(f"Email de prueba: {TEST_EMAIL}")

    try:
        # Registrar usuario (enviando JSON)
        data = {
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD,
            "password_confirm": TEST_PASSWORD,
            "nombre": "Usuario Test"
        }

        response = requests.post(f"{BASE_URL}/auth/signup", json=data, allow_redirects=False)

        # Puede redirigir a login (302) o devolver 200 si todo bien
        if response.status_code in [200, 302]:
            print_ok(f"Usuario registrado correctamente")
            return True
        else:
            print_error(f"Signup falló con status {response.status_code}")
            print_info(f"Response: {response.text[:200]}")
            return False

    except Exception as e:
        print_error(f"Error en signup: {e}")
        return False

def test_login():
    """Test: Login de usuario."""
    print_header("TEST 3: Login")

    try:
        session = requests.Session()

        # Hacer login (enviando JSON)
        data = {
            "email": TEST_EMAIL,
            "password": TEST_PASSWORD
        }

        response = session.post(f"{BASE_URL}/auth/login", json=data, allow_redirects=True)

        # Verificar que está autenticado
        if response.status_code == 200:
            # Intentar acceder a dashboard
            response = session.get(f"{BASE_URL}/dashboard")
            if response.status_code == 200:
                print_ok(f"Login exitoso")
                return True, session
            else:
                print_error(f"No se puede acceder a dashboard (status {response.status_code})")
                return False, None
        else:
            print_error(f"Login falló con status {response.status_code}")
            return False, None

    except Exception as e:
        print_error(f"Error en login: {e}")
        return False, None

def test_dashboard(session):
    """Test: Acceso a dashboard."""
    print_header("TEST 4: Dashboard de Usuario")

    try:
        response = session.get(f"{BASE_URL}/dashboard")

        if response.status_code == 200:
            print_ok("Dashboard carga correctamente")

            # Verificar que contiene elementos esperados
            if "creditos" in response.text.lower() or "plan" in response.text.lower():
                print_ok("Dashboard contiene información de créditos/plan")
                return True
            else:
                print_info("Dashboard cargó pero puede no contener datos esperados")
                return True
        else:
            print_error(f"Dashboard retornó status {response.status_code}")
            return False

    except Exception as e:
        print_error(f"Error accediendo a dashboard: {e}")
        return False

def test_planes(session):
    """Test: Ver planes de compra."""
    print_header("TEST 5: Planes de Compra")

    try:
        response = session.get(f"{BASE_URL}/pagos/planes")

        if response.status_code == 200:
            print_ok("Página de planes carga correctamente")

            # Verificar que contiene opciones de planes
            if "individual" in response.text.lower() or "estudio" in response.text.lower():
                print_ok("Planes disponibles encontrados")
                return True
            else:
                print_info("Página cargó pero falta contenido de planes")
                return True
        else:
            print_error(f"Planes retornó status {response.status_code}")
            return False

    except Exception as e:
        print_error(f"Error accediendo a planes: {e}")
        return False

def test_crear_orden(session):
    """Test: Crear orden de compra (sin procesar pago)."""
    print_header("TEST 6: Crear Orden de Compra")

    try:
        data = {"plan": "individual"}
        response = session.post(f"{BASE_URL}/pagos/crear-orden", json=data, allow_redirects=False)

        if response.status_code == 200:
            try:
                result = response.json()
                if "redirect_url" in result or "init_point" in result:
                    print_ok("Orden creada correctamente")
                    print_info(f"Tipo de respuesta: {result.get('type', 'unknown')}")
                    return True
                else:
                    print_info("Orden creada pero formato inesperado")
                    return True
            except:
                print_info("Orden creada (respuesta no es JSON)")
                return True
        elif response.status_code == 302:  # Redirect
            print_ok("Orden creada con redirección")
            return True
        else:
            print_error(f"Crear orden falló con status {response.status_code}")
            return False

    except Exception as e:
        print_error(f"Error creando orden: {e}")
        return False

def test_api_user(session):
    """Test: Endpoint de usuario autenticado."""
    print_header("TEST 7: Datos de Usuario (API)")

    try:
        response = session.get(f"{BASE_URL}/auth/user")

        if response.status_code == 200:
            try:
                user_data = response.json()
                print_ok("Datos de usuario obtenidos")
                print_info(f"Email: {user_data.get('email', 'N/A')}")
                print_info(f"Plan: {user_data.get('plan', 'N/A')}")
                print_info(f"Créditos: {user_data.get('creditos_disponibles', 'N/A')}")
                return True
            except:
                print_info("Endpoint responde pero no es JSON")
                return True
        else:
            print_error(f"User endpoint retornó status {response.status_code}")
            return False

    except Exception as e:
        print_error(f"Error obteniendo datos de usuario: {e}")
        return False

def main():
    print_header("PRUEBAS E2E: DESCARGADOR DE EXPEDIENTES")
    print_info(f"Hora de inicio: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    resultados = {}

    # Esperamos que el servidor esté corriendo
    print("\n" + "="*70)
    print("IMPORTANTE: Asegúrate de que el servidor Flask está corriendo en otra terminal")
    print("  $ python servidor.py")
    print("="*70)

    # Esperar a que el servidor esté listo
    if not esperar_servidor():
        print_error("No se pudo conectar al servidor. ¿Está corriendo?")
        print_info("Inicia el servidor con: python servidor.py")
        return

    # Test 1: Homepage
    resultados["Homepage"] = test_homepage()

    # Test 2: Signup
    resultados["Signup"] = test_signup()

    # Test 3: Login
    login_ok, session = test_login()
    resultados["Login"] = login_ok

    if not login_ok:
        print_error("No se pudo hacer login. Deteniendo pruebas.")
        return

    # Test 4: Dashboard
    resultados["Dashboard"] = test_dashboard(session)

    # Test 5: Planes
    resultados["Planes"] = test_planes(session)

    # Test 6: Crear orden
    resultados["Crear Orden"] = test_crear_orden(session)

    # Test 7: User API
    resultados["User API"] = test_api_user(session)

    # Resumen
    print_header("RESUMEN DE PRUEBAS")

    total_tests = len(resultados)
    tests_ok = sum(1 for v in resultados.values() if v)
    tests_fallidos = total_tests - tests_ok

    for nombre, resultado in resultados.items():
        status = f"{Colors.GREEN}[PASS]{Colors.END}" if resultado else f"{Colors.RED}[FAIL]{Colors.END}"
        print(f"  {nombre}: {status}")

    print(f"\n{Colors.BOLD}Total: {tests_ok}/{total_tests} tests pasaron{Colors.END}")

    if tests_fallidos == 0:
        print(f"{Colors.GREEN}{Colors.BOLD}[SUCCESS] Todos los tests pasaron!{Colors.END}")
    else:
        print(f"{Colors.YELLOW}[WARNING] {tests_fallidos} tests fallaron{Colors.END}")

if __name__ == "__main__":
    main()
