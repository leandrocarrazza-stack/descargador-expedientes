"""
Fixtures sinteticas que imitan la forma del HTML de SIU-Toba.

OJO: son inventadas. Sirven para testear la LOGICA del parser (deteccion de
tabla por encabezados, desenvoltura del JS, normalizacion de fechas,
paginacion, captcha) sin depender de la red.

Las fixtures REALES salen de la Fase 0 y van en
`data/jurisprudencia/descubrimiento/*.html`. Cuando esten, los tests de
tests/stjer/test_parser_real.py las levantan automaticamente y son esos los
que mandan.
"""

LISTADO_HTML = """
<html><body>
<div id="cabecera">Jurisprudencia - Búsqueda Pública</div>
<table id="ei_2001_datos" class="tabla-datos">
  <tr>
    <th>Jurisdicción</th><th>Organismo</th><th>Fallo</th>
    <th>N° Expediente</th><th>Carátula</th><th>Sumario</th><th>Fuero</th>
  </tr>
  <tr onclick="toba.rango_tabla('ei_2001', 'seleccion', '88123')">
    <td>Gualeguaychú</td>
    <td>Cámara de Apelaciones Sala I</td>
    <td>14/03/2019</td>
    <td>Nro. 12.345/2019</td>
    <td>PEREZ c/ MUNICIPALIDAD s/ DAÑOS Y PERJUICIOS</td>
    <td>La responsabilidad del Estado por caída de árbol es objetiva...</td>
    <td>Civil y Comercial</td>
  </tr>
  <tr onclick="toba.rango_tabla('ei_2001', 'seleccion', '88124')">
    <td>Paraná</td>
    <td>Sala Penal STJ</td>
    <td>02/07/2021</td>
    <td>9876/2021</td>
    <td>FISCALIA c/ GOMEZ s/ ROBO</td>
    <td>La tentativa exige comienzo de ejecución...</td>
    <td>Penal</td>
  </tr>
  <tr><td colspan="7">&nbsp;</td></tr>
</table>
<div class="pie">Se encontraron 47 registros - Página 1 de 2</div>
</body></html>
"""

# Toba devolviendo el mismo HTML dentro de un literal de JavaScript.
LISTADO_JS = (
    "toba.actualizar_celda('ei_2001', '"
    + LISTADO_HTML.replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n")
    + "');\ntoba.set_ah('st672374e1c84563.72627224');"
)

DETALLE_HTML = """
<html><body>
<table class="ficha">
  <tr><td>CARATULA:</td><td>PEREZ c/ MUNICIPALIDAD s/ DAÑOS Y PERJUICIOS</td></tr>
  <tr><td>EXPEDIENTE:</td><td>Nro. 12.345/2019</td></tr>
  <tr><td>FECHA SENTENCIA:</td><td>14/03/2019</td></tr>
  <tr><td>JURISDICCION:</td><td>Gualeguaychú</td></tr>
  <tr><td>ORGANISMO:</td><td>Cámara de Apelaciones Sala I</td></tr>
  <tr><td>FUERO:</td><td>Civil y Comercial</td></tr>
</table>

<div class="sumario">
  La responsabilidad del Estado por la caída de un árbol de la vía pública es
  objetiva y no se exime probando la mera diligencia en el mantenimiento.
</div>
<div class="sumario">
  El daño moral en estos supuestos se presume in re ipsa cuando se acredita
  la lesión a la integridad física.
</div>

<table class="voces">
  <tr><th>Materia</th><th>Voz Principal</th><th>Voz</th></tr>
  <tr><td>DERECHO CIVIL</td><td>RESPONSABILIDAD CIVIL</td><td>RESPONSABILIDAD OBJETIVA</td></tr>
  <tr><td>DERECHO CIVIL</td><td>DAÑOS</td><td>DAÑO MORAL</td></tr>
</table>

<table class="votos">
  <tr><th>Juez/a</th><th>Tipo de voto</th></tr>
  <tr><td>Dra. González</td><td>primer voto</td></tr>
  <tr><td>Dr. Martínez</td><td>adhesión</td></tr>
</table>

<div class="fallo">
  FALLO: <a href="dossier/bCARATULA_b__PEREZ__.PDF">link al PDF</a>
</div>
</body></html>
"""

CAPTCHA_HTML = """
<html><body>
<form id="form_captcha">
  <label>Verificación (*)</label>
  <img src="captcha.php?id=847263" width="120" height="40" alt="captcha">
  <input type="text" name="codigo" />
  <input type="submit" value="Aceptar" />
</form>
</body></html>
"""

TESAURO_UL_HTML = """
<html><body>
<ul id="arbol_tesauro">
  <li id="mat_1">DERECHO CIVIL
    <ul>
      <li id="vp_11">RESPONSABILIDAD CIVIL
        <ul>
          <li id="v_111">RESPONSABILIDAD OBJETIVA</li>
          <li id="v_112">RESPONSABILIDAD DEL ESTADO</li>
        </ul>
      </li>
      <li id="vp_12">DAÑOS
        <ul><li id="v_121">DAÑO MORAL</li></ul>
      </li>
    </ul>
  </li>
  <li id="mat_2">DERECHO PENAL
    <ul><li id="vp_21">TENTATIVA</li></ul>
  </li>
</ul>
</body></html>
"""

TESAURO_SELECT_HTML = """
<html><body>
<select name="materia">
  <option value="">-- Seleccione --</option>
  <option value="1">DERECHO CIVIL</option>
  <option value="2">DERECHO PENAL</option>
  <option value="3">DERECHOS HUMANOS</option>
</select>
</body></html>
"""
