# sync_bim_fast v0.4.1

Corrección de promoción de candidatos NWD.

## Cambio principal
La v0.4 usaba `download_policy='metadata'` al promover un candidato. En instalaciones donde `bim_project_models_download_policy_check` no admite ese valor, PostgreSQL rechazaba el INSERT.

La v0.4.1 ya no impone ese valor. Al promover:
1. usa el DEFAULT de PostgreSQL si existe;
2. si no existe default, reutiliza el `download_policy` más común del mismo proyecto;
3. si no hay ninguno, intenta el valor más común de toda la tabla;
4. si aún no puede resolver un valor obligatorio, aborta antes de INSERTAR e informa el CHECK detectado.

## Reintento IREN SUR
CHECK:

python run_sync_bim_fast.py --project-code 100021 --promote-item "urn:adsk.wipprod:dm.lineage:U6Db2oltQa6k8ZVT0mQfXQ" --model-code "IREN_SEGUIMIENTO" --model-role federated

APPLY:

python run_sync_bim_fast.py --project-code 100021 --promote-item "urn:adsk.wipprod:dm.lineage:U6Db2oltQa6k8ZVT0mQfXQ" --model-code "IREN_SEGUIMIENTO" --model-role federated --apply

No usar --set-default todavía.
