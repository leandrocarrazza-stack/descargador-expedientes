/* Descargar Expediente - Lógica Principal */

// Etapas con tiempos estimados para la animación
const ETAPAS = [
    { idx: 0, porcentaje: 10, segundos: 2  },
    { idx: 1, porcentaje: 25, segundos: 12 },
    { idx: 2, porcentaje: 75, segundos: 30 },
    { idx: 3, porcentaje: 95, segundos: 80 },
];

let timers = [];
let numeroActual = '';

// ─── MOSTRAR/OCULTAR PANELES ───────────────────────────────────────────
function mostrarSolo(idPanel) {
    ['panel-form', 'panel-progreso', 'panel-seleccion', 'panel-exito', 'panel-error']
        .forEach(id => document.getElementById(id).style.display = 'none');
    document.getElementById(idPanel).style.display = 'block';
}

// ─── ANIMACIÓN DE PROGRESO ─────────────────────────────────────────────
function iniciarAnimacion() {
    mostrarSolo('panel-progreso');
    setBarra(0, 'Iniciando...');

    let delay = 0;
    ETAPAS.forEach((etapa) => {
        const t = setTimeout(() => activarEtapa(etapa.idx, etapa.porcentaje), delay * 1000);
        timers.push(t);
        delay += etapa.segundos;
    });
}

function activarEtapa(idx, pct) {
    // Marcar anteriores como completadas
    for (let i = 0; i < idx; i++) marcarEtapaCompleta(i);

    // Activar la actual
    const badge = document.getElementById(`badge-${idx}`);
    const texto = document.getElementById(`texto-${idx}`);
    badge.className = 'etapa-badge activa';
    texto.className = 'etapa-texto activa';

    const textos = [
        'Autenticando en Mesa Virtual...',
        'Buscando expediente...',
        'Descargando archivos...',
        'Convirtiendo y unificando PDF...'
    ];
    texto.textContent = textos[idx];
    setBarra(pct, textos[idx]);
}

function marcarEtapaCompleta(idx) {
    document.getElementById(`badge-${idx}`).className = 'etapa-badge completa';
    document.getElementById(`badge-${idx}`).textContent = '✓';
    document.getElementById(`texto-${idx}`).className = 'etapa-texto completa';
}

function setBarra(pct, label) {
    document.getElementById('barra-fill').style.width = pct + '%';
    document.getElementById('porcentaje-label').textContent = pct + '%';
    document.getElementById('etapa-label').textContent = label;
}

function detenerAnimacion() {
    timers.forEach(t => clearTimeout(t));
    timers = [];
}

function animarExito() {
    detenerAnimacion();
    ETAPAS.forEach(e => marcarEtapaCompleta(e.idx));
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
    // Llamado desde el panel de selección múltiple
    mostrarSolo('panel-progreso');
    iniciarAnimacion();
    await enviarDescarga(numeroActual, indice);
}

async function enviarDescarga(numero, indice) {
    iniciarAnimacion();

    const body = { numero_expediente: numero };
    if (indice !== null) body.indice_expediente = indice;

    try {
        // 1. Iniciar el job en el servidor (respuesta inmediata con job_id)
        const resp = await fetch('/descargas/expediente', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });

        const data = await resp.json();

        // Errores de validación inmediatos (sin créditos, sin sesión MV, etc.)
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

        // 2. Long-polling: esperar a que el job termine (el servidor retiene el request)
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
// (el servidor lo despierta apenas termina el pipeline, máx 5 minutos)
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
            // Reintentar con long-polling nuevamente
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
    // Reset barra
    document.getElementById('barra-fill').style.width = '0%';
    document.getElementById('barra-fill').classList.remove('error-barra');
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
