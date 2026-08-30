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
<div id="panel_tesauro">
  <select name="materia">
    <option value="">-- Seleccione --</option>
    <option value="1">DERECHO CIVIL</option>
    <option value="2">DERECHO PENAL</option>
    <option value="3">DERECHOS HUMANOS</option>
  </select>
</div>
</body></html>
"""

# Caso real observado en produccion: el pedido de "arbol_tesauro" no llego al
# panel real y el sitio devolvio la pagina de busqueda comun, cuyos <option>
# de Fuero/Agregar Filtro/operadores NO estan dentro de ningun contenedor de
# tesauro identificable.
# Paginacion real de Toba: el numero de pagina esta en un <input>, no como
# texto plano. La regex "pagina 1 de 84" no hace match; hay que leer el DOM.
_TABLA_RESULTADO = """
<table>
  <tr>
    <th>Jurisdicción</th><th>Organismo</th><th>Fallo</th>
    <th>Nº Expediente</th><th>Carátula</th><th>Sumario</th><th>Fuero</th>
  </tr>
  <tr onclick="js.ir('123')">
    <td>Paraná</td><td>STJ</td><td>15/12/2023</td>
    <td>1234</td><td>GARCIA c/ ESTADO s/ DAÑOS</td>
    <td>El daño moral...</td><td>Civil</td>
  </tr>
</table>"""

LISTADO_CON_PAGER_TOBA_HTML = f"""<html><body>
<nav aria-label="pagination">
  <ul class="pager">
    <li class="disabled"><a href="#"><span>←</span> Anterior</a></li>
    Página
    <input class="form-control input-pager"
           name="cuadro_11000967_cuadro__pagina_actual"
           type="text" size="3" value="1">
    de <strong>84</strong>
    <li><a href="#" onclick="js_cuadro.set_evento(new evento_ei('cambiar_pagina','','','2'));">
      Siguiente <span>→</span>
    </a></li>
  </ul>
</nav>
Se encontraron 416 registros
{_TABLA_RESULTADO}
</body></html>"""

LISTADO_ULTIMA_PAGINA_TOBA_HTML = f"""<html><body>
<nav aria-label="pagination">
  <ul class="pager">
    <li><a href="#"><span>←</span> Anterior</a></li>
    Página
    <input class="form-control input-pager"
           name="cuadro_11000967_cuadro__pagina_actual"
           type="text" size="3" value="84">
    de <strong>84</strong>
    <li class="disabled"><a href="#">Siguiente <span>→</span></a></li>
  </ul>
</nav>
Se encontraron 416 registros
{_TABLA_RESULTADO}
</body></html>"""

PAGINA_BUSQUEDA_SIN_TESAURO_HTML = """
<html><body>
<select name="fuero">
  <option value="">-- Seleccione --</option>
  <option value="1">Amparos del STJER</option>
  <option value="2">Contencioso Administrativo</option>
  <option value="3">Fuero Civil y Comercial</option>
  <option value="4">Fuero Laboral</option>
  <option value="5">Fuero Penal</option>
</select>
<select name="agregar_filtro">
  <option value="">-- Seleccione --</option>
  <option value="1">Carátula</option>
  <option value="2">Fecha del Fallo</option>
  <option value="3">Juez/a</option>
  <option value="4">Nro. Expediente</option>
  <option value="5">Organismos</option>
  <option value="6">Sumario</option>
  <option value="7">Tipo de Voto</option>
</select>
<select name="operador">
  <option value="">-- Seleccione --</option>
  <option value="1">contiene</option>
  <option value="2">es igual a</option>
  <option value="3">comienza con</option>
</select>
</body></html>
"""
