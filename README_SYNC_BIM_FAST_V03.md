# SYNC BIM FAST v0.3 — Discovery recursivo NWD

## Objetivo

Mantiene el modo rápido existente para modelos ya registrados y añade un modo de descubrimiento que recorre las carpetas ACC de un proyecto de manera recursiva buscando estrictamente archivos `.nwd`.

## Prueba recomendada — IREN SUR

### 1. Escaneo completo del proyecto (solo lectura)

```powershell
python run_sync_bim_fast.py --scan-folders --project-code 100021
```

El script intenta resolver desde PostgreSQL:
- el registro de `public.bim_projects` correspondiente a 100021;
- el GUID ACC;
- el `hub_id` (preferentemente desde `public.acc_projects`).

No modifica la base.

### 2. Empezar desde la carpeta detectada en ACC

```powershell
python run_sync_bim_fast.py --scan-folders --project-code 100021 --folder-urn "urn:adsk.wipprod:fs.folder:co.-cZ-na8uTweOR9E_TTO5dw"
```

Esto es útil para validar rápidamente la carpeta `5_SEGUIMIENTO DE OBRA` antes de recorrer todo el proyecto.

### 3. Si el código 100021 no permite resolver el GUID

```powershell
python run_sync_bim_fast.py --scan-folders --project-code 100021 --project-guid ebb8be83-45a3-4424-9ec7-da7f60a1d2c0
```

### 4. Aplicar descubrimiento

Solo después de revisar el CHECK:

```powershell
python run_sync_bim_fast.py --scan-folders --project-code 100021 --apply
```

En APPLY:
- los NWD ya registrados en `bim_project_models` refrescan `source_filename_cache`, carpeta y ruta cacheada;
- los NWD nuevos se guardan en `public.bim_nwd_candidates`;
- **no** se insertan automáticamente como modelos activos en `bim_project_models`.

Esto evita que un NWD encontrado accidentalmente reemplace el federado gerencial sin revisión.

## Salida esperada

```text
[NUEVO     ] V 12  100021-JYS01-100-ZZZ-MBM-BIM-E5-000001.nwd
             /03_Etapa 5/.../5_SEGUIMIENTO DE OBRA

[REGISTRADO] V 31  100021-JYS01-100-ZZZ-FED-BIM-E5-000001.nwd
             /...

RESUMEN DISCOVERY NWD
Carpetas recorridas : ...
Items revisados     : ...
NWD encontrados     : ...
Ya registrados      : ...
Nuevos candidatos   : ...
```

Se genera `sync_bim_discovery_100021.json` con todos los candidatos y su última versión remota.

## Modo rápido anterior

Sigue disponible sin cambios:

```powershell
python run_sync_bim_fast.py --limit 5
python run_sync_bim_fast.py
```

Ese modo consulta únicamente NWD ya registrados en `bim_project_models`; no recorre carpetas.
