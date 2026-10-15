# Administrador ACC · Viewer Manager v0.4

## Alcance implementado

La v0.4 convierte el Administrador en el punto de selección y apertura del modelo gerencial:

1. Selección explícita de un NWD por `acc_item_id`.
2. Persistencia en `bim_projects` + `bim_project_models`.
3. Seguimiento automático de `acc_items.latest_version_id`.
4. Resolución del derivative de ACC para esa versión.
5. Fallback por `versions/:version_id/relationships/refs` cuando el derivative no viene directamente en la versión.
6. Cache de estado remoto en `bim_remote_versions`.
7. Apertura del derivative en Autodesk Viewer sin descargar el NWD.
8. Token de navegador limitado a `viewables:read`.
9. Viewer SDK fijado en `7.108.0`.
10. Preparación de `bim_local_versions` para la siguiente etapa de espejo local.

## Estados

- `NEED_CHECK`: falta consultar el derivative.
- `REMOTE_READY`: derivative disponible y visualizable desde ACC.
- `PROCESSING`: ACC/Model Derivative todavía está procesando.
- `SOURCE_ONLY`: NWD existe, pero no se obtuvo derivative.
- `ERROR`: fallo de consulta o manifest.
- `LOCAL_READY`: reservado para una versión local completa.
- `UPDATE_AVAILABLE`: existe una versión local anterior a la versión ACC vigente.

## Importante sobre el espejo

Esta versión NO marca ningún espejo como `LOCAL_READY` sin haber descargado y validado todos sus recursos.

El siguiente motor se diseñará con dos políticas:

- SVF clásico: espejo local versionado completo.
- SVF2: visualización remota/streaming; no se tratará como un SVF clásico descargable.

## Instalación

Descomprimir este ZIP encima de `C:\administradorACC`.

No reemplazar tu `.env`. `.env.viewer.example` es solo referencia.

### 1. Migrar el catálogo

```powershell
cd C:\administradorACC
python .\migrar_viewer_manager.py
python .\migrar_viewer_manager.py --check
```

La comprobación debe mostrar `OK` para:

- `bim_projects`
- `bim_project_models`
- `bim_remote_versions`
- `bim_local_versions`
- `v_bim_viewer_state`

### 2. Revisar credenciales APS

El navegador solo recibe un 2LO con `viewables:read`.

Preferidas:

```text
APS_CLIENT_ID_S2S=...
APS_CLIENT_SECRET_S2S=...
```

También se admiten `APS_CLIENT_ID/APS_CLIENT_SECRET` y `ACC_CLIENT_ID/ACC_CLIENT_SECRET`.

### 3. Verificar instalación

```powershell
python .\verificar_viewer_manager.py
```

Debe terminar con `OK: Viewer Manager listo para prueba.`

### 4. Reiniciar

```powershell
python .\run.py
```

### 5. Uso

1. Abrir `/admin/projects`.
2. Entrar a un proyecto con NWD.
3. Pulsar `Seleccionar como gerencial` sobre un candidato, o `Seleccionar` sobre cualquier NWD de la tabla.
4. Revisar el bloque `Viewer Manager`.
5. Si aparece `REMOTE_READY`, pulsar `Abrir visor ACC`.
6. Si aparece `NEED_CHECK`, pulsar `Verificar derivative`.

## Proyectos del inventario con TIMEOUT

La corrida general finalizó, pero los proyectos reportados con TIMEOUT conservaron inventario parcial y no fueron reconciliados. Antes de seleccionar definitivamente el federado de uno de ellos, ejecutar nuevamente ese proyecto con timeout ilimitado.

Ejemplo Chulucanas:

```powershell
cd D:\miguel\anin\inventarioACC
python .\acc_inventory_sync.py --project-guid 91e28939-f606-4058-b7e2-b58c937d4871 --no-versions --project-timeout-min 0 --progress-seconds 30
```

No es necesario descargar los históricos de versiones para el Viewer Manager: `latest_version_id` es suficiente para localizar la versión vigente y su derivative.
