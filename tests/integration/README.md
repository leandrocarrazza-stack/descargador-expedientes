# Tests de Integración

Estos tests requieren servicios externos y **no corren en CI**:

- Mesa Virtual (Poder Judicial de Entre Ríos) — credenciales reales
- Selenium + Chrome instalado localmente
- Conexión a internet

## Cómo correrlos

```bash
# Configurar credenciales en .env primero
python test_simple.py
python test_pipeline_directo.py
```

No usar `pytest` sobre estos archivos en CI.
