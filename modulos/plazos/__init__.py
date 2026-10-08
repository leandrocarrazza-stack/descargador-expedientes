"""
Contador de plazos judiciales (Poder Judicial de Entre Ríos)
============================================================

- localidades.py: localidades con sede judicial → departamento.
- textos.py: lectura de fechas escritas en castellano ("16 y 17 de Febrero").
- reglas.py: feriados que salen de una regla fija (fechas fijas, Pascua,
  rotación Magistratura/Abogacía) + datos históricos 2020-2025.
- calendario.py: junta reglas + registros de la base y responde "¿este día
  es hábil?" para una localidad/fuero.
- calculo.py: cómputo del plazo (lógica pura, sin Flask ni base).
- sync_stjer.py: actualización automática desde jusentrerios.gov.ar.
"""
