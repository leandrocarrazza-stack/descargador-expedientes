"""
Chat Conversacional con Claude API
===================================

Interfaz conversacional usando Claude API de Anthropic.
Traduce consultas en lenguaje natural a términos de búsqueda.
"""

import logging
import json
from typing import Dict, List

try:
    import anthropic
except ImportError:
    pass

import config
from modulos.jurisprudencia.tesauro import obtener_voces_para_consulta

logger = logging.getLogger(__name__)


class ChatJurisprudencia:
    """
    Conversational interface usando Claude API.
    Traduce consultas en lenguaje natural a términos de búsqueda.
    """

    SYSTEM_PROMPT = """Sos un asistente legal especializado en jurisprudencia del
Superior Tribunal de Justicia de Entre Ríos (STJER). Tu tarea es ayudar a abogados
y usuarios a encontrar fallos relevantes y entender jurisprudencia.

Cuando el usuario haga una consulta sobre jurisprudencia:
1. Identificá los conceptos jurídicos clave
2. Traducí al lenguaje técnico-jurídico argentino
3. Sugiere términos de búsqueda útiles
4. Respondé SIEMPRE con JSON válido en el formato especificado

Formato de respuesta OBLIGATORIO (JSON válido):
{
  "terminos_busqueda": ["término1", "término2"],
  "voces_juridicas": ["voz1", "voz2"],
  "respuesta_usuario": "Texto natural breve para el usuario",
  "necesita_clarificacion": false
}

Sé conciso y preciso. El JSON debe ser parseable."""

    def __init__(self, anthropic_client, buscador, tesauro: Dict):
        """
        Args:
            anthropic_client: Cliente de Anthropic (anthropic.Anthropic)
            buscador: Instancia de BuscadorJurisprudencia
            tesauro: Dict del tesauro cargado
        """
        self.client = anthropic_client
        self.buscador = buscador
        self.tesauro = tesauro

    def procesar_mensaje(self, mensaje: str, historial: List[Dict] = None) -> Dict:
        """
        Pipeline completo de una consulta:
        1. Envía a Claude para parsear
        2. Ejecuta búsqueda con términos extraídos
        3. Retorna resultados formateados

        Args:
            mensaje: Mensaje del usuario
            historial: Historial de mensajes anteriores (opcional)

        Returns:
            dict: {respuesta, resultados, terminos_usados, voces_usadas}
        """
        if historial is None:
            historial = []

        try:
            # Step 1: Llamar a Claude para extraer términos
            respuesta_claude = self._llamar_claude(mensaje, historial)

            if not respuesta_claude:
                return {
                    'respuesta': 'No se pudo procesar tu consulta. Intenta ser más específico.',
                    'resultados': [],
                    'terminos_usados': [],
                    'voces_usadas': []
                }

            # Step 2: Ejecutar búsqueda con términos extraídos
            terminos = respuesta_claude.get('terminos_busqueda', [])
            voces = respuesta_claude.get('voces_juridicas', [])

            resultados = self.buscador.buscar(
                terminos=terminos,
                voces_tesauro=voces,
                limite=5
            )

            # Step 3: Formatear respuesta final
            return self.formatear_respuesta_con_sumarios(respuesta_claude, resultados, terminos, voces)

        except Exception as e:
            logger.error(f"Error en procesar_mensaje: {e}")
            return {
                'respuesta': f'Error procesando tu consulta: {str(e)}',
                'resultados': [],
                'terminos_usados': [],
                'voces_usadas': []
            }

    def _llamar_claude(self, mensaje: str, historial: List[Dict]) -> Dict:
        """Llama a Claude API y retorna respuesta parseada como JSON."""
        try:
            # Construir messages para Claude
            messages = self._construir_historial_messages(historial, mensaje)

            # Llamar a Claude
            response = self.client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=500,
                system=self.SYSTEM_PROMPT,
                messages=messages
            )

            # Extraer respuesta
            texto_respuesta = response.content[0].text

            # Parsear JSON
            json_start = texto_respuesta.find('{')
            json_end = texto_respuesta.rfind('}') + 1

            if json_start >= 0 and json_end > json_start:
                json_str = texto_respuesta[json_start:json_end]
                resultado = json.loads(json_str)
                return resultado
            else:
                logger.warning(f"No se encontró JSON en respuesta de Claude: {texto_respuesta[:100]}")
                return {}

        except json.JSONDecodeError as e:
            logger.error(f"Error parseando JSON de Claude: {e}")
            return {}
        except Exception as e:
            logger.error(f"Error llamando a Claude: {e}")
            return {}

    def _construir_historial_messages(self, historial: List[Dict], mensaje_actual: str) -> List[Dict]:
        """
        Convierte historial de sesión al formato de messages de Anthropic.

        Args:
            historial: Lista de mensajes previos [{'role': 'user'|'assistant', 'content': '...'}]
            mensaje_actual: Mensaje actual del usuario

        Returns:
            list: Mensajes formateados para Anthropic API
        """
        messages = []

        # Agregar historial previo (máximo 10 mensajes para no exceder contexto)
        for msg in historial[-10:]:
            messages.append({
                'role': msg.get('role', 'user'),
                'content': msg.get('content', '')
            })

        # Agregar mensaje actual
        messages.append({
            'role': 'user',
            'content': mensaje_actual
        })

        return messages

    def formatear_respuesta_con_sumarios(self, respuesta_claude: Dict,
                                          resultados: List[Dict],
                                          terminos: List[str],
                                          voces: List[str]) -> Dict:
        """
        Construye respuesta final con sumarios completos.

        Args:
            respuesta_claude: Dict con análisis de Claude
            resultados: Resultados de búsqueda
            terminos: Términos usados en búsqueda
            voces: Voces jurídicas usadas

        Returns:
            dict: Respuesta para el frontend
        """
        try:
            # Respuesta del usuario desde Claude
            respuesta_usuario = respuesta_claude.get('respuesta_usuario', '')

            # Si hay resultados, agregar intro
            if resultados:
                respuesta_usuario += f"\n\nEncontré {len(resultados)} fallo(s) relacionado(s):"

            return {
                'respuesta': respuesta_usuario,
                'resultados': resultados,
                'terminos_usados': terminos,
                'voces_usadas': voces,
                'cantidad_resultados': len(resultados)
            }

        except Exception as e:
            logger.error(f"Error formateando respuesta: {e}")
            return {
                'respuesta': 'Error formateando respuesta',
                'resultados': [],
                'terminos_usados': [],
                'voces_usadas': []
            }
