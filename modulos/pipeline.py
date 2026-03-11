"""
Pipeline orquestador para el descargador de expedientes.
======================================================

La clase PipelineDescargador encapsula todo el flujo de ejecución:
1. Autenticación
2. Búsqueda de expediente
3. Descarga de archivos
4. Conversión RTF→PDF
5. Unificación en PDF único
6. Limpieza de temporales

Patrón: Orchestrator / Workflow

Uso:
    pipeline = PipelineDescargador()
    resultado = pipeline.ejecutar("21/24")
    print(resultado)  # [OK] Expediente_21_24_UNIFICADO.pdf
"""

from pathlib import Path
from datetime import datetime
import shutil
import time
from typing import Optional

from modulos.logger import crear_logger
from modulos.modelos import (
    EstadoPipeline,
    ResultadoPipeline,
    Expediente,
    NumeroExpediente,
    Archivo,
)
from modulos.excepciones import (
    MesaVirtualError,
    ErrorAutenticacion,
    ErrorBusqueda,
    ErrorDescarga,
    ErrorConversion,
    ErrorUnificacion,
    OpsionesMultiplesExpediente,
)
from modulos.login import crear_cliente_sesion
from modulos.navegacion import crear_buscador
from modulos.descarga import crear_descargador
from modulos.conversion import crear_conversor
from modulos.unificacion import crear_unificador

import config

logger = crear_logger(__name__)


class PipelineDescargador:
    """
    Orquestador del flujo completo de descarga de expedientes.

    Coordina autenticación, búsqueda, descarga, conversión y unificación.
    Mantiene estado interno y maneja errores de forma tipificada.

    Attributes:
        config: Configuración global (URLs, directorios, etc.)
        estado: EstadoPipeline con el estado actual
    """

    def __init__(self, config_override: Optional[dict] = None) -> None:
        """
        Inicializa el pipeline.

        Args:
            config_override: Diccionario para sobrescribir configuración
        """
        self.config = config
        if config_override:
            for key, value in config_override.items():
                setattr(self.config, key, value)

        self.estado: Optional[EstadoPipeline] = None
        logger.debug("Pipeline inicializado")

    def ejecutar(
        self, numero_expediente: str, limpiar_temp: Optional[bool] = None,
        expediente_preseleccionado: Optional[dict] = None
    ) -> ResultadoPipeline:
        """
        Ejecuta el pipeline completo.

        Orquesta los pasos: autenticación → búsqueda → descarga → conversión
        → unificación → limpieza.

        Args:
            numero_expediente: Número a descargar (ej: "21/24")
            limpiar_temp: Limpiar temporales al final (None = usar config)
            expediente_preseleccionado: Dict con datos del expediente ya seleccionado
                (opcional, para evitar búsqueda cuando hay múltiples opciones)

        Returns:
            ResultadoPipeline: Resultado con PDF final o error

        Raises:
            MesaVirtualError: Si algo falla (se captura internamente)
        """
        try:
            # Crear estado
            carpeta_temporal = self._crear_carpeta_temporal(numero_expediente)
            self.estado = EstadoPipeline(
                numero_expediente=numero_expediente,
                carpeta_temporal=carpeta_temporal,
            )

            logger.info(f"Iniciando pipeline para {numero_expediente}")
            logger.debug(f"Carpeta temporal: {carpeta_temporal}")

            # Guardar expediente preseleccionado (si existe)
            self._expediente_preseleccionado = expediente_preseleccionado

            # Ejecutar pasos en secuencia
            self._paso_autenticacion()
            self._paso_busqueda()
            self._paso_descarga()
            self._paso_conversion()
            self._paso_unificacion()

            # Limpieza
            if limpiar_temp is None:
                limpiar_temp = self.config.LIMPIAR_TEMP
            if limpiar_temp:
                self._paso_limpieza()

            # Marcar como completado
            self.estado.marcar_completado()

            logger.info(f"Pipeline completado exitosamente en {self.estado.duracion_segundos:.1f}s")

            return self._crear_resultado_exito()

        except MesaVirtualError as e:
            logger.error(f"Error en pipeline: {e}")
            self.estado.registrar_error(str(e))
            return self._crear_resultado_error(str(e))
        except Exception as e:
            logger.error(f"Error inesperado en pipeline: {e}", exc_info=True)
            self.estado.registrar_error(f"Error inesperado: {e}")
            return self._crear_resultado_error(f"Error inesperado: {e}")

    # ═══════════════════════════════════════════════════════════════════════════
    # PASOS DEL PIPELINE
    # ═══════════════════════════════════════════════════════════════════════════

    def _paso_autenticacion(self) -> None:
        """Paso 1: Autenticar con Mesa Virtual (con reintentos).

        Intenta autenticar hasta 2 veces con exponential backoff:
        - Intento 1: inmediato
        - Intento 2: después de 5 segundos
        """
        assert self.estado is not None, "Estado no inicializado en autenticación"

        max_reintentos = 2
        for intento in range(max_reintentos):
            try:
                logger.info(f"Paso 1/5: Autenticación (intento {intento + 1}/{max_reintentos})")
                self.estado.cliente = crear_cliente_sesion(
                    api_graphql_url=self.config.API_GRAPHQL,
                    url_mesa_virtual=self.config.MESA_VIRTUAL_URL,
                )
                logger.info("[OK] Autenticación completada")
                return  # Éxito

            except Exception as e:
                error_msg = type(e).__name__
                logger.warning(f"Intento {intento + 1}/{max_reintentos} fallo: {error_msg}")

                # Si hay más reintentos, esperar antes de reintentar
                if intento < max_reintentos - 1:
                    espera_segundos = 5 * (2 ** intento)  # 5s, 10s
                    logger.info(f"Esperando {espera_segundos}s antes de reintentar...")
                    time.sleep(espera_segundos)
                else:
                    # Último intento falló
                    logger.error(f"Autenticacion fallo despues de {max_reintentos} intentos")
                    raise ErrorAutenticacion(f"No se pudo autenticar despues de {max_reintentos} intentos") from e

    def _paso_busqueda(self) -> None:
        """Paso 2: Buscar el expediente."""
        assert self.estado is not None, "Estado no inicializado en búsqueda"
        try:
            logger.info("Paso 2/5: Búsqueda de expediente")
            self.estado.buscador = crear_buscador(self.estado.cliente)

            # Si hay expediente preseleccionado, usarlo directamente
            if (
                hasattr(self, "_expediente_preseleccionado")
                and self._expediente_preseleccionado
            ):
                logger.info(
                    f"Usando expediente preseleccionado: "
                    f"{self._expediente_preseleccionado.get('numero')}"
                )
                self.estado.expediente = self.estado.buscador._dict_a_expediente(
                    self._expediente_preseleccionado
                )
                return

            # Buscar expediente
            resultado = self.estado.buscador.buscar(self.estado.numero_expediente)

            # Procesar resultado
            if resultado is None:
                raise ErrorBusqueda(f"Expediente {self.estado.numero_expediente} no encontrado")

            # buscar() devuelve un dict de expediente directamente
            self.estado.expediente = resultado
            logger.info(f"[OK] Expediente encontrado: {resultado.get('numero')}")

        except OpsionesMultiplesExpediente:
            # Re-lanzar esta excepción especial sin envolverla
            raise
        except ErrorBusqueda:
            raise
        except Exception as e:
            logger.error(f"Fallo búsqueda: {e}")
            raise ErrorBusqueda(f"Error en búsqueda: {e}") from e

    def _paso_descarga(self) -> None:
        """Paso 3: Descargar todos los archivos."""
        assert self.estado is not None, "Estado no inicializado en descarga"
        try:
            logger.info("Paso 3/5: Descarga de archivos")

            self.estado.descargador = crear_descargador(
                self.estado.cliente,
                carpeta_temp=self.estado.carpeta_temporal,
            )

            # Obtener movimientos
            numero_para_descarga = self.estado.expediente.numero
            movimientos = self.estado.descargador.obtener_movimientos(numero_para_descarga)
            self.estado.movimientos = movimientos
            logger.debug(f"Encontrados {len(movimientos)} movimientos")

            # Descargar archivos - ya retorna List[Archivo]
            self.estado.archivos_descargados = self.estado.descargador.descargar_archivos(
                numero_para_descarga,
                movimientos,
            )

            if not self.estado.archivos_descargados:
                raise ErrorDescarga("No se descargó ningún archivo")

            logger.info(f"[OK] Descargados {len(self.estado.archivos_descargados)} archivos")
        except ErrorDescarga:
            raise
        except Exception as e:
            logger.error(f"Fallo descarga: {e}")
            raise ErrorDescarga(f"Error en descarga: {e}") from e

    def _paso_conversion(self) -> None:
        """Paso 4: Convertir RTF a PDF."""
        assert self.estado is not None, "Estado no inicializado en conversión"
        try:
            logger.info("Paso 4/5: Conversión RTF→PDF")

            self.estado.conversor = crear_conversor()

            # Obtener rutas de archivos descargados
            rutas_archivos = [archivo.ruta for archivo in self.estado.archivos_descargados]

            # Convertir - ya retorna List[Archivo] con convertido_a actualizado
            self.estado.archivos_convertidos = self.estado.conversor.convertir_multiples(
                rutas_archivos
            )

            logger.info(
                f"[OK] Conversión completada: {len(self.estado.archivos_convertidos)} archivos"
            )
            self.estado.archivos_finales = self.estado.archivos_convertidos
        except ErrorConversion as e:
            logger.warning(f"Conversión falló, continuando con archivos originales: {e}")
            self.estado.archivos_finales = self.estado.archivos_descargados
        except Exception as e:
            logger.warning(f"Error en conversión (continuando): {e}")
            self.estado.archivos_finales = self.estado.archivos_descargados

    def _paso_unificacion(self) -> None:
        """Paso 5: Unificar en PDF único."""
        assert self.estado is not None, "Estado no inicializado en unificación"
        try:
            logger.info("Paso 5/5: Unificación de PDFs")

            self.estado.unificador = crear_unificador(self.config.OUTPUT_DIR)

            # Obtener rutas de archivos finales
            rutas_archivos = [archivo.ruta for archivo in self.estado.archivos_finales]

            # Nombre de carpeta para el PDF
            nombre_carpeta = self.estado.expediente.numero_descompuesto.numero_sanitizado

            # Unificar
            pdf_final = self.estado.unificador.unificar(rutas_archivos, nombre_carpeta)

            if not pdf_final or not Path(pdf_final).exists():
                raise ErrorUnificacion("No se generó PDF final")

            self.estado.pdf_final = Path(pdf_final)
            logger.info(f"[OK] PDF generado: {self.estado.pdf_final.name}")
        except ErrorUnificacion:
            raise
        except Exception as e:
            logger.error(f"Fallo unificación: {e}")
            raise ErrorUnificacion(f"Error en unificación: {e}") from e

    def _paso_limpieza(self) -> None:
        """Paso final: Limpiar archivos temporales."""
        assert self.estado is not None, "Estado no inicializado en limpieza"
        try:
            logger.info("Limpieza de archivos temporales")
            if self.estado.carpeta_temporal.exists():
                shutil.rmtree(self.estado.carpeta_temporal)
                logger.info(f"[OK] Carpeta temporal eliminada: {self.estado.carpeta_temporal}")
        except Exception as e:
            logger.warning(f"No se pudo limpiar temp: {e}")

    # ═══════════════════════════════════════════════════════════════════════════
    # UTILIDADES Y PROCESAMIENTO
    # ═══════════════════════════════════════════════════════════════════════════

    def _crear_carpeta_temporal(self, numero_expediente: str) -> Path:
        """Crea y retorna la carpeta temporal para el expediente."""
        # Sanitizar número para uso en nombre
        nombre_sanitizado = numero_expediente.replace("/", "_")
        nombre_carpeta = f"exp_{nombre_sanitizado}"

        carpeta = self.config.TEMP_DIR / nombre_carpeta
        carpeta.mkdir(parents=True, exist_ok=True)

        return carpeta

    # ═══════════════════════════════════════════════════════════════════════════
    # RESULTADOS
    # ═══════════════════════════════════════════════════════════════════════════

    def _crear_resultado_exito(self) -> ResultadoPipeline:
        """Crea ResultadoPipeline para ejecución exitosa."""
        assert self.estado is not None, "Estado no inicializado"
        estadisticas = {
            "movimientos": len(self.estado.movimientos),
            "archivos_descargados": len(self.estado.archivos_descargados),
            "archivos_convertidos": len(self.estado.archivos_convertidos),
            "tiempo_segundos": round(self.estado.duracion_segundos, 1),
        }

        return ResultadoPipeline(
            exito=True,
            pdf_final=self.estado.pdf_final,
            estadisticas=estadisticas,
            error=None,
            tiempo_ejecucion=self.estado.duracion_segundos,
        )

    def _crear_resultado_error(self, mensaje_error: str) -> ResultadoPipeline:
        """Crea ResultadoPipeline para ejecución fallida."""
        return ResultadoPipeline(
            exito=False,
            pdf_final=None,
            estadisticas={},
            error=mensaje_error,
            tiempo_ejecucion=self.estado.duracion_segundos if self.estado else 0,
        )
