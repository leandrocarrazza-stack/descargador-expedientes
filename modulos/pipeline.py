# modulos/pipeline.py
"""
Pipeline de Descarga - FASE 3 v2 (Sincrónico)

Orquesta el flujo completo:
1. Autenticación en Mesa Virtual
2. Búsqueda de expediente
3. Descarga de archivos
4. Conversión RTF>PDF
5. Unificación de PDFs

Retorna: ResultadoPipeline con .exito, .pdf_final, .error
"""

import logging
import shutil
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

from modulos.login import ClienteSelenium, crear_cliente_sesion
from modulos.navegacion import BuscadorExpedientes
from modulos.descarga import DescargadorArchivos
from modulos.conversion import ConversorRTF
from modulos.unificacion import UnificadorPDF
from modulos.compresion import comprimir_pdf
import config

logger = logging.getLogger(__name__)


@dataclass
class ResultadoPipeline:
    """Resultado de ejecutar el pipeline"""
    exito: bool
    expediente: Optional[Dict[str, Any]] = None
    pdf_final: Optional[Path] = None
    error: Optional[str] = None
    tipo_error: Optional[str] = None
    movimientos: Optional[List[Dict]] = None
    archivos_descargados: int = 0
    opciones: Optional[List[Dict[str, Any]]] = None  # Cuando hay múltiples expedientes


class PipelineDescargador:
    """Orquesta la descarga completa de un expediente"""

    def __init__(self):
        self.cliente: Optional[ClienteSelenium] = None
        self.buscador: Optional[BuscadorExpedientes] = None
        self.descargador: Optional[DescargadorArchivos] = None
        self.conversor: Optional[ConversorRTF] = None
        self.unificador: Optional[UnificadorPDF] = None
        self.carpeta_temp: Optional[Path] = None

    def ejecutar(self, numero_expediente: str, limpiar_temp: bool = True, indice_expediente: int = None) -> ResultadoPipeline:
        """
        Ejecuta el pipeline completo de forma sincrónica (bloqueante).

        Args:
            numero_expediente: Número a descargar (ej: "21/24")
            limpiar_temp: Limpiar carpeta temporal al finalizar

        Returns:
            ResultadoPipeline con resultado o error
        """
        try:
            logger.info(f"[PIPELINE] Iniciando descarga: {numero_expediente}")

            # PASO 1: AUTENTICACIÓN
            logger.info("[PASO 1/5] Autenticación en Mesa Virtual")
            self.cliente = crear_cliente_sesion(usar_sesion_guardada=True, headless=True)
            if not self.cliente:
                return ResultadoPipeline(
                    exito=False,
                    error="No se pudo crear sesión. Intenta nuevamente.",
                    tipo_error="auth_failed"
                )

            # PASO 2: BÚSQUEDA
            logger.info("[PASO 2/5] Búsqueda de expediente")
            self.buscador = BuscadorExpedientes(self.cliente)
            resultado_busqueda = self.buscador.buscar(numero_expediente, indice_expediente=indice_expediente)

            if not resultado_busqueda:
                # Verificar si hay múltiples opciones pendientes de selección
                if self.buscador._opciones_multiples:
                    opciones = self.buscador._opciones_multiples
                    logger.info(f"[MULTIPLES] {len(opciones)} expedientes encontrados, requiere selección")
                    return ResultadoPipeline(
                        exito=False,
                        tipo_error="multiples_opciones",
                        opciones=[{
                            'indice': i + 1,
                            'numero': op.get('numero', ''),
                            'caratula': op.get('caratula', 'Sin descripción'),
                            'tribunal': op.get('tribunal', 'No especificado'),
                        } for i, op in enumerate(opciones)]
                    )

                logger.error(f"Expediente no encontrado: {numero_expediente}")
                return ResultadoPipeline(
                    exito=False,
                    error=f"Expediente {numero_expediente} no encontrado",
                    tipo_error="not_found"
                )

            expediente = resultado_busqueda  # dict
            logger.info(f"[OK] Expediente encontrado: {expediente.get('numero', numero_expediente)}")

            # PASO 3: DESCARGA DE ARCHIVOS
            logger.info("[PASO 3/5] Descarga de archivos")

            # Crear carpeta temporal PRIMERO
            self.carpeta_temp = Path(config.TEMP_DIR) / f"exp_{numero_expediente.replace('/', '_')}"
            self.carpeta_temp.mkdir(parents=True, exist_ok=True)

            # Crear descargador con carpeta temp
            self.descargador = DescargadorArchivos(self.cliente, self.carpeta_temp)

            # Descargar por paginas: en cada pagina descargamos todos los archivos
            # ANTES de navegar a la siguiente. Esto evita que los JWT tokens expiren.
            # Problema critico: al navegar de pagina 1 a 2, los tokens de pagina 1 vencen -> HTTP 403
            archivos_descargados = self.descargador.descargar_todo_por_paginas(numero_expediente)
            logger.info(f"[OK] {len(archivos_descargados)} archivos descargados")

            if not archivos_descargados:
                return ResultadoPipeline(
                    exito=False,
                    error="No se pudieron descargar archivos",
                    tipo_error="download_failed",
                    expediente=expediente
                )

            # CERRAR CLIENTE AQUÍ para evitar crash
            # El navegador ya no se necesita, conversión/unificación no requieren driver
            logger.info("[CLEANUP] Cerrando navegador para evitar crash")
            if self.cliente:
                try:
                    self.cliente.cerrar()
                    self.cliente = None
                    logger.info("[OK] Navegador cerrado exitosamente")
                except Exception as e:
                    logger.warning(f"[WARN] Error al cerrar navegador: {e}")

            # PASO 4: CONVERSIÓN RTF>PDF
            logger.info("[PASO 4/5] Conversión RTF>PDF")
            self.conversor = ConversorRTF()

            # Convertir archivos descargados manteniendo metadata
            # descargar_todo_por_paginas() retorna {path, tipo, movimiento, url}
            archivos_convertidos = []
            for arch in archivos_descargados:
                ruta_original = arch['path']
                pdf_convertido = self.conversor.convertir_rtf_a_pdf(ruta_original)
                if pdf_convertido:
                    # Actualizar ruta con conversión realizada, mantener metadata
                    arch['path'] = pdf_convertido
                    archivos_convertidos.append(arch)

            logger.info(f"[OK] Conversión completada: {len(archivos_convertidos)} archivos")

            if not archivos_convertidos:
                return ResultadoPipeline(
                    exito=False,
                    error="Falló la conversión RTF>PDF",
                    tipo_error="conversion_failed",
                    expediente=expediente
                )

            # PASO 5: UNIFICACIÓN
            logger.info("[PASO 5/5] Unificación de PDFs")
            self.unificador = UnificadorPDF(config.OUTPUT_DIR)

            # Pasar archivos con metadata al unificador
            pdf_final = self.unificador.unificar(numero_expediente, archivos_convertidos)

            if not pdf_final or not pdf_final.exists():
                logger.error(f"PDF final no generado o inexistente: {pdf_final}")
                return ResultadoPipeline(
                    exito=False,
                    error="No se pudo generar el PDF final",
                    tipo_error="unification_failed",
                    expediente=expediente
                )

            logger.info(f"[OK] PDF final generado: {pdf_final}")

            # PASO 6 (OPCIONAL): COMPRESIÓN
            # Solo comprime si COMPRIMIR_PDF=true en .env (desactivado por defecto)
            pdf_final = comprimir_pdf(pdf_final)

            # SUCCESS
            return ResultadoPipeline(
                exito=True,
                expediente=expediente,
                pdf_final=pdf_final,
                archivos_descargados=len(archivos_descargados)
            )

        except Exception as e:
            logger.error(f"[ERROR] Excepción en pipeline: {str(e)}", exc_info=True)
            return ResultadoPipeline(
                exito=False,
                error=f"Error interno: {str(e)}",
                tipo_error="exception"
            )

        finally:
            # ═══════════════════════════════════════════════════════════════
            # LIMPIEZA AGRESIVA
            # Siempre limpiar recursos, haya éxito o error.
            # En servidores cloud con disco limitado, dejar basura = disco lleno.
            # ═══════════════════════════════════════════════════════════════

            # 1. Cerrar navegador Chrome (liberar RAM)
            if self.cliente:
                try:
                    self.cliente.cerrar()
                except Exception:
                    pass

            # 2. Borrar carpeta temporal completa (RTFs, PDFs individuales, lotes)
            #    El PDF final ya está en OUTPUT_DIR, así que temp/ es descartable
            if self.carpeta_temp and self.carpeta_temp.exists():
                try:
                    shutil.rmtree(self.carpeta_temp, ignore_errors=True)
                    logger.info(f"[CLEANUP] Carpeta temporal eliminada: {self.carpeta_temp}")
                except Exception as e:
                    logger.warning(f"[CLEANUP] No se pudo eliminar temp: {e}")
