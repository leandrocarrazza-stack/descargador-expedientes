#!/usr/bin/env python3
"""
test_descarga.py
================

Script para probar el endpoint POST /descargas/expediente

Pasos:
1. Crea un usuario de prueba
2. Hace login
3. Ejecuta POST /descargas/expediente
4. Verifica la respuesta
"""

import requests
import json
from pathlib import Path

# URL base del servidor
BASE_URL = "http://localhost:5000"

# Datos de usuario de prueba (usar email real)
EMAIL_TEST = "leandro@gmail.com"
PASSWORD_TEST = "TestPass123!@#"
NUMERO_EXPEDIENTE = "21/24"  # Ajusta según sea necesario

def test_pipeline():
    session = requests.Session()

    print("\n" + "="*70)
    print("TEST DESCARGADOR DE EXPEDIENTES")
    print("="*70 + "\n")

    # PASO 1: Verificar usuario (ya está creado en la BD)
    print("[PASO 1] Usuario de prueba (pre-creado en BD)...")
    print("-" * 70)
    print(f"Email: {EMAIL_TEST}")
    print(f"Créditos iniciales: 10")

    # PASO 2: Login
    print("\n[PASO 2] Login...")
    print("-" * 70)

    login_data = {
        "email": EMAIL_TEST,
        "password": PASSWORD_TEST
    }

    resp = session.post(f"{BASE_URL}/auth/login", json=login_data)
    print(f"Login: {resp.status_code}")
    if resp.status_code != 200:
        print(f"[ERROR] Login fallido: {resp.text}")
        return False

    data = resp.json()
    print(f"[OK] Login exitoso. User ID: {data.get('user_id')}")

    # PASO 3: Agregar créditos (simulado - en dev podemos modificar BD)
    print("\n[PASO 3] Verificar créditos...")
    print("-" * 70)

    # GET /auth/user para ver créditos
    resp = session.get(f"{BASE_URL}/auth/user")
    user_data = resp.json()
    print(f"Créditos disponibles: {user_data.get('creditos_disponibles', 'N/A')}")

    # Si no hay créditos, no podemos descargar
    if user_data.get('creditos_disponibles', 0) < 1:
        print("[WARN] Sin créditos, pero intentaremos de todas formas...")

    # PASO 4: Descargar expediente
    print(f"\n[PASO 4] POST /descargas/expediente (expediente: {NUMERO_EXPEDIENTE})...")
    print("-" * 70)

    download_data = {
        "numero_expediente": NUMERO_EXPEDIENTE
    }

    print(f"Enviando: {json.dumps(download_data, indent=2)}")
    print("\nEsperando respuesta...")

    resp = session.post(
        f"{BASE_URL}/descargas/expediente",
        json=download_data,
        timeout=300  # 5 minutos de timeout
    )

    print(f"Status Code: {resp.status_code}")

    try:
        result = resp.json()
        print(f"\nRespuesta JSON:")
        print(json.dumps(result, indent=2, ensure_ascii=False))

        if result.get('exito'):
            print(f"\n[OK] Descarga exitosa!")
            print(f"  Expediente ID: {result.get('expediente_id')}")
            print(f"  PDF URL: {result.get('pdf_url')}")
            print(f"  Créditos restantes: {result.get('creditos_restantes')}")
            return True
        else:
            print(f"\n[ERROR] Descarga fallida: {result.get('mensaje')}")
            print(f"  Tipo error: {result.get('tipo_error')}")
            return False
    except Exception as e:
        print(f"[ERROR] No se pudo parsear respuesta: {e}")
        print(f"Response text: {resp.text[:500]}")
        return False

if __name__ == "__main__":
    try:
        exito = test_pipeline()
        print("\n" + "="*70)
        if exito:
            print("TEST COMPLETADO: EXITO")
        else:
            print("TEST COMPLETADO: FALLÓ")
        print("="*70 + "\n")
    except Exception as e:
        print(f"\n[ERROR] Excepcion no manejada: {e}")
        import traceback
        traceback.print_exc()
