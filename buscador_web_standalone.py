#!/usr/bin/env python3
"""
Buscador de jurisprudencia STJER - pagina web local, sin conectores
=====================================================================

Alternativa al servidor MCP para cuando el conector de Claude Desktop no
anda: una paginita que corre en tu propia compu (sin tocar Configuracion
de Claude Desktop, sin CAPTCHA, sin conectores) y habla directo con
ChatSTJER -el mismo motor que ya se probo en el chat web y en el servidor
MCP-.

Uso:
    python buscador_web_standalone.py

Despues abri http://localhost:5001 en el navegador. Ctrl+C en esta ventana
para pararlo.
"""

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT_DIR))

from flask import Flask, jsonify, render_template_string, request

app = Flask(__name__)

PAGINA = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Jurisprudencia STJER</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 800px; margin: 2rem auto; padding: 0 1rem; color: #222; }
  h1 { font-size: 1.4rem; }
  .caja { display: flex; gap: 0.5rem; margin-bottom: 1rem; flex-wrap: wrap; }
  input[type=text] { flex: 1; padding: 0.6rem; font-size: 1rem; min-width: 200px; }
  button { padding: 0.6rem 1rem; font-size: 1rem; cursor: pointer; }
  #resultados { margin-top: 1rem; }
  .fallo { border-left: 3px solid #b08d2b; background: #f7f4ee; padding: 0.8rem; margin-bottom: 0.8rem; }
  .fallo b { display: block; }
  .fallo small { color: #555; }
  .voces { font-size: 0.85rem; color: #444; margin-top: 0.3rem; }
  .respuesta { white-space: pre-wrap; margin-bottom: 1rem; }
  .error { color: #a11; }
  .cargando { color: #888; }
</style>
</head>
<body>
<h1>Buscador de jurisprudencia STJER</h1>
<p>Escribi tu consulta en lenguaje natural, o subi un escrito (PDF/DOCX).</p>

<div class="caja">
  <input type="text" id="consulta" placeholder="Ej: intereses aplicables a un convenio de pago" autocomplete="off">
  <button onclick="buscar()">Buscar</button>
  <input type="file" id="archivo" accept=".pdf,.docx" style="display:none" onchange="subirEscrito()">
  <button onclick="document.getElementById('archivo').click()">Adjuntar escrito</button>
</div>

<div id="resultados"></div>

<script>
const consultaInput = document.getElementById('consulta');
const resultadosDiv = document.getElementById('resultados');

consultaInput.addEventListener('keypress', e => {
  if (e.key === 'Enter') buscar();
});

function buscar() {
  const mensaje = consultaInput.value.trim();
  if (!mensaje) return;
  resultadosDiv.innerHTML = '<p class="cargando">Buscando...</p>';
  fetch('/buscar', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({mensaje}),
  }).then(r => r.json()).then(mostrar).catch(e => {
    resultadosDiv.innerHTML = '<p class="error">Error: ' + e.message + '</p>';
  });
}

function subirEscrito() {
  const archivo = document.getElementById('archivo').files[0];
  document.getElementById('archivo').value = '';
  if (!archivo) return;
  resultadosDiv.innerHTML = '<p class="cargando">Analizando ' + archivo.name + '...</p>';
  const formData = new FormData();
  formData.append('archivo', archivo);
  fetch('/escrito', {method: 'POST', body: formData})
    .then(r => r.json()).then(mostrar).catch(e => {
      resultadosDiv.innerHTML = '<p class="error">Error: ' + e.message + '</p>';
    });
}

function mostrar(data) {
  if (data.error) {
    resultadosDiv.innerHTML = '<p class="error">' + data.error + '</p>';
    return;
  }
  let html = '<div class="respuesta">' + (data.respuesta || '') + '</div>';
  for (const r of (data.resultados || [])) {
    const voces = (r.voces || []).join(' · ');
    html += `<div class="fallo">
      <b>${r.caratula || r.clave}</b>
      <small>${r.organismo || ''} ${r.fuero ? '· ' + r.fuero : ''} ${r.fecha ? '· ' + r.fecha : ''}</small>
      <div>${r.fragmento || ''}</div>
      ${voces ? `<div class="voces"><b>Voces:</b> ${voces}</div>` : ''}
      ${r.url_pdf ? `<div><a href="${r.url_pdf}" target="_blank" rel="noopener">Ver PDF</a></div>` : ''}
    </div>`;
  }
  resultadosDiv.innerHTML = html;
}
</script>
</body>
</html>
"""


def _abrir_chat():
    """Arma un ChatSTJER contra el corpus local, con o sin Claude segun haya API key."""
    import config
    from modulos.jurisprudencia.stjer import ajustes, corpus
    from modulos.jurisprudencia.stjer.chat import ChatSTJER

    con = corpus.abrir(ajustes.CORPUS_PATH, solo_lectura=True)
    cliente = None
    if config.ANTHROPIC_API_KEY:
        try:
            import anthropic

            cliente = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
        except ImportError:
            pass
    return ChatSTJER(con, cliente_anthropic=cliente), con


@app.route("/")
def index():
    return render_template_string(PAGINA)


@app.route("/buscar", methods=["POST"])
def buscar():
    mensaje = (request.get_json(silent=True) or {}).get("mensaje", "").strip()
    if not mensaje:
        return jsonify({"error": "Escribí una consulta"}), 400
    try:
        chat, con = _abrir_chat()
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 503
    try:
        return jsonify(chat.procesar_mensaje(mensaje))
    finally:
        con.close()


@app.route("/escrito", methods=["POST"])
def escrito():
    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        return jsonify({"error": "No se recibió ningún archivo"}), 400

    from modulos.jurisprudencia.stjer.extraccion_documento import (
        ErrorExtraccion,
        extraer_texto,
    )

    try:
        texto = extraer_texto(archivo.read(), archivo.filename)
    except ErrorExtraccion as e:
        return jsonify({"error": str(e)}), 400
    if not texto.strip():
        return jsonify({"error": "No se pudo extraer texto del archivo"}), 400

    try:
        chat, con = _abrir_chat()
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 503
    try:
        return jsonify(chat.procesar_documento(texto, archivo.filename))
    finally:
        con.close()


if __name__ == "__main__":
    print("Abrí http://localhost:5001 en tu navegador. Ctrl+C en esta ventana para parar.")
    app.run(port=5001, debug=False)
