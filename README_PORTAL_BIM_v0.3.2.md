# Administrador ACC · Portal BIM v0.3.2

## Cambio principal
Los proyectos auxiliares o descartados se omiten del análisis principal.

Patrones excluidos por defecto:
- NO VALE
- PRUEBA / PRUEBAS
- TEST / TESTS
- REPOSITORIO
- MANTENIMIENTO

No se borran de `acc_projects`, `acc_folders` ni `acc_items`.

## Efecto
- No aparecen en `/admin/projects` por defecto.
- No cuentan en KPI del portafolio.
- No cuentan en `/admin/bim-stats`.
- No aparecen en rankings BIM.
- Siguen disponibles en `/admin/` (Explorador ACC).
- En Proyectos existe el selector `Incluir excluidos` para revisarlos.

## Importante
`PILOTO` NO se excluye por defecto porque podría ser un proyecto real.

## Configuración opcional
Se puede reemplazar la expresión desde `.env`:

PORTFOLIO_EXCLUDE_REGEX=(^|[^a-z0-9])(no[ _-]*vale|pruebas?|tests?|repositorio|mantenimiento)([^a-z0-9]|$)

## Archivos a reemplazar
- app/utils/portal_db.py
- app/routes/portfolio.py
- app/templates/projects.html
- app/templates/bim_stats.html
- app/static/app.css

No hay migración SQL.

## Ejecutar
Reiniciar el Administrador:

python .\run.py
