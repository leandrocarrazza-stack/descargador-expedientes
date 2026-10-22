// Test de guardarPendiente/leerPendiente (templates/descargar_expediente.html). Uso: node tests/test_frontend_pendiente.js
const fs = require('fs');
const html = fs.readFileSync(require('path').join(__dirname, '..', 'templates', 'descargar_expediente.html'), 'utf8');
const pick = (name) => {
  const i = html.indexOf('function ' + name);
  let d = 0, j = html.indexOf('{', i);
  for (let k = j; k < html.length; k++) { if (html[k]==='{') d++; if (html[k]==='}') { d--; if (!d) return html.slice(i, k+1); } }
};
const store = {};
global.localStorage = { getItem: k => (k in store ? store[k] : null), setItem: (k,v)=>{store[k]=String(v)}, removeItem: k=>{delete store[k]} };
const el = { desdeFecha: {value:''}, soloDesdeFecha: {checked:false}, numero_expediente:{value:''}, bloqueDesdeFecha:{style:{}} };
global.document = { getElementById: id => el[id] };
eval(pick('guardarPendiente') + pick('leerPendiente') + ';global.g=guardarPendiente;global.l=leerPendiente;');
let ok = true; const check=(n,c)=>{console.log((c?'  OK  ':' FALLA ')+n); if(!c) ok=false;};

el.soloDesdeFecha.checked = true; el.desdeFecha.value = '2026-09-01';
g('14246'); let p = l();
check('guarda y restaura número + fecha', p.numero==='14246' && p.desde_fecha==='2026-09-01');
check('lo leído se borra (no reanuda dos veces)', l()===null);

el.soloDesdeFecha.checked = false; g('14246'); p = l();
check('sin casillero marcado: no guarda fecha', p.numero==='14246' && !p.desde_fecha);

el.soloDesdeFecha.checked = true; el.desdeFecha.value = ''; g('14246'); p = l();
check('casillero marcado pero fecha vacía: no guarda fecha', !p.desde_fecha);

store['expediente_pendiente'] = '5790-6'; p = l();
check('formato viejo (texto plano) sigue funcionando', p.numero==='5790-6' && !p.desde_fecha);

g(''); check('número vacío: no guarda nada', l()===null);
process.exit(ok?0:1);
