# Administrador ACC · Mirror Cache v0.5

Parche incremental sobre Viewer Manager v0.4.

## Objetivo

Añade un espejo local versionado sin modificar el NWD fuente ni reemplazar
el visor remoto que ya funciona.

Flujo:

ACC derivative READY
    |
    +-- Abrir visor ACC              -> remoto
    |
    +-- Preparar espejo SVF
            |
            +-- crea carpeta vXXXXXX
            +-- guarda aps_manifest.json
            +-- guarda mirror.json
            +-- crea/actualiza bim_local_versions
            |
            +-- Abrir espejo
                    |
                    +-- Viewer pide recursos al backend local
                    +-- backend busca cache
                    +-- HIT  -> sirve disco
                    +-- MISS -> descarga APS, guarda disco, sirve respuesta

## Soporte de v0.5

### SVF clásico
Habilitado:
- preparar espejo;
- cache HTTP por versión;
- reutilización local de recursos ya descargados;
- contadores de archivos y bytes;
- preservación de Range / Content-Range para recursos parciales.

### SVF2
Permanece REMOTO en v0.5.

No se declara soporte local SVF2 porque el proxy moderno de APS para streamingV2
puede requerir WebSocket además de HTTP. El Administrador actual usa Flask/Waitress
WSGI y no debe declarar un mirror SVF2 como local si ese canal no existe.

## Importante: no es todavía "offline completo"

`DOWNLOADING` significa "cache en calentamiento".

El modelo puede abrirse mediante el espejo mientras haya conexión:
- recursos ya cacheados -> disco;
- recursos nuevos -> APS y luego disco.

No se marca `LOCAL_READY` automáticamente porque todavía no tenemos una prueba
exhaustiva de que todos los assets necesarios para navegación completa estén
presentes.

La siguiente fase podrá hacer:
- prefetch exhaustivo de SVF;
- validación de cobertura;
- activar `LOCAL_READY`;
- servir Viewer SDK local para modo completamente desconectado.

## Base de datos

Añade solamente:

`public.bim_local_cache_files`

No altera tablas ACC.

Cada fila registra:
- local_version_id
- cache_key
- remote_path
- Range solicitado
- archivo local
- HTTP status
- Content-Type
- Content-Range
- ETag
- tamaño
- fechas de cache/acceso

`bim_local_versions` ya existía desde Fase 2 / Viewer Manager v0.4.

## Carpeta local

Por defecto:

C:\administradorACC\data\projects

Recomendado para modelos grandes:

D:\miguel\anin\federado\data\projects

Agregar al `.env` real si desea usar D::

BIM_LOCAL_DATA_ROOT=D:\miguel\anin\federado\data\projects

No copie ni reemplace su `.env` con `.env.mirror.example`.

Ejemplo para proyecto 600002 y versión 2:

D:\miguel\anin\federado\data\projects\
└── P600002\
    └── models\
        └── managerial\
            └── v000002\
                ├── mirror.json
                ├── aps_manifest.json
                └── cache\
                    ├── 0a\
                    ├── 0b\
                    └── ...

## Instalación

1. Debe estar funcionando Viewer Manager v0.4.

2. Copiar el contenido del ZIP sobre:

C:\administradorACC

3. Opcional: configurar `BIM_LOCAL_DATA_ROOT` en `.env`.

4. Ejecutar migración:

```powershell
cd C:\administradorACC
python .\migrar_mirror_v0_5.py
python .\migrar_mirror_v0_5.py --check
```

Resultado esperado:

```text
bim_local_versions            OK
bim_local_cache_files         OK
```

5. Verificar carpeta:

```powershell
python .\verificar_mirror_v0_5.py
```

Resultado esperado:

```text
OK: Mirror Cache listo.
```

6. Reiniciar Administrador:

```powershell
python .\run.py
```

## Prueba recomendada

Abrir 600002 y revisar Viewer Manager.

Si el derivative dice `SVF`:

1. `Preparar espejo SVF`
2. volverá a la ficha;
3. debe aparecer ruta local y contador 0;
4. `Abrir espejo`;
5. navegar/zoom/seleccionar;
6. volver a la ficha;
7. los contadores de recursos y tamaño deben haber aumentado.

También puede revisar:

```sql
SELECT
    lv.id,
    lv.acc_version_id,
    lv.derivative_format,
    lv.local_root,
    lv.status,
    lv.file_count,
    lv.size_bytes,
    lv.last_error
FROM public.bim_local_versions lv
ORDER BY lv.id DESC
LIMIT 10;
```

Y el detalle de cache:

```sql
SELECT
    local_version_id,
    count(*) AS recursos,
    sum(size_bytes) AS bytes,
    max(cached_at) AS ultimo_cache
FROM public.bim_local_cache_files
GROUP BY local_version_id
ORDER BY ultimo_cache DESC;
```

Si el derivative dice `SVF2`, v0.5 mostrará `SVF2 · remoto`.
Eso es intencional y no es un error.

## Archivos del parche

Modificados:
- app/routes/portfolio.py
- app/routes/viewer_manager.py
- app/utils/viewer_db.py
- app/templates/project_detail.html
- app/static/app.css

Nuevos:
- app/utils/local_mirror.py
- app/templates/viewer_mirror.html
- app/static/viewer_mirror.js
- viewer_migrations/sql/06_local_mirror_cache.sql
- migrar_mirror_v0_5.py
- verificar_mirror_v0_5.py
- .env.mirror.example

No incluye:
- `.env`
- credenciales
- datos ACC
- modelos
- cache local
