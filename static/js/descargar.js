/* Descargar Expediente - Lógica Principal */

// Etapas con tiempos estimados y techo de porcentaje
const ETAPAS = [
    { idx: 0, techo: 10, segundos: 3,  textos: ['Autenticando en Mesa Virtual...'] },
    { idx: 1, techo: 30, segundos: 10, textos: ['Buscando expediente...'] },
    { idx: 2, techo: 70, segundos: 45, textos: [
        'Descargando archivos del expediente...',
        'Procesando movimientos...',
        'Descargando documentos adjuntos...',
        'Verificando archivos descargados...',
    ]},
    { idx: 3, techo: 92, segundos: 60, textos: [
        'Convirtiendo y unificando PDF...',
        'Combinando todas las fojas...',
        'Generando el archivo final...',
    ]},
];

let timers = [];
let tickInterval = null;
let numeroActual = '';
let tiempoInicioAnimacion = 0;
let etapaActualIdx = 0;
let porcentajeActual = 0;

// ─── MOSTRAR/OCULTAR PANELES ───────────────────────────────────────────
function mostrarSolo(idPanel) {
    ['panel-form', 'panel-progreso', 'panel-seleccion', 'panel-exito', 'panel-error']
        .forEach(id => document.getElementById(id).style.display = 'none');
    document.getElementById(idPanel).style.display = 'block';
}

// ─── ANIMACIÓN DE PROGRESO CONTINUA ────────────────────────────────────
function iniciarAnimacion() {
    mostrarSolo('panel-progreso');
    porcentajeActual = 0;
    etapaActualIdx = 0;
    tiempoInicioAnimacion = Date.now();
    setBarra(0, 'Iniciando...');

    // Activar la primera etapa inmediatamente
    activarEtapa(0);

    // Iniciar tick continuo
    tickInterval = setInterval(tickAnimacion, 400);
}

function tickAnimacion() {
    const tiempoTranscurrido = (Date.now() - tiempoInicioAnimacion) / 1000;
    let nuevoIdx = 0;
    let pctEnEtapa = 0;

    // Determinar etapa actual y progreso dentro de ella
    let tiempoAcumulado = 0;
    for (let i = 0; i < ETAPAS.length; i++) {
        const etapa = ETAPAS[i];
        if (tiempoTranscurrido < tiempoAcumulado + etapa.segundos) {
            nuevoIdx = i;
            const tiempoEnEtapa = tiempoTranscurrido - tiempoAcumulado;
            const k = etapa.segundos / 3;
            pctEnEtapa = 1 - Math.exp(-tiempoEnEtapa / k);
            break;
        }
        tiempoAcumulado += etapa.segundos;
        // Si pasamos todas las etapas, quedarse en la última
        if (i === ETAPAS.length - 1) {
            nuevoIdx = i;
            pctEnEtapa = 0.98;
        }
    }

    // Cambiar etapa si es necesario
    if (nuevoIdx !== etapaActualIdx) {
        for (let i = 0; i < nuevoIdx; i++) {
            marcarEtapaCompleta(i);
        }
        activarEtapa(nuevoIdx);
        etapaActualIdx = nuevoIdx;
    }

    // Rotar textos dentro de la etapa
    const etapa = ETAPAS[nuevoIdx];
    const tiempoBase = ETAPAS.slice(0, nuevoIdx).reduce((s, e) => s + e.segundos, 0);
    const tiempoEnEtapa = tiempoTranscurrido - tiempoBase;
    const idxTexto = Math.floor((tiempoEnEtapa / (etapa.segundos / etapa.textos.length)) % etapa.textos.length);
    const texto = etapa.textos[Math.min(idxTexto, etapa.textos.length - 1)];

    // Calcular porcentaje con curva asintótica
    const pctDesde = nuevoIdx === 0 ? 0 : ETAPAS[nuevoIdx - 1].techo;
    const pctHasta = etapa.techo;
    const nuevoPct = Math.round(pctDesde + (pctHasta - pctDesde) * pctEnEtapa);

    // Garantizar que siempre avanza (mínimo 0.1% más)
    if (nuevoPct <= porcentajeActual) {
        porcentajeActual = Math.min(porcentajeActual + 0.1, pctHasta);
    } else {
        porcentajeActual = nuevoPct;
    }

    setBarra(Math.round(porcentajeActual), texto);
}

function activarEtapa(idx) {
    const badge = document.getElementById(`badge-${idx}`);
    const texto = document.getElementById(`texto-${idx}`);
    badge.className = 'etapa-badge activa';
    texto.className = 'etapa-texto activa';
    texto.textContent = ETAPAS[idx].textos[0];
}

function marcarEtapaCompleta(idx) {
    document.getElementById(`badge-${idx}`).className = 'etapa-badge completa';
    document.getElementById(`badge-${idx}`).textContent = '✓';
    document.getElementById(`texto-${idx}`).className = 'etapa-texto completa';
}

function setBarra(pct, label) {
    const barra = document.getElementById('barra-fill');
    barra.style.width = pct + '%';
    barra.setAttribute('aria-valuenow', pct);
    document.getElementById('porcentaje-label').textContent = pct + '%';
    document.getElementById('etapa-label').textContent = label;
}

function detenerAnimacion() {
    timers.forEach(t => clearTimeout(t));
    timers = [];
    if (tickInterval) {
        clearInterval(tickInterval);
        tickInterval = null;
    }
}

function animarExito() {
    detenerAnimacion();
    ETAPAS.forEach(e => marcarEtapaCompleta(e.idx));
    // Transición suave a 100%
    const barra = document.getElementById('barra-fill');
    barra.style.transition = 'width 0.6s ease-out';
    setBarra(100, '¡Completado!');
}

function animarError() {
    detenerAnimacion();
    document.getElementById('barra-fill').classList.add('error-barra');
    setBarra(100, 'Error');
    ETAPAS.forEach(e => {
        const badge = document.getElementById(`badge-${e.idx}`);
        if (!badge.classList.contains('completa')) {
            badge.className = 'etapa-badge error-badge';
            badge.textContent = '✗';
        }
    });
}

// ─── FLUJO PRINCIPAL ──────────────────────────────────────────────────
async function iniciarDescarga() {
    const numero = document.getElementById('numero_expediente').value.trim();
    if (!numero) {
        alert('Ingresá un número de expediente');
        return;
    }
    numeroActual = numero;
    await enviarDescarga(numero, null);
}

async function descargarConIndice(indice) {
    mostrarSolo('panel-progreso');
    iniciarAnimacion();
    await enviarDescarga(numeroActual, indice);
}

async function enviarDescarga(numero, indice) {
    iniciarAnimacion();

    const body = { numero_expediente: numero };
    if (indice !== null) body.indice_expediente = indice;

    try {
        const csrfToken = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
        const resp = await fetch('/descargas/expediente', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify(body)
        });

        const data = await resp.json();

        if (resp.status === 401 && data.tipo_error === 'sesion_mv_requerida') {
            detenerAnimacion();
            window.location.href = data.login_url || '/auth/mv-login?next=/descargas/expediente';
            return;
        }
        if (!resp.ok || !data.job_id) {
            animarError();
            setTimeout(() => {
                document.getElementById('error-detalle').textContent = data.mensaje || 'Error al iniciar la descarga';
                mostrarSolo('panel-error');
            }, 800);
            return;
        }

        await longPolling(data.job_id);

    } catch (err) {
        animarError();
        setTimeout(() => {
            document.getElementById('error-detalle').textContent = `Error de conexión: ${err.message}`;
            mostrarSolo('panel-error');
        }, 800);
    }
}

// Long-polling: un único request que espera en el servidor hasta que el job complete
async function longPolling(jobId) {
    try {
        console.log(`[LONG-POLL] Esperando resultado del job ${jobId.substring(0, 8)}`);

        const resp = await fetch(`/descargas/estado/${jobId}`);
        const data = await resp.json();

        console.log(`[LONG-POLL] Respuesta recibida:`, data);

        if (data.estado === 'completo') {
            animarExito();
            setTimeout(() => {
                document.getElementById('exito-creditos').textContent =
                    `Créditos restantes: ${data.creditos_restantes}`;
                document.getElementById('btn-descargar-pdf').href = data.pdf_url;
                mostrarSolo('panel-exito');
                window.location.href = data.pdf_url;
            }, 800);
            return;

        } else if (data.estado === 'multiples_opciones') {
            detenerAnimacion();
            mostrarOpciones(data.opciones);
            return;

        } else if (data.estado === 'error') {
            if (data.tipo_error === 'sesion_mv_requerida') {
                detenerAnimacion();
                window.location.href = data.login_url || '/auth/mv-login?next=/descargas/expediente';
                return;
            }
            animarError();
            setTimeout(() => {
                document.getElementById('error-detalle').textContent = data.mensaje || 'Error desconocido';
                mostrarSolo('panel-error');
            }, 800);
            return;

        } else if (data.estado === 'no_encontrado') {
            animarError();
            setTimeout(() => {
                document.getElementById('error-detalle').textContent = 'La sesión expiró. Intentá de nuevo.';
                mostrarSolo('panel-error');
            }, 800);
            return;

        } else if (data.estado === 'procesando') {
            // Timeout del server (5 minutos): el job sigue en progreso
            mostrarMensajeExtendido('Este expediente tiene muchos movimientos. Estamos terminando — no cierres la pestaña.');
            console.log('[LONG-POLL] Timeout del servidor, reintentando...');
            await longPolling(jobId);
            return;
        }

    } catch (err) {
        animarError();
        setTimeout(() => {
            document.getElementById('error-detalle').textContent = `Error de conexión: ${err.message}`;
            mostrarSolo('panel-error');
        }, 800);
    }
}

function mostrarMensajeExtendido(texto) {
    const el = document.getElementById('mensaje-extendido');
    if (el) {
        el.textContent = texto;
        el.style.display = 'block';
    }
}

// ─── PANEL DE SELECCIÓN MÚLTIPLE ──────────────────────────────────────
function mostrarOpciones(opciones) {
    const lista = document.getElementById('lista-opciones');
    lista.innerHTML = '';

    opciones.forEach(op => {
        const card = document.createElement('div');
        card.className = 'opcion-card';

        const caratula = document.createElement('div');
        caratula.className = 'opcion-caratula';
        caratula.textContent = op.caratula || 'Sin descripción';

        const meta = document.createElement('div');
        meta.className = 'opcion-meta';

        const numero = document.createElement('span');
        numero.textContent = '📋 ' + (op.numero || '');

        const tribunal = document.createElement('span');
        tribunal.textContent = '🏛️ ' + (op.tribunal || 'Tribunal no especificado');

        const btn = document.createElement('button');
        btn.className = 'btn-opcion';
        btn.textContent = '⬇ Descargar este expediente';
        const indice = parseInt(op.indice, 10);
        if (!isNaN(indice)) {
            btn.addEventListener('click', () => descargarConIndice(indice));
        }

        meta.appendChild(numero);
        meta.appendChild(tribunal);
        card.appendChild(caratula);
        card.appendChild(meta);
        card.appendChild(btn);
        lista.appendChild(card);
    });

    mostrarSolo('panel-seleccion');
}

// ─── REINICIAR ────────────────────────────────────────────────────────
function reiniciar() {
    detenerAnimacion();
    const barra = document.getElementById('barra-fill');
    barra.style.width = '0%';
    barra.classList.remove('error-barra');
    barra.setAttribute('aria-valuenow', 0);
    const msgExtendido = document.getElementById('mensaje-extendido');
    if (msgExtendido) msgExtendido.style.display = 'none';
    ETAPAS.forEach(e => {
        const badge = document.getElementById(`badge-${e.idx}`);
        badge.className = 'etapa-badge';
        badge.textContent = e.idx + 1;
        document.getElementById(`texto-${e.idx}`).className = 'etapa-texto';
    });
    mostrarSolo('panel-form');
}

// Enter en el input dispara la descarga
document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('numero_expediente').addEventListener('keydown', e => {
        if (e.key === 'Enter') iniciarDescarga();
    });
});
