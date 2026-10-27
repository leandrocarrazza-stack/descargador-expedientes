# Foja — Ficha técnica para redactar Términos y Condiciones y Política de Privacidad

**Para qué sirve este documento.** Describe qué hace Foja *en la práctica*, según el código del repositorio (`descargador-expedientes`), para que quien redacte los Términos y la Política de Privacidad lo haga sobre hechos verificados y no sobre suposiciones. Fecha de elaboración: 27/10/2026. Los datos de la persona responsable **no** figuran acá: se cargan como variables de entorno en el hosting (`RESPONSABLE_NOMBRE`, `RESPONSABLE_CUIT`, `RESPONSABLE_DOMICILIO`, `RESPONSABLE_EMAIL`).

Lo que no pude verificar en el código está marcado **[A CONFIRMAR]**.

---

## 1. Qué es Foja

- Aplicación web (Flask) en `foja.com.ar`, dirigida a abogados de Entre Ríos.
- **Descarga y unifica el expediente**: con las credenciales propias del usuario en la Mesa Virtual del STJER, un navegador automatizado (Selenium/Chrome) entra a Mesa Virtual, recorre los movimientos del expediente, descarga los escritos y los une en un único PDF cronológico.
- **Actualización incremental**: para un expediente ya descargado, baja solo los movimientos nuevos y los suma al PDF anterior.
- **Contador de plazos**: calculadora de plazos procesales con un calendario judicial que se sincroniza desde el sitio oficial del STJER.
- **Aviso por email** (opcional) cuando termina una descarga, con enlace al PDF.
- **Foja no es** un servicio del Poder Judicial ni del STJER, ni tiene vínculo institucional con ellos. **[A CONFIRMAR]** si existe alguna autorización o convenio; el código no indica ninguno.

## 2. Cómo accede Foja a Mesa Virtual (punto de mayor sensibilidad)

- El usuario inicia sesión en Mesa Virtual *a través de Foja* (`modulos/auth_mv.py`). Soporta doble factor (2FA).
- Foja **no guarda la contraseña de Mesa Virtual** en la base de datos. Guarda las **cookies de la sesión** de Mesa Virtual (`SesionUsuarioMV.cookies_json`) y el nombre de usuario de Mesa Virtual (`mv_usuario`), con las cookies **cifradas con Fernet** (clave `ENCRYPTION_KEY`, exigida en producción).
- Con esa sesión, Foja actúa **en nombre del usuario** y solo ve los expedientes a los que ese usuario ya tiene acceso en Mesa Virtual.
- Hay una única sesión guardada por usuario (restricción de unicidad por `user_id`). La sesión puede vencer: el usuario debe "reconectar".
- **[A CONFIRMAR]** Si los términos de uso de Mesa Virtual permiten o prohíben la automatización de accesos. Es el principal punto de riesgo contractual frente al STJER y conviene tratarlo expresamente en los Términos.

## 3. Datos personales que trata Foja

| Dato | Dónde se guarda | Para qué | Notas |
|---|---|---|---|
| Email, nombre | Tabla `users` | Cuenta, avisos, soporte | El email es único |
| Contraseña de Foja | `users.password_hash` | Acceso | Solo hash (PBKDF2-SHA256), nunca en claro |
| Plan, créditos, uso mensual | `users` | Funcionamiento del servicio | |
| Preferencias: aviso por email, promociones | `users.notificar_email`, `users.recibir_promociones` | Respetar elecciones | Promociones: `NULL` o `True` = recibe; `False` = se dio de baja (ver `baja_promociones.html`) |
| Aceptación de términos | `users.terminos_version`, `terminos_aceptados_en`, `terminos_aceptados_ip` | Constancia de consentimiento | IP tomada de `X-Forwarded-For`; sirve de constancia, no es infalsificable |
| Sesión de Mesa Virtual | `sesiones_usuario_mv` | Descargar en nombre del usuario | Cifrada (ver punto 2) |
| Expedientes descargados: número, carátula, tribunal, fechas, estado, huellas de movimientos, enlace en Mesa Virtual | `ExpedienteDescargado` | Historial, descargas incrementales | **La carátula y el contenido del expediente pueden contener datos de terceros (partes, menores, datos sensibles)** |
| **PDF del expediente** | Storage persistente: Cloudflare R2 si está configurado, o disco local como alternativa (`modulos/storage.py`) | Que el usuario pueda volver a bajarlo | Se conserva **180 días desde el último acceso** (`RETENCION_PDF_DIAS`) y luego se borra. Además hay un caché local en `output/` con TTL de 24 h (`PDF_TTL_HOURS`) |
| Compras de créditos | `CompraCreditos` | Acreditar créditos, historial | Procesa Mercado Pago. **Foja no almacena datos de tarjeta**; guarda identificadores de pago y monto |
| Mensajes de contacto | `MensajeContacto` | Soporte | Nombre, email, asunto y mensaje |
| Tokens de recuperación de contraseña | `tokens_reset_password` | Recuperar acceso | Con vencimiento y un solo uso |
| Cookie de sesión de Foja | Navegador del usuario | Mantener la sesión | `HttpOnly`, `SameSite=Lax`, `Secure` en producción, 8 horas de duración |

**Importante para la Política de Privacidad:**
- Los PDF pueden contener datos personales de terceros y datos sensibles (art. 2 Ley 25.326). El usuario, como abogado, ya tiene deber de confidencialidad sobre ellos; los Términos deberían asignar al usuario la responsabilidad por el uso lícito de lo que descarga, y a Foja el deber de resguardarlo.
- No hay evidencia en el código de **seguimiento publicitario** (analytics, píxeles) ni de **cookies de terceros**. **[A CONFIRMAR]** revisando `templates/base.html` y los scripts externos (se carga Bootstrap desde la CDN jsDelivr, lo que implica que ese proveedor recibe la IP del visitante).
- Base de datos: SQLite o PostgreSQL según la configuración; en producción, **[A CONFIRMAR]** proveedor y ubicación de los servidores (el despliegue se describe para Render y se evalúa Cloud Run; esto implica **transferencia internacional de datos**, relevante para el art. 12 Ley 25.326).

## 4. Terceros que intervienen (posibles cesiones o encargados de tratamiento)

| Tercero | Rol |
|---|---|
| Mesa Virtual del STJER | Fuente de los expedientes; sitio al que Foja accede con la sesión del usuario |
| Mercado Pago | Procesa los pagos (API de Orders y webhook) |
| Cloudflare R2 | Almacena los PDF |
| Proveedor de hosting (Render u otro) **[A CONFIRMAR]** | Ejecuta la aplicación y aloja la base |
| Proveedor SMTP / Gmail **[A CONFIRMAR]** | Envía los emails (avisos, recuperación de contraseña, promociones, respuesta a contacto) |
| jsDelivr (CDN) | Entrega Bootstrap y librerías de estilo a los visitantes |

## 5. Créditos, precios y pagos (hechos del código)

- **Precios (ARS):** Individual: 1 descarga, $3.000. Estudio: 10 descargas, $24.000. Estudio Plus: 30 descargas, $63.000 (clave interna `matricula`).
- **Prueba gratuita:** al registrarse, la cuenta recibe **2 créditos** de prueba. El alta es gratuita.
- **Cuándo se descuenta un crédito:** **solo cuando la descarga termina con éxito y el PDF queda guardado** (`rutas/descargas.py`, `registrar_uso_credito`). Una descarga que falla **no consume crédito**; no hay un "reintegro" porque nunca se cobró. Una descarga **parcial** (con `desde_fecha`) **sí consume un crédito**. Una actualización incremental también consume uno.
- Los administradores no consumen créditos.
- Los créditos los acredita el webhook de pago al confirmarse (`rutas/pagos.py`) y también puede otorgarlos manualmente el administrador.
- **[A CONFIRMAR]** Si los créditos tienen vencimiento (el código no define uno; el modelo solo lleva un contador mensual de uso).
- **[A CONFIRMAR]** Facturación: el código no emite facturas; se sugiere aclarar en los Términos cómo y cuándo se factura como monotributista.
- Hay un contacto de soporte para "Reposición de créditos" (formulario `/contacto/`), que hoy se resuelve **a mano**.

## 6. Qué puede salir mal (insumo para la limitación de responsabilidad)

- Mesa Virtual cambia su estructura o se cae, y las descargas fallan o quedan **incompletas**.
- El PDF unificado puede **omitir archivos corruptos** (la unión con `pypdf` los saltea y sigue). **[A CONFIRMAR]** si el usuario es informado de qué archivos se omitieron: es relevante, porque un PDF incompleto presentado como "completo" es un riesgo profesional para el usuario.
- La sesión de Mesa Virtual puede vencer a mitad de una descarga.
- La calculadora de plazos depende del calendario sincronizado desde el sitio del STJER: puede tener errores o demoras. Por eso su resultado debería declararse **orientativo y no sustitutivo del cómputo del profesional**.
- Dependencia de terceros (Mercado Pago, R2, hosting): caídas fuera del control de Foja.
- Una auditoría de seguridad del 25/10/2026 (`AUDITORIA_SEGURIDAD_2026-10-25.md`) mantiene observaciones sobre dependencias y la política de seguridad de contenido (`unsafe-inline`). No se transcribe acá, pero conviene que la Política de Privacidad no prometa "seguridad absoluta" sino medidas razonables.

## 7. Esquema de cláusulas propuesto para los Términos

1. Identidad y datos del responsable; domicilio especial para notificaciones; carácter de monotributista.
2. Qué es Foja y qué no es: herramienta auxiliar; no es servicio oficial; **no es asesoramiento ni patrocinio**; el usuario verifica el expediente, la completitud del PDF y **los plazos**.
3. Uso con credenciales propias de Mesa Virtual: declaración del usuario sobre su legitimación y facultades; prohibición de usar cuentas ajenas; secreto profesional y datos de terceros a cargo del usuario; cumplimiento por el usuario de las condiciones de Mesa Virtual.
4. Disponibilidad y dependencia de terceros; sin garantía de continuidad ni de resultado.
5. Créditos y pagos: cuándo se consume un crédito (ver punto 5), precios, medios de pago, facturación.
6. Devoluciones (política decidida por el titular: sin devoluciones salvo falla del servicio imputable a Foja). **Ver la salvedad sobre relación de consumo en el punto 8.**
7. Conductas prohibidas, suspensión y baja.
8. Limitación de responsabilidad: servicio "tal cual está"; exclusión de daños indirectos y lucro cesante; **tope** de responsabilidad; excepción expresa de dolo y culpa grave.
9. Propiedad intelectual de la plataforma; licencia limitada del usuario para que Foja procese sus documentos.
10. Datos personales (remisión a la Política de Privacidad) y retención de PDF.
11. Modificaciones con aviso y nueva aceptación; ley aplicable; jurisdicción (tribunales ordinarios de Paraná, sin perjuicio de lo que la ley imponga si hay relación de consumo).
12. Reclamos y contacto.

## 8. Decisiones del titular y salvedades para la redacción

- **Domicilio público:** el del estudio/profesional (no el domicilio real).
- **Carácter fiscal:** persona humana, monotributista (responsabilidad patrimonial personal; ver cláusula de limitación).
- **Devoluciones:** "sin devoluciones salvo falla del servicio".
- **Botón de arrepentimiento:** el titular decidió **no implementarlo** porque el registro es gratuito. **Salvedad a evaluar en la redacción:** la gratuidad del alta no alcanza a la **compra de créditos**, que sí es una contratación onerosa a distancia. Si el abogado que compra fuera considerado consumidor, podrían aplicar el art. 34 de la Ley 24.240, el art. 1110 del CCyC y la Res. SCI 424/2020, y la cláusula "sin devoluciones" podría tenerse por no convenida (art. 37 Ley 24.240). Conviene que los Términos funden expresamente el **carácter profesional** del servicio (insumo de la actividad del abogado).
- **Registro de la base de datos ante la AAIP** (arts. 21 y 22 Ley 25.326): **[A CONFIRMAR]** si corresponde y si ya se tramitó. La Política debe incluir la leyenda del art. 6 y de la AAIP como órgano de control.
- Las normas citadas surgen de fuentes secundarias consultadas el 27/10/2026; **verificar el texto vigente** en Infoleg o el Boletín Oficial antes de publicar.

## 9. Cómo integrar el texto final en el sitio

1. Pegar la redacción en `templates/legal_terminos.html` y `templates/legal_privacidad.html`, dentro del bloque `texto_legal`, en el lugar indicado.
2. Poner `TERMINOS_BORRADOR=0` en el hosting para quitar el cartel "Documento en preparación".
3. Actualizar `TERMINOS_VERSION` y `TERMINOS_FECHA` (variables de entorno). Cada alta nueva guarda la versión aceptada.
4. Cargar las variables `RESPONSABLE_*`.
