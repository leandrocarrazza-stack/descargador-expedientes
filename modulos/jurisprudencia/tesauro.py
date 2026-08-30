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

# Definicion unica en el paquete stjer. Se re-exporta aca para no romper los
# call sites existentes (chat.py, pdf_extractor.py, rutas/jurisprudencia.py).
# La version anterior vivia en este archivo y conservaba toda la puntuacion
# ASCII, con lo cual el match por token de abajo nunca enganchaba junto a
# comas o puntos.
from modulos.jurisprudencia.stjer.normalizacion import (  # noqa: F401
    STOP_WORDS as _STOP_WORDS_COMPARTIDAS,
    normalizar_texto,
)

logger = logging.getLogger(__name__)


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

    Si ya se cosechó el tesauro REAL del sitio
    (`data/jurisprudencia/tesauro_stjer.json`, ver `scripts/stjer.py tesauro
    --cosechar`), se usa ese, con matcheo por puntaje. Si todavía no existe,
    se cae al `tesauro` que venga por parámetro —que hoy es el placeholder de
    10 categorías inventadas— con la estrategia vieja de coincidencia exacta
    de tokens.

    Args:
        consulta: Texto de la consulta del usuario
        tesauro: Dict del tesauro cargado en memoria (respaldo)

    Returns:
        list: Voces jurídicas encontradas (strings)
    """
    if not consulta:
        return []

    try:
        from modulos.jurisprudencia.stjer.tesauro_stjer import Tesauro

        real = Tesauro.cargar()
        if real:
            return [voz for voz, _ in real.buscar_por_etiqueta(consulta, n=10)]
    except Exception as e:  # nunca romper el chat web por esto
        logger.debug("Tesauro real no disponible, se usa el de respaldo: %s", e)

    if not tesauro:
        return []

    STOP_WORDS = _STOP_WORDS_COMPARTIDAS

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
