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
import uuid
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Dict, Any

from modulos.login import ClienteSelenium, crear_cliente_sesion
from modulos.auth_mv import crear_cliente_desde_cookies
from modulos.navegacion import BuscadorExpedientes
from modulos.descarga import DescargadorArchivos, EstrategiaCompleta, EstrategiaIncremental, _leer_filas_pagina, huella_fila
from modulos.progreso import PROGRESO_CADA_N_ARCHIVOS
from modulos.conversion import ConversorRTF, matar_procesos_soffice, memoria_disponible_mb, parece_pdf, LOTE_CONVERSION
from modulos.unificacion import UnificadorPDF, anexar_con_qpdf
from modulos.excepciones import ErrorUnificacion
from modulos.compresion import comprimir_pdf
from modulos.concurrencia import ControlJob
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

    # Metadatos para la actualización incremental (modulos/descarga.py:
    # EstrategiaCompleta/EstrategiaIncremental). Se completan en TODA
    # descarga exitosa (no sólo en una actualización) para que la PRÓXIMA
    # actualización incremental de este expediente tenga con qué anclar.
    total_filas: Optional[int] = None
    huellas: Optional[List[str]] = None
    # True si esta fue una actualización incremental que no pudo incluir
    # todos los movimientos nuevos (se detectaron intercalados por debajo
    # del ancla de huellas — ver EstrategiaIncremental).
    parcial: bool = False


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

    def _autenticar(self, cookies_mv: Optional[list]) -> Optional[ClienteSelenium]:
        """
        Crea un cliente autenticado en Mesa Virtual. Usado tanto por PASO 1
        (auth inicial) como por el reciclaje proactivo de navegador (ver
        _reconectar en ejecutar()), para que ambos caminos de login no se
        desincronicen si el flujo de autenticación cambia.

        Retorna None si falla (el caller decide qué error mostrar).
        """
        if cookies_mv:
            # Usar las cookies del usuario (Login Relay) — camino normal en producción
            logger.info("Usando cookies del usuario (Login Relay)")
            return crear_cliente_desde_cookies(cookies_mv)
        # Fallback: sesión local (solo para desarrollo/testing)
        logger.info("Sin cookies_mv, usando sesión local (modo desarrollo)")
        return crear_cliente_sesion(usar_sesion_guardada=True, headless=True)

    def ejecutar(self, numero_expediente: str, limpiar_temp: bool = True, indice_expediente: int = None, cookies_mv: list = None, on_progreso=None, control: Optional[ControlJob] = None, modo_actualizacion: Optional[dict] = None) -> ResultadoPipeline:
        """
        Ejecuta el pipeline completo de forma sincrónica (bloqueante).

        Args:
            numero_expediente: Número a descargar (ej: "21/24")
            limpiar_temp: Limpiar carpeta temporal al finalizar
            on_progreso: callable opcional que recibe dicts con el avance real
                (fase, actual, total...) para que el frontend muestre
                "archivo N de TOTAL" en vez de una barra inventada.
            control: handle de concurrencia (modulos/concurrencia.py) que
                coordina cuántos Chrome/conversiones pueden correr a la vez.
                Si se omite (llamadores viejos: CLI, MCP, Celery legacy), se
                usa un control nulo que no bloquea ni limita nada — igual que
                el comportamiento de antes de que existiera la cola.
            modo_actualizacion: si se pasa, en vez de una descarga completa
                se hace una actualización incremental (ver
                modulos/descarga.py::EstrategiaIncremental). Dict con:
                - total_filas_previo (int): total de filas de la última
                  descarga/actualización de este expediente.
                - huellas_previas (list[str]): huellas de las primeras filas
                  de esa descarga (ver modulos/descarga.py::huella_fila).
                - pdf_previo_local (str|Path): PDF de esa descarga, ya
                  bajado del storage a un archivo local (rutas/descargas.py
                  se encarga de traerlo antes de llamar acá).

        Returns:
            ResultadoPipeline con resultado o error. tipo_error puede ser
            'sin_novedades' (no hay movimientos nuevos, no se cobra crédito)
            o 'expediente_cambio' (cambió de una forma que no se puede
            actualizar de manera incremental) además de los ya existentes.
        """
        self._on_progreso = on_progreso
        control = control or ControlJob.nulo()
        # Token único por job: evita que dos descargas del MISMO expediente
        # (dos usuarios, o el mismo usuario dos veces) compartan carpeta
        # temporal o nombre de PDF final y se pisen entre sí.
        self._token = uuid.uuid4().hex[:8]
        try:
            logger.info(f"[PIPELINE] Iniciando descarga: {numero_expediente}")

            # Limpieza preventiva: si un pipeline anterior crasheó a mitad de la
            # conversión (excepción no manejada, OOM-kill del proceso), puede haber
            # dejado un soffice.bin residente consumiendo memoria desde entonces.
            # Se libera ANTES de arrancar Chrome, que es lo que más RAM necesita.
            # nadie_mas_convirtiendo() (en vez de control.es_nulo) deja esto activo
            # también con concurrencia real: antes se apagaba del todo en
            # producción porque control nunca es nulo ahí, así que un soffice
            # huérfano de un crash (ver los reinicios de instancia por OOM) sólo
            # se limpiaba al reiniciar el contenedor entero, nunca entre jobs.
            if control.nadie_mas_convirtiendo():
                matar_procesos_soffice()
            _log_memoria("inicio")

            # PASO 1: AUTENTICACIÓN
            logger.info("[PASO 1/5] Autenticación en Mesa Virtual")
            self._emitir(fase='auth', actual=0, total=None, total_exacto=False)

            self.cliente = self._autenticar(cookies_mv)
            if not self.cliente:
                return ResultadoPipeline(
                    exito=False,
                    error="Tu sesión de Mesa Virtual expiró. Reconectá tu cuenta."
                        if cookies_mv else "No se pudo crear sesión. Intenta nuevamente.",
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

            # Crear carpeta temporal PRIMERO. El token la hace única por job:
            # sin él, dos descargas del mismo expediente en simultáneo
            # comparten carpeta y el rmtree del finally de una borra las
            # descargas de la otra a mitad de camino.
            self.carpeta_temp = Path(config.TEMP_DIR) / f"exp_{numero_expediente.replace('/', '_')}_{self._token}"
            self.carpeta_temp.mkdir(parents=True, exist_ok=True)

            # fn_reconectar: recrea el cliente autenticado (mismo camino que PASO 1)
            # y vuelve a buscar el expediente, para el reciclaje proactivo de
            # navegador en expedientes largos (ver DescargadorArchivos y
            # _reciclar_navegador_en_pagina en modulos/descarga.py). Sin esto,
            # el purgado de memoria de Chrome (HeapProfiler.collectGarbage)
            # queda como único paliativo, y en la práctica libera 0-16 MB con
            # el servidor ya en el límite de los 512 MB del plan.
            def _reconectar():
                nuevo_cliente = self._autenticar(cookies_mv)
                if not nuevo_cliente:
                    return None
                nuevo_buscador = BuscadorExpedientes(nuevo_cliente)
                if not nuevo_buscador.buscar(numero_expediente, indice_expediente=indice_expediente):
                    return None
                return nuevo_cliente

            # Crear descargador con carpeta temp
            self.descargador = DescargadorArchivos(self.cliente, self.carpeta_temp, fn_reconectar=_reconectar)

            # ── Actualización incremental: decidir delta ANTES de descargar ──
            # (ver modulos/descarga.py::EstrategiaIncremental para el detalle
            # de cómo se ancla en filas). Si no es una actualización, se
            # descarga todo con la estrategia por defecto de siempre.
            estrategia = EstrategiaCompleta()
            if modo_actualizacion:
                total_filas_previo = modo_actualizacion.get('total_filas_previo') or 0
                huellas_previas = modo_actualizacion.get('huellas_previas') or []

                rango_actual = self.descargador.leer_total_filas_confiable(self.cliente.driver)
                if not rango_actual:
                    return ResultadoPipeline(
                        exito=False,
                        tipo_error="expediente_cambio",
                        error="No pudimos confirmar el estado actual del expediente. Hacé una descarga completa.",
                        expediente=expediente,
                    )

                desde, hasta, total_filas_actual = rango_actual
                tam_pagina = max(hasta - desde + 1, 1)
                delta = total_filas_actual - total_filas_previo

                if delta < 0:
                    return ResultadoPipeline(
                        exito=False,
                        tipo_error="expediente_cambio",
                        error="El expediente tiene menos movimientos que en tu última descarga. Hacé una descarga completa.",
                        expediente=expediente,
                    )

                if delta == 0:
                    filas_pagina1 = _leer_filas_pagina(self.cliente.driver)
                    huellas_actuales = [huella_fila(f) for f in filas_pagina1[:len(huellas_previas)]]
                    if huellas_previas and huellas_actuales == huellas_previas:
                        return ResultadoPipeline(
                            exito=False,
                            tipo_error="sin_novedades",
                            error="No hay movimientos nuevos desde tu última descarga.",
                            expediente=expediente,
                            total_filas=total_filas_actual,
                        )
                    return ResultadoPipeline(
                        exito=False,
                        tipo_error="expediente_cambio",
                        error="El expediente cambió de una forma que no se puede actualizar de manera incremental. Hacé una descarga completa.",
                        expediente=expediente,
                    )

                logger.info(f"[INCREMENTAL] delta={delta} fila(s) nueva(s) (tam_pagina={tam_pagina})")
                estrategia = EstrategiaIncremental(delta, huellas_previas, tam_pagina)

            # Descargar por paginas: en cada pagina descargamos todos los archivos
            # ANTES de navegar a la siguiente. Esto evita que los JWT tokens expiren.
            # Problema critico: al navegar de pagina 1 a 2, los tokens de pagina 1 vencen -> HTTP 403
            archivos_descargados = self.descargador.descargar_todo_por_paginas(
                numero_expediente, on_progreso=self._on_progreso, estrategia=estrategia
            )
            # Re-sincronizar: si hubo reciclaje de navegador (ver
            # _reciclar_navegador_en_pagina en modulos/descarga.py), el
            # descargador se quedó con un cliente nuevo — self.cliente todavía
            # apunta al original, YA CERRADO por el propio reciclaje. Sin este
            # re-sync, el cierre de abajo y el finally cierran un objeto muerto
            # y el navegador real (el último vivo) queda huérfano para
            # siempre, exactamente lo que este reciclaje debía evitar.
            self.cliente = self.descargador.cliente
            logger.info(f"[OK] {len(archivos_descargados)} archivos descargados")
            _log_memoria("descarga (Chrome todavía abierto)")

            if modo_actualizacion and estrategia.posicion_ancla is None:
                # No debería pasar (descargar_todo_por_paginas ya levanta
                # ErrorDescarga si se agota el margen de seguridad sin
                # encontrar el ancla), pero si Mesa Virtual se quedó sin
                # páginas antes de eso, es la misma situación: no hay
                # certeza de dónde empieza lo ya descargado.
                return ResultadoPipeline(
                    exito=False,
                    tipo_error="expediente_cambio",
                    error="No se pudo confirmar la continuidad con tu descarga anterior. Hacé una descarga completa.",
                    expediente=expediente,
                )

            es_parcial = bool(modo_actualizacion) and estrategia.posicion_ancla < estrategia.delta

            if not archivos_descargados:
                if modo_actualizacion:
                    # Hubo filas nuevas (delta > 0) pero ninguna traía un
                    # adjunto: el PDF no cambia, pero sí hay que actualizar
                    # los metadatos para la PRÓXIMA actualización.
                    pdf_previo = modo_actualizacion.get('pdf_previo_local')
                    if not pdf_previo or not Path(pdf_previo).exists():
                        return ResultadoPipeline(
                            exito=False,
                            error="No se encontró el PDF de la descarga anterior",
                            tipo_error="pdf_previo_faltante",
                            expediente=expediente,
                        )
                    return ResultadoPipeline(
                        exito=True,
                        expediente=expediente,
                        pdf_final=Path(pdf_previo),
                        archivos_descargados=0,
                        total_filas=total_filas_actual,
                        huellas=estrategia.huellas_iniciales,
                        parcial=es_parcial,
                    )
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

            # Con SOLAPE_NAVEGADOR_CONVERSION=true, este permiso se libera DE
            # VERDAD acá: el siguiente job de la cola puede arrancar su Chrome
            # mientras este pipeline sigue en conversión/unificación (ya sin
            # Chrome propio abierto). Con el default (false), es un no-op —
            # el permiso se retiene hasta el final, como el semáforo de antes.
            control.liberar_navegador()
            _log_memoria("cierre de Chrome")

            # PASO 4: CONVERSIÓN RTF>PDF
            # El permiso de conversión es exclusivo (cap 1, sin importar
            # cuántos navegadores estén habilitados): cubre PASO 4 + PASO 5,
            # porque el par de RAM peligroso es exactamente soffice de un job
            # + el merge de PyPDF2 de otro. Si no se consigue a tiempo, el
            # servidor está genuinamente saturado — no tiene sentido reintentar
            # desde acá, se corta con un error claro.
            if not control.adquirir_conversion(timeout=600):
                logger.error("[PASO 4/5] No se pudo obtener el permiso de conversión (servidor ocupado)")
                return ResultadoPipeline(
                    exito=False,
                    error="El servidor está muy ocupado en este momento. Probá de nuevo en unos minutos.",
                    tipo_error="servidor_ocupado",
                    expediente=expediente
                )

            logger.info("[PASO 4/5] Conversión RTF>PDF")
            # perfil_dir propio: cinturón y tiradores si algún día sube el cap
            # de conversión (ver modulos/concurrencia.py) — hoy, con el
            # permiso exclusivo ya tomado arriba, sólo un soffice corre a la
            # vez de cualquier forma.
            self.conversor = ConversorRTF(perfil_dir=self.carpeta_temp / 'lo_perfil')

            # Convertir archivos descargados manteniendo metadata
            # descargar_todo_por_paginas() retorna {path, tipo, movimiento, url}
            total_a_convertir = len(archivos_descargados)
            self._emitir(fase='conversion', actual=0, total=total_a_convertir, total_exacto=True)

            # Partición: los que ya son PDF (la mayoría en un expediente
            # típico) son casi gratis — van uno por uno por el camino de
            # siempre, sin tocar soffice. Los RTF reales van agrupados en
            # lotes de LOTE_CONVERSION a UNA sola invocación de soffice cada
            # uno, en vez de pagar ~1.5-3s de arranque en frío + 1s de sleep
            # POR ARCHIVO (ver convertir_lote() en modulos/conversion.py).
            archivos_convertidos = []
            pendientes_rtf = []  # [(arch, ruta_original)]
            actual = 0

            for arch in archivos_descargados:
                ruta_original = arch['path']
                if parece_pdf(ruta_original):
                    pdf_convertido = self.conversor.convertir_rtf_a_pdf(ruta_original)
                    actual += 1
                    if pdf_convertido:
                        arch['path'] = pdf_convertido
                        archivos_convertidos.append(arch)
                    if actual % PROGRESO_CADA_N_ARCHIVOS == 0:
                        self._emitir(fase='conversion', actual=actual, total=total_a_convertir, total_exacto=True)
                else:
                    pendientes_rtf.append((arch, ruta_original))

            for inicio in range(0, len(pendientes_rtf), LOTE_CONVERSION):
                grupo = pendientes_rtf[inicio:inicio + LOTE_CONVERSION]
                resultados_lote = self.conversor.convertir_lote(
                    [ruta for _, ruta in grupo], self.carpeta_temp
                )
                for arch, ruta_original in grupo:
                    actual += 1
                    pdf_convertido = resultados_lote.get(Path(ruta_original))
                    if pdf_convertido:
                        arch['path'] = pdf_convertido
                        archivos_convertidos.append(arch)
                # Se emite siempre al cerrar cada lote (no gateado por
                # PROGRESO_CADA_N_ARCHIVOS): un lote entero puede ser más
                # grande que la cadencia normal, y sin esto el contador
                # podría saltar de golpe LOTE_CONVERSION números de una vez.
                self._emitir(fase='conversion', actual=actual, total=total_a_convertir, total_exacto=True)

            logger.info(f"[OK] Conversión completada: {len(archivos_convertidos)} archivos")

            # Liberar la memoria del proceso soffice.bin residente (si hubo al
            # menos un RTF) ANTES de que PyPDF2 tenga que cargar y combinar todos
            # los PDFs — ambos compiten por la misma RAM del proceso Python.
            # Seguro incluso con otros jobs corriendo en simultáneo: sólo se
            # llega acá con el permiso de conversión tomado (control no nulo
            # devolvió arriba si no lo consiguió), así que ningún otro job
            # puede tener su propio soffice en vuelo en este momento.
            if control.permite_matar_soffice():
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
            # carpeta_temp propia (no config.OUTPUT_DIR): los PDFs intermedios
            # de lote (_lote_N.pdf) quedan scoped a este job, así dos
            # unificaciones concurrentes no pisan los archivos intermedios
            # de la otra.
            #
            # En modo incremental, el resultado de este paso es sólo el
            # delta (los archivos nuevos entre sí) — todavía falta combinarlo
            # con el PDF anterior más abajo. Ese delta también se manda a
            # carpeta_temp (no a OUTPUT_DIR): si quedara en OUTPUT_DIR nadie
            # lo borraría nunca (sólo se usa el PDF combinado final, copiado
            # aparte), acumulando un archivo huérfano por cada actualización.
            carpeta_salida_unificacion = self.carpeta_temp if modo_actualizacion else config.OUTPUT_DIR
            self.unificador = UnificadorPDF(self.carpeta_temp, carpeta_salida_unificacion)

            # Pasar archivos con metadata al unificador
            pdf_final = self.unificador.unificar(
                numero_expediente, archivos_convertidos, on_progreso=self._on_progreso,
                sufijo_salida=self._token
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

            if modo_actualizacion:
                # `pdf_final` acá es sólo el delta (los archivos nuevos,
                # unificados entre sí) — falta combinarlo con el PDF de la
                # descarga anterior. Corre todavía dentro de la ventana del
                # permiso de conversión (se libera recién en el finally),
                # mismo criterio que la unificación de una descarga completa.
                pdf_previo_str = modo_actualizacion.get('pdf_previo_local')
                if not pdf_previo_str or not Path(pdf_previo_str).exists():
                    # Ojo: Path('') resuelve a Path('.'), el directorio de
                    # trabajo actual, que SIEMPRE existe — chequear la
                    # cadena antes de construir el Path, no después.
                    return ResultadoPipeline(
                        exito=False,
                        error="No se encontró el PDF de la descarga anterior para combinar",
                        tipo_error="pdf_previo_faltante",
                        expediente=expediente,
                    )
                pdf_previo = Path(pdf_previo_str)
                ruta_combinada = self.carpeta_temp / f"combinado_{self._token}.pdf"
                try:
                    pdf_combinado = anexar_con_qpdf(pdf_previo, pdf_final, ruta_combinada)
                except ErrorUnificacion as e:
                    logger.error(f"[INCREMENTAL] No se pudo combinar con el PDF anterior: {e}")
                    return ResultadoPipeline(
                        exito=False,
                        error=str(e),
                        tipo_error="unification_failed",
                        expediente=expediente,
                    )
                # carpeta_temp se borra entera en el finally: copiar el
                # resultado a OUTPUT_DIR, mismo lugar donde unificar() deja
                # el PDF de una descarga completa.
                numero_sanitizado = numero_expediente.replace('/', '_').replace('\\', '_').replace(':', '_')
                destino_final = Path(config.OUTPUT_DIR) / f"Expediente_{numero_sanitizado}_ACTUALIZADO_{self._token}.pdf"
                shutil.copy2(pdf_combinado, destino_final)
                pdf_final = destino_final
                logger.info(f"[INCREMENTAL] PDF combinado: {pdf_final}")

            # PASO 6 (OPCIONAL): COMPRESIÓN
            # Solo comprime si COMPRIMIR_PDF=true en .env (desactivado por defecto)
            pdf_final = comprimir_pdf(pdf_final)

            # SUCCESS
            return ResultadoPipeline(
                exito=True,
                expediente=expediente,
                pdf_final=pdf_final,
                archivos_descargados=len(archivos_descargados),
                total_filas=(total_filas_actual if modo_actualizacion else estrategia.total_filas_final),
                huellas=estrategia.huellas_iniciales,
                parcial=es_parcial,
            )

        except Exception as e:
            if "SESION_MV_EXPIRADA" in str(e):
                logger.warning(f"[AUTH] Sesión de Mesa Virtual expirada durante el proceso: {e}")
                return ResultadoPipeline(
                    exito=False,
                    error="Tu sesión de Mesa Virtual expiró. Reconectá tu cuenta.",
                    tipo_error="auth_failed"
                )
            if modo_actualizacion and ("NO_SE_PUDO_ALINEAR" in str(e) or "NO_SE_PUDO_LEER_FILAS" in str(e)):
                logger.warning(f"[INCREMENTAL] No se pudo alinear con la descarga anterior: {e}")
                return ResultadoPipeline(
                    exito=False,
                    error="El expediente cambió de una forma que no se puede actualizar de manera incremental. Hacé una descarga completa.",
                    tipo_error="expediente_cambio",
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
            # Backstop: si el pipeline salió por una excepción DURANTE
            # descargar_todo_por_paginas (ej. SESION_MV_EXPIRADA), el re-sync
            # de más arriba nunca corrió — tomar el cliente vivo directo del
            # descargador si hubo reciclaje, para no dejarlo huérfano acá también.
            if self.descargador and self.descargador.cliente:
                self.cliente = self.descargador.cliente
            if self.cliente:
                try:
                    self.cliente.cerrar()
                except Exception:
                    pass

            # 1b. Backstop: soltar cualquier permiso de concurrencia que haya
            #     quedado tomado, sea cual sea el camino de salida (éxito,
            #     error temprano, excepción). Idempotente: si ya se soltó
            #     arriba (liberar_navegador tras cerrar Chrome), esto no hace
            #     nada de más.
            control.liberar_todo()

            # 2. Borrar carpeta temporal completa (RTFs, PDFs individuales, lotes)
            #    El PDF final ya está en OUTPUT_DIR, así que temp/ es descartable
            if self.carpeta_temp and self.carpeta_temp.exists():
                try:
                    shutil.rmtree(self.carpeta_temp, ignore_errors=True)
                    logger.info(f"[CLEANUP] Carpeta temporal eliminada: {self.carpeta_temp}")
                except Exception as e:
                    logger.warning(f"[CLEANUP] No se pudo eliminar temp: {e}")
