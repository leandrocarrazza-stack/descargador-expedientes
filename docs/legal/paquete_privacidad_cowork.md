# Foja — Paquete para redactar la Política de Privacidad (Cowork)

Hechos verificados en el código del repositorio `descargador-expedientes` al 28/10/2026. Lo marcado **[CONFIRMAR]** no surge del código y debe completarlo el Titular antes de enviar este paquete.

## 1. Mensaje para pegar en Cowork

> Redactá la **Política de Privacidad de Foja** (foja.com.ar) para publicarla en `/privacidad`, en español jurídico claro, conforme a la Ley 25.326, su decreto reglamentario 1558/2001 y las disposiciones de la AAIP. Usá **solo** los hechos de la sección 2 y las decisiones de la sección 3: no agregues prácticas que no figuren. Mantené coherencia con los **Términos y Condiciones ya redactados**, que remiten a esta política (art. 17) y ya afirman: contraseña guardada como hash PBKDF2-SHA256; cookies de la sesión de Mesa Virtual guardadas cifradas; documentos PDF conservados durante [RETENCION_PDF_DIAS] días desde el último acceso; pagos por Mercado Pago sin almacenar datos de tarjeta; constancia de aceptación con versión, fecha, hora e IP; Foja como "prestador de servicios" respecto del contenido de los expedientes (art. 10 Ley 25.326). Formato de salida: artículos numerados y los mismos placeholders que los Términos (`[RESPONSABLE_NOMBRE]`, `[RESPONSABLE_CUIT]`, `[RESPONSABLE_DOMICILIO]`, `[RESPONSABLE_EMAIL]`, `[TERMINOS_VERSION]`, `[TERMINOS_FECHA]`, `[RETENCION_PDF_DIAS]`), sin notas internas dentro del texto publicable: lo pendiente debe ir en un mensaje aparte. Incluí: responsable del tratamiento; datos que se recopilan; finalidades; base del tratamiento y consentimiento; cesiones y proveedores; transferencias internacionales (art. 12); plazos de conservación; seguridad; derechos de acceso, rectificación, actualización y supresión (arts. 14 a 16) y cómo ejercerlos; comunicaciones promocionales y baja; cookies y tecnologías; menores; modificaciones; y la **leyenda obligatoria del art. 6 y de la AAIP** como órgano de control.

## 2. Hechos verificados en el código

**Quién trata los datos.** Persona humana, monotributista (datos del Titular por placeholders). Aplicación web Flask en foja.com.ar.

**Datos que se recopilan y dónde se guardan**

| Dato | Origen / momento | Finalidad |
|---|---|---|
| Email (único), nombre, hash de contraseña | Registro | Cuenta, avisos, soporte |
| Plan, saldo de créditos, uso mensual | Uso del servicio | Funcionamiento y cobro |
| Preferencias de aviso por email y de promociones | Cuenta del usuario | Respetar elecciones |
| Versión de términos aceptada, fecha y hora, IP | Registro | Constancia de consentimiento (la IP sale de `X-Forwarded-For`) |
| Sesión de Mesa Virtual: cookies **cifradas (Fernet)** y nombre de usuario de Mesa Virtual. **No se guarda la contraseña de Mesa Virtual.** | Cuando el usuario conecta su cuenta | Descargar expedientes en su nombre |
| Expedientes descargados: número, carátula, tribunal, fechas, estado, enlace en Mesa Virtual | Cada descarga | Historial y actualización incremental |
| **PDF unificado del expediente** (puede contener datos de terceros y datos sensibles) | Cada descarga | Que el usuario lo vuelva a bajar |
| Compras: identificador del pago de Mercado Pago, monto, plan, cantidad de créditos, estado | Compra de créditos | Acreditar créditos e historial. **No se guardan datos de tarjeta** |
| Mensajes de contacto: nombre, email, asunto, mensaje | Formulario `/contacto/` | Soporte (llegan por email al Titular) |
| Token de recuperación de contraseña | Pedido del usuario | Recuperar acceso (con vencimiento y un solo uso) |
| Registros técnicos (logs) que incluyen el email en eventos de registro, inicio y cierre de sesión | Uso del sitio | Seguridad y diagnóstico |

**Plazos reales de conservación:** el PDF y su ruta se eliminan a los **180 días del último acceso** (`RETENCION_PDF_DIAS`; limpieza automática cada hora). El caché local de PDF se borra a las 24 h (48 h en producción). El resto de los datos no tiene hoy plazo de borrado automático: los datos de cuenta permanecen mientras exista la cuenta.

**Cookies y tecnologías.** Solo la cookie de sesión de Foja (`HttpOnly`, `SameSite=Lax`, `Secure` en producción, 8 horas). **No hay analítica, publicidad ni píxeles de seguimiento.** Al cargar el sitio, el navegador pide recursos a terceros, que reciben la IP del visitante: **Google Fonts** (tipografías) y **jsDelivr** (Bootstrap y su librería de íconos).

**Proveedores que intervienen**

| Proveedor | Función | Dato |
|---|---|---|
| Render (web y PostgreSQL gestionado) | Aloja la aplicación y la base de datos | Todos los datos de la cuenta. Región: **[CONFIRMAR]** |
| Cloudflare R2 | Almacena los PDF | PDF de expedientes. Ubicación del bucket: **[CONFIRMAR]** |
| Mercado Pago | Procesa los pagos | Datos de pago (los maneja Mercado Pago) |
| Correo saliente por SMTP (por defecto `smtp.gmail.com`) | Avisos, recuperación de contraseña, promociones | Email y contenido del mensaje. Proveedor real en producción: **[CONFIRMAR]** |
| Google Fonts y jsDelivr | Recursos de estilo | IP del visitante |
| Mesa Virtual del STJER | Fuente de los expedientes | Sesión del usuario, que Foja usa por su cuenta y orden |

**Derechos del usuario hoy.** En "Mi cuenta" el usuario puede cambiar su **nombre**, su **contraseña** y sus **preferencias de aviso**. **No existe una función para eliminar la cuenta ni para exportar los datos:** esos pedidos se atienden hoy **manualmente**, por email al Titular. Existe baja de promociones por enlace.

**Comunicaciones promocionales.** El sistema envía promociones a **todos los usuarios que no se hayan dado de baja** (opt-out por defecto, sin casilla de consentimiento separada en el registro), con enlace de baja en cada mensaje.

**Funciones que no alcanzan a los usuarios.** El código incluye un módulo de jurisprudencia que usa una cuenta de Gmail con OAuth: según el código, pertenece al Titular y no a los usuarios. **[CONFIRMAR]** que no procesa datos de usuarios.

## 3. Decisiones y datos a completar por el Titular (antes de enviar)

1. **Región de Render y ubicación del bucket de R2.** Se ven en los paneles de cada servicio. Si los datos están fuera de Argentina, hay **transferencia internacional** (art. 12 Ley 25.326); la Política debería incluir el consentimiento expreso. Verificar la lista de países adecuados y las cláusulas modelo vigentes de la AAIP.
2. **Proveedor de correo en producción** (variable `MAIL_SERVER`).
3. **Promociones:** ¿mantener el envío por defecto con baja en cada mensaje, o pasar a consentimiento expreso? Hoy no hay casilla específica.
4. **Cómo se atienden los derechos** (acceso, rectificación, supresión): canal (email del Titular) y plazos. Por ley, la respuesta al acceso es de 10 días corridos y la de rectificación o supresión de 5 días hábiles (verificar en el decreto 1558/2001).
5. **Qué pasa con los datos al dar de baja la cuenta:** qué se borra y qué se conserva (por ejemplo, el registro de compras por obligaciones contables y fiscales; verificar el plazo de conservación aplicable).
6. **Plazo de conservación de los registros técnicos (logs).** Hoy no está definido.
7. **Si el Titular accede a los PDF de los usuarios** (por ejemplo, para soporte): sí o no, y en qué casos.
8. **Inscripción de la base de datos en la AAIP** (arts. 21 y 22 Ley 25.326): si ya está inscripta, número y fecha; si no, decidir si se tramita antes de publicar.
9. **Menores:** el servicio está destinado a profesionales mayores de edad. Confirmar para incluirlo.

## 4. Cómo se integra el texto final

1. Pegar el texto en `templates/legal_privacidad.html`, dentro del bloque `texto_legal` (misma estructura que `legal_terminos.html`).
2. Reemplazar los placeholders por las variables del sitio: `{{ responsable.nombre }}`, `{{ responsable.cuit }}`, `{{ responsable.domicilio }}`, `{{ responsable.email }}`, `{{ terminos_version }}`, `{{ terminos_fecha }}`, `{{ retencion_pdf_dias }}`.
3. Poner `PRIVACIDAD_BORRADOR=0` en el servidor para quitar el cartel "Documento en preparación".
