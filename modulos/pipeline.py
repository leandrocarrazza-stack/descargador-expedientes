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
from modulos.auth_mv import crear_cliente_desde_cookies
from modulos.navegacion import BuscadorExpedientes
from modulos.descarga import DescargadorArchivos
from modulos.progreso import PROGRESO_CADA_N_ARCHIVOS
from modulos.conversion import ConversorRTF, matar_procesos_soffice, memoria_disponible_mb
from modulos.unificacion import UnificadorPDF
from modulos.compresion import comprimir_pdf
import config

logger = logging.getLogger(__name__)


def _log_memoria(etapa: str):
    """
    Loguea la memoria disponible del CONTENEDOR (no del host) en puntos
    clave del pipeline.

    Por qué: en un servidor de 512 MB totales, lo que importa es cuánto
    queda libre para que Chrome/LibreOffice puedan arrancar, no cuánto usa
    el proceso Flask en sí — ni cuánta RAM tiene libre el host físico que
    corre Docker (eso es lo que /proc/meminfo reportaba antes, mostrando
    valores de varios GB que no tenían nada que ver con el límite real de
    este contenedor). Ver memoria_disponible_mb() para el detalle de cómo
    se lee el límite real vía cgroup.
    """
    mb = memoria_disponible_mb()
    if mb is not None:
        logger.info(f"[MEMORIA] Disponible tras {etapa}: {mb} MB")
    else:
        logger.debug("[MEMORIA] No se pudo leer memoria disponible")


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
        self._on_progreso = None

    def _emitir(self, **datos):
        """
        Publica el avance del pipeline hacia el frontend.

        Traga cualquier excepción del callback a propósito: el progreso es
        cosmético y no puede hacer fracasar una descarga que ya consumió un
        crédito del usuario.
        """
        if not self._on_progreso:
            return
        try:
            self._on_progreso(datos)
        except Exception:
            logger.debug("Callback de progreso falló (ignorado)", exc_info=True)

    def ejecutar(self, numero_expediente: str, limpiar_temp: bool = True, indice_expediente: int = None, cookies_mv: list = None, on_progreso=None) -> ResultadoPipeline:
        """
        Ejecuta el pipeline completo de forma sincrónica (bloqueante).

        Args:
            numero_expediente: Número a descargar (ej: "21/24")
            limpiar_temp: Limpiar carpeta temporal al finalizar
            on_progreso: callable opcional que recibe dicts con el avance real
                (fase, actual, total...) para que el frontend muestre
                "archivo N de TOTAL" en vez de una barra inventada.

        Returns:
            ResultadoPipeline con resultado o error
        """
        self._on_progreso = on_progreso
        try:
            logger.info(f"[PIPELINE] Iniciando descarga: {numero_expediente}")

            # Limpieza preventiva: si un pipeline anterior crasheó a mitad de la
            # conversión (excepción no manejada, OOM-kill del proceso), puede haber
            # dejado un soffice.bin residente consumiendo memoria desde entonces.
            # Se libera ANTES de arrancar Chrome, que es lo que más RAM necesita.
            matar_procesos_soffice()
            _log_memoria("inicio")

            # PASO 1: AUTENTICACIÓN
            logger.info("[PASO 1/5] Autenticación en Mesa Virtual")
            self._emitir(fase='auth', actual=0, total=None, total_exacto=False)

            if cookies_mv:
                # Usar las cookies del usuario (Login Relay) — camino normal en producción
                logger.info("[PASO 1/5] Usando cookies del usuario (Login Relay)")
                self.cliente = crear_cliente_desde_cookies(cookies_mv)
                if not self.cliente:
                    return ResultadoPipeline(
                        exito=False,
                        error="Tu sesión de Mesa Virtual expiró. Reconectá tu cuenta.",
                        tipo_error="auth_failed"
                    )
            else:
                # Fallback: sesión local (solo para desarrollo/testing)
                logger.info("[PASO 1/5] Sin cookies_mv, usando sesión local (modo desarrollo)")
                self.cliente = crear_cliente_sesion(usar_sesion_guardada=True, headless=True)
                if not self.cliente:
                    return ResultadoPipeline(
                        exito=False,
                        error="No se pudo crear sesión. Intenta nuevamente.",
                        tipo_error="auth_failed"
                    )

            _log_memoria("autenticación (Chrome recién arrancado)")

            # PASO 2: BÚSQUEDA
            logger.info("[PASO 2/5] Búsqueda de expediente")
            self._emitir(fase='busqueda', actual=0, total=None, total_exacto=False)
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

            _log_memoria("búsqueda")

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
            archivos_descargados = self.descargador.descargar_todo_por_paginas(
                numero_expediente, on_progreso=self._on_progreso
            )
            logger.info(f"[OK] {len(archivos_descargados)} archivos descargados")
            _log_memoria("descarga (Chrome todavía abierto)")

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

            _log_memoria("cierre de Chrome")

            # PASO 4: CONVERSIÓN RTF>PDF
            logger.info("[PASO 4/5] Conversión RTF>PDF")
            self.conversor = ConversorRTF()

            # Convertir archivos descargados manteniendo metadata
            # descargar_todo_por_paginas() retorna {path, tipo, movimiento, url}
            total_a_convertir = len(archivos_descargados)
            self._emitir(fase='conversion', actual=0, total=total_a_convertir, total_exacto=True)

            archivos_convertidos = []
            for i, arch in enumerate(archivos_descargados, 1):
                ruta_original = arch['path']
                pdf_convertido = self.conversor.convertir_rtf_a_pdf(ruta_original)
                if pdf_convertido:
                    # Actualizar ruta con conversión realizada, mantener metadata
                    arch['path'] = pdf_convertido
                    archivos_convertidos.append(arch)
                if i % PROGRESO_CADA_N_ARCHIVOS == 0:
                    self._emitir(fase='conversion', actual=i, total=total_a_convertir, total_exacto=True)

            logger.info(f"[OK] Conversión completada: {len(archivos_convertidos)} archivos")

            # Liberar la memoria del proceso soffice.bin residente (si hubo al
            # menos un RTF) ANTES de que PyPDF2 tenga que cargar y combinar todos
            # los PDFs — ambos compiten por la misma RAM del proceso Python.
            matar_procesos_soffice()
            _log_memoria("conversión RTF>PDF (soffice liberado)")

            if not archivos_convertidos:
                return ResultadoPipeline(
                    exito=False,
                    error="Falló la conversión RTF>PDF",
                    tipo_error="conversion_failed",
                    expediente=expediente
                )

            # PASO 5: UNIFICACIÓN
            logger.info("[PASO 5/5] Unificación de PDFs")
            self._emitir(fase='unificacion', actual=0, total=len(archivos_convertidos), total_exacto=True)
            self.unificador = UnificadorPDF(config.OUTPUT_DIR)

            # Pasar archivos con metadata al unificador
            pdf_final = self.unificador.unificar(
                numero_expediente, archivos_convertidos, on_progreso=self._on_progreso
            )

            if not pdf_final or not pdf_final.exists():
                logger.error(f"PDF final no generado o inexistente: {pdf_final}")
                return ResultadoPipeline(
                    exito=False,
                    error="No se pudo generar el PDF final",
                    tipo_error="unification_failed",
                    expediente=expediente
                )

            logger.info(f"[OK] PDF final generado: {pdf_final}")
            _log_memoria("unificación")

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
            if "SESION_MV_EXPIRADA" in str(e):
                logger.warning(f"[AUTH] Sesión de Mesa Virtual expirada durante el proceso: {e}")
                return ResultadoPipeline(
                    exito=False,
                    error="Tu sesión de Mesa Virtual expiró. Reconectá tu cuenta.",
                    tipo_error="auth_failed"
                )
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
