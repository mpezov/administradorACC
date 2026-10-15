# Prueba rápida v0.2 — federados NWD

Este cambio **no reemplaza** `sync_inventario.py`.

Añade un robot paralelo que:

1. Lee `public.bim_project_models`.
2. Reconoce `source_filename_cache` y filtra `.nwd`; usa `format = nwd` como respaldo.
3. No recorre carpetas ACC.
4. Va directo a `/items/{item_id}/versions?page[limit]=1`.
5. Compara la última `version_id` de ACC contra la registrada localmente.
6. En modo normal no escribe en la BD.

## Primera prueba recomendada

Desde la raíz `D:\administradorACC`:

```powershell
conda activate administradoracc
python run_sync_bim_fast.py --limit 5
```

Si el resultado es correcto, probar todos:

```powershell
python run_sync_bim_fast.py
```

El resumen esperado es parecido a:

```text
Federados NWD encontrados: 37
Revisados       : 37
Sin cambios     : 34
Con cambio      : 3
Actualizados BD : 0
Errores         : 0
Tiempo total    : 9.42 s
```

El modo anterior es **solo diagnóstico** y no modifica datos.

## Aplicar cambios

Solo después de verificar la salida:

```powershell
python run_sync_bim_fast.py --apply
```

El modo `--apply` únicamente actualiza columnas que el script reconoce de forma segura dentro de `bim_project_models`. Si el esquema no coincide, reportará que no modificó la BD en lugar de adivinar.

## Reporte

Cada ejecución genera:

```text
sync_bim_fast_last.json
```

con cada NWD, versión local, versión ACC, estado y tiempos globales.

## Importante

Para poder hacer la consulta directa, `bim_project_models` debe contener (con alguno de los nombres reconocidos):

- identificador de proyecto ACC;
- `item_id` del modelo;
- nombre de archivo/modelo.

Si alguna columna no existe, el robot lo indicará y no hará escrituras.


## Cambio v0.2

- Reconoce la columna real `source_filename_cache`.
- Usa también `format` para identificar NWD.
- Si `bim_project_models.project_id` es una FK interna, intenta resolver el GUID ACC desde `bim_projects`.
- Si la versión local no está en `bim_project_models`, intenta leerla desde `bim_remote_versions`.
