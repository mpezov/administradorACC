# Administrador ACC · Portal de Proyectos BIM v0.2

Evolución de `administradorACC1` para usar el inventario `public.acc_*` como
portal de proyectos y navegador de modelos BIM vigentes.

## No reemplaza las funciones existentes

Se conserva:
- `/admin/` Explorador ACC por carpetas
- flags `tipo_gis` / `incidencias`
- jobs y descargas GIS
- herramientas existentes del Administrador ACC

Se añade:
- `/admin/projects` Portafolio de proyectos
- `/admin/project/<project_guid>` Ficha BIM del proyecto
- `/admin/bim-stats` Estadísticas BIM/ACC

## Fuentes

Inventario:
- `public.acc_projects`
- `public.acc_folders`
- `public.acc_items`
- `public.acc_etl_runs`

Indicadores:
- `public.proyectos_avance`
- vínculo de negocio: `proyectos_avance.wvs = acc_projects.cod_wvs`
- se toma el registro más reciente por fecha/periodo

El vínculo WVS no sustituye la identidad técnica ACC. El proyecto sigue siendo
`acc_projects.id_guid`.

## Archivo BIM vigente

No se listan todas las versiones históricas. Se muestra una sola fila por
`acc_items.item_id` activo y se usa:

- `latest_version_id` = versión vigente de ACC
- `last_modified` = última modificación
- `folder_path_cached` = ubicación
- `size_bytes` = tamaño

Formatos por defecto:

`nwd,nwc,rvt,ifc,dwg,dgn`

Configurable en `.env`:

`BIM_VIEW_EXTENSIONS=nwd,nwc,rvt,ifc,dwg,dgn`

## Clasificación orientativa

- NWD con FED/FEDER/MASTER/COORD/INTEGR en nombre: prioridad 100
- NWD: 90
- RVT: 70
- IFC: 65
- NWC: 60
- DWG/DGN: 40

Es únicamente ayuda para navegación. El modelo oficial se seleccionará luego
explícitamente mediante `bim_project_models.acc_item_id`.

## Estado del inventario

El portal consulta la última fila de `acc_etl_runs`. Si `finished_at` es NULL,
muestra que el inventario está en ejecución y advierte que los conteos pueden
seguir creciendo mientras la persistencia incremental continúa.

## Instalación del parche

Reemplazar/agregar solamente:

- `app/__init__.py`
- `app/templates/base.html`
- `app/static/app.css`
- `app/routes/portfolio.py` (nuevo)
- `app/utils/portal_db.py` (nuevo)
- `app/templates/projects.html` (nuevo)
- `app/templates/project_detail.html` (nuevo)
- `app/templates/bim_stats.html` (nuevo)

No hay migración SQL y no modifica las tablas del inventario.

## Próxima fase

Cuando finalice ACC Inventory Sync:
1. instalar catálogo `bim_*`;
2. seleccionar desde esta ficha el modelo gerencial;
3. persistir `acc_item_id`;
4. detectar `UPDATE_AVAILABLE` comparando `latest_version_id` con versión local;
5. worker de descarga/derivado;
6. generar `project.json` y `layers.json`;
7. abrir Visor Universal.
