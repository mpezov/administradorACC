# Sync BIM Fast v0.4 — catálogo NWD persistente

## Objetivo
Separar el descubrimiento costoso de carpetas ACC del refresco cotidiano de versiones.

- **Discovery**: recorre carpetas recursivamente y busca exclusivamente `.nwd`.
- **Catálogo persistente**: guarda cada NWD por `acc_project_guid + acc_item_id` en `public.bim_nwd_candidates`.
- **Fast update**: consulta directamente la última versión de los NWD catalogados, sin recorrer carpetas.
- **Promoción explícita**: un candidato solo entra en `bim_project_models` cuando el operador lo solicita; nunca se promueve automáticamente.

## Recomendación para IREN SUR (100021)

### 1. Aprovechar el discovery que ya tomó ~26 minutos
Si existe `sync_bim_discovery_100021.json` generado por v0.3:

```powershell
python run_sync_bim_fast.py --project-code 100021 --import-discovery-json .\sync_bim_discovery_100021.json
```

Esto es CHECK. Para guardarlo:

```powershell
python run_sync_bim_fast.py --project-code 100021 --import-discovery-json .\sync_bim_discovery_100021.json --apply
```

No vuelve a recorrer ACC.

### 2. Ver los NWD catalogados

```powershell
python run_sync_bim_fast.py --project-code 100021 --list-candidates
```

Muestra versión, `acc_item_id`, nombre y ruta.

### 3. Refresco rápido cotidiano

```powershell
python run_sync_bim_fast.py --project-code 100021
```

Consulta solo los `item_id` del catálogo. No recorre las 4,002 carpetas.

Cuando se quiera persistir una nueva versión detectada:

```powershell
python run_sync_bim_fast.py --project-code 100021 --apply
```

Para NWD ya registrados en `bim_project_models`, actualiza además metadata de versión/ruta cuando el esquema lo permite. Los candidatos siguen siendo candidatos.

### 4. Buscar NWD nuevos deliberadamente

```powershell
python run_sync_bim_fast.py --discover --project-code 100021
```

Para guardar el nuevo discovery:

```powershell
python run_sync_bim_fast.py --discover --project-code 100021 --apply
```

Opcionalmente se puede empezar en una carpeta concreta:

```powershell
python run_sync_bim_fast.py --discover --project-code 100021 --folder-urn "urn:adsk.wipprod:fs.folder:..."
```

`--scan-folders` se mantiene como alias compatible de la v0.3.

## Promoción explícita

Primero copiar el `acc_item_id` desde `--list-candidates`.

CHECK:

```powershell
python run_sync_bim_fast.py --project-code 100021 --promote-item "urn:adsk.wipprod:dm.lineage:..."
```

Para insertar realmente en `bim_project_models`, v0.4 exige un `model_code` explícito:

```powershell
python run_sync_bim_fast.py --project-code 100021 \
  --promote-item "urn:adsk.wipprod:dm.lineage:..." \
  --model-code "IREN_SEGUIMIENTO" \
  --model-role federated \
  --apply
```

`--set-default` existe, pero no desmarca otros modelos por seguridad. Antes de usarlo conviene revisar cuál debe ser el NWD principal del visor.

## Estados

- `REGISTERED`: el mismo `acc_item_id` ya está en `bim_project_models`.
- `CANDIDATE`: NWD descubierto/catalogado, todavía no usado por el Viewer Manager.
- `NUEVA VERSION`: el mismo `acc_item_id` tiene una versión ACC distinta de la guardada en catálogo.

## Identidad
El nombre del archivo no se usa como identidad porque en ACC puede haber varios NWD con el mismo nombre. La identidad operativa es:

`ACC project GUID + acc_item_id`

La carpeta/ruta, nombre y número de versión son metadata.

## Seguridad
- Sin `--apply`, todas las operaciones son de consulta/diagnóstico.
- Discovery nunca promueve candidatos automáticamente.
- Promoción con escritura requiere `--promote-item`, `--model-code` y `--apply`.
