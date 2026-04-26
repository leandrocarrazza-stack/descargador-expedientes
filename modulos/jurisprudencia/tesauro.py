"""
Cargador de Tesauro STJER JUR
=============================

Carga el tesauro de voces jurídicas del Superior Tribunal de Justicia
de Entre Ríos al iniciar la aplicación.

El tesauro es un archivo JSON con voces jurídicas por materia.
Se carga UNA SOLA VEZ al startup y se almacena en current_app.config.
"""

import json
import logging
from pathlib import Path
from unicodedata import normalize

logger = logging.getLogger(__name__)


def normalizar_texto(texto: str) -> str:
    """Normaliza texto: lowercase, sin acentos."""
    if not texto:
        return ""
    # NFD decompose para separar acentos
    nfd = normalize('NFD', texto.lower())
    # Filtrar caracteres sin acentos
    return ''.join(c for c in nfd if ord(c) < 128 or c.isalpha())


def cargar_tesauros(app):
    """
    Carga ambos archivos de tesauro en app.config.
    Llamado una sola vez desde servidor.py al iniciar.

    Args:
        app: Flask app instance
    """
    import config

    # Cargar tesauro completo
    if config.TESAURO_PATH.exists():
        try:
            with open(config.TESAURO_PATH, 'r', encoding='utf-8') as f:
                tesauro = json.load(f)
            app.config['TESAURO'] = tesauro
            logger.info(f"[OK] Tesauro cargado: {len(tesauro)} entradas")
        except Exception as e:
            logger.error(f"[ERROR] No se pudo cargar tesauro.json: {e}")
            app.config['TESAURO'] = {}
    else:
        logger.warning(
            f"[WARN] Tesauro no encontrado en {config.TESAURO_PATH}. "
            "Descarga falla hasta que lo subas."
        )
        app.config['TESAURO'] = {}

    # Cargar tesauro compacto
    if config.TESAURO_COMPACTO_PATH.exists():
        try:
            with open(config.TESAURO_COMPACTO_PATH, 'r', encoding='utf-8') as f:
                tesauro_compacto = json.load(f)
            app.config['TESAURO_COMPACTO'] = tesauro_compacto
            logger.info(f"[OK] Tesauro compacto cargado: {len(tesauro_compacto)} entradas")
        except Exception as e:
            logger.error(f"[ERROR] No se pudo cargar tesauro_compacto.json: {e}")
            app.config['TESAURO_COMPACTO'] = {}
    else:
        logger.warning(
            f"[WARN] Tesauro compacto no encontrado en {config.TESAURO_COMPACTO_PATH}"
        )
        app.config['TESAURO_COMPACTO'] = {}


def obtener_voces_para_consulta(consulta: str, tesauro: dict) -> list:
    """
    Dado un texto en español, retorna lista de voces STJER relacionadas.

    Estrategia: normalizar tokens, filtrar stop words, buscar coincidencias
    exactas de palabras completas en claves del tesauro y sinónimos.

    Args:
        consulta: Texto de la consulta del usuario
        tesauro: Dict del tesauro cargado en memoria

    Returns:
        list: Voces jurídicas encontradas (strings)
    """
    if not tesauro or not consulta:
        return []

    # Stop words comunes en español
    STOP_WORDS = {
        'y', 'o', 'de', 'la', 'el', 'en', 'un', 'una', 'los', 'las',
        'es', 'son', 'está', 'estoy', 'tienen', 'tiene', 'del', 'al',
        'por', 'para', 'con', 'sin', 'que', 'si', 'este', 'ese', 'aquel'
    }

    voces_encontradas = set()
    consulta_norm = normalizar_texto(consulta)
    # Filtrar stop words de tokens
    tokens = [t for t in consulta_norm.split() if t not in STOP_WORDS and len(t) > 2]

    if not tokens:
        return []

    for clave in tesauro.keys():
        clave_norm = normalizar_texto(clave)
        tokens_clave = [t for t in clave_norm.split() if len(t) > 2]

        # Coincidencia de palabras completas en la clave
        if any(token in tokens_clave for token in tokens):
            voces_encontradas.add(clave)
            continue

        # Buscar en sinónimos/términos del valor
        valor = tesauro.get(clave)
        if isinstance(valor, dict):
            terminos = valor.get('terminos', [])
            if isinstance(terminos, list):
                for termino in terminos:
                    termino_norm = normalizar_texto(str(termino))
                    tokens_termino = [t for t in termino_norm.split() if len(t) > 2]
                    if any(token in tokens_termino for token in tokens):
                        voces_encontradas.add(clave)
                        break

        elif isinstance(valor, list):
            for termino in valor:
                termino_norm = normalizar_texto(str(termino))
                tokens_termino = [t for t in termino_norm.split() if len(t) > 2]
                if any(token in tokens_termino for token in tokens):
                    voces_encontradas.add(clave)
                    break

    return list(voces_encontradas)
