-- 06 · Varios federados publicados por proyecto (tramos, especialidades…)
--
-- Hasta la v0.5 el visor mostraba un único federado por proyecto: el modelo
-- MANAGERIAL marcado is_default. Los proyectos lineales se federan por tramos,
-- así que ahora cada modelo MANAGERIAL puede marcarse como "publicado" en el
-- visor, con un orden y una etiqueta opcional (por defecto, el nombre del archivo). El modelo principal
-- (is_default) se publica siempre. La vista v_bim_viewer_state no cambia.
--
-- Solo agrega columnas y una vista: no modifica datos existentes.

ALTER TABLE public.bim_project_models ADD COLUMN IF NOT EXISTS publicado boolean NOT NULL DEFAULT false;
ALTER TABLE public.bim_project_models ADD COLUMN IF NOT EXISTS etiqueta text;
ALTER TABLE public.bim_project_models ADD COLUMN IF NOT EXISTS orden integer;

-- Todos los modelos que se muestran en el visor (principal + publicados),
-- con el mismo cálculo de estado que v_bim_viewer_state.
CREATE OR REPLACE VIEW public.v_bim_viewer_models AS
SELECT bp.id AS project_id,
       bp.project_code,
       bp.wvs,
       bp.acc_project_guid,
       m.id AS model_id,
       m.model_code,
       m.acc_item_id,
       m.is_default,
       m.publicado,
       COALESCE(NULLIF(btrim(m.etiqueta), ''), i.item_name) AS etiqueta,
       COALESCE(m.orden, CASE WHEN m.is_default THEN 0 ELSE 999 END) AS orden,
       i.item_name,
       i.ext,
       i.folder_path_cached,
       i.latest_version_id AS acc_latest_version_id,
       i.last_modified AS acc_last_modified,
       rv.derivative_urn,
       rv.derivative_format,
       rv.default_viewable_guid,
       rv.status AS remote_status,
       rv.checked_at AS remote_checked_at,
       rv.last_error AS remote_error,
       CASE
           WHEN COALESCE(i.is_active, false) = false THEN 'ACC_ITEM_INACTIVE'
           WHEN rv.id IS NOT NULL AND rv.acc_version_id = i.latest_version_id AND rv.status = 'READY' THEN 'REMOTE_READY'
           WHEN rv.id IS NOT NULL AND rv.acc_version_id = i.latest_version_id AND rv.status = 'PROCESSING' THEN 'PROCESSING'
           WHEN rv.id IS NOT NULL AND rv.acc_version_id = i.latest_version_id AND rv.status = 'MISSING' THEN 'SOURCE_ONLY'
           WHEN rv.id IS NOT NULL AND rv.acc_version_id = i.latest_version_id AND rv.status = 'ERROR' THEN 'ERROR'
           ELSE 'NEED_CHECK'
       END AS viewer_status
FROM public.bim_projects bp
JOIN public.bim_project_models m
  ON m.project_id = bp.id
 AND m.model_role = 'MANAGERIAL'
 AND m.enabled = true
 AND (m.is_default = true OR m.publicado = true)
LEFT JOIN public.acc_items i ON i.item_id = m.acc_item_id
LEFT JOIN public.bim_remote_versions rv
  ON rv.model_id = m.id AND rv.acc_version_id = i.latest_version_id;
