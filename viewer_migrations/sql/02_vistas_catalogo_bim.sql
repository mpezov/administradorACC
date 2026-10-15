-- ============================================================================
-- 02_vistas_catalogo_bim.sql
-- Vistas de integración. Solo lectura.
-- ============================================================================

CREATE OR REPLACE VIEW public.v_proyectos_avance_latest AS
SELECT DISTINCT ON (p.cui)
    p.*
FROM public.proyectos_avance p
WHERE p.cui IS NOT NULL
ORDER BY p.cui, p.fecha_carga DESC NULLS LAST, p.anio_reporte DESC NULLS LAST, p.id DESC;

CREATE OR REPLACE VIEW public.v_bim_models_current AS
SELECT
    bp.id AS project_id,
    bp.project_code,
    bp.cui,
    bp.wvs,
    bp.project_type,
    bp.viewer_enabled,
    bp.managerial_profile,
    bp.local_project_key,
    m.id AS model_id,
    m.model_code,
    m.model_role,
    m.is_default,
    m.enabled AS model_enabled,
    m.download_policy,
    m.acc_item_id,
    i.item_name AS acc_item_name,
    i.ext AS acc_ext,
    i.folder_id AS acc_folder_id,
    i.folder_path_cached AS acc_folder_path,
    i.latest_version_id AS acc_latest_version_id,
    i.last_modified AS acc_last_modified,
    i.is_active AS acc_item_active,
    lv.id AS local_version_id,
    lv.acc_version_id AS local_acc_version_id,
    lv.version_urn,
    lv.version_number,
    lv.viewable_guid,
    lv.local_root,
    lv.local_entry,
    lv.manifest_path,
    lv.layers_cache_path,
    lv.status AS local_status,
    lv.is_active AS local_is_active,
    lv.downloaded_at,
    CASE
        WHEN m.acc_item_id IS NULL THEN 'MODEL_NOT_CONFIGURED'
        WHEN COALESCE(i.is_active,false) = false THEN 'ACC_ITEM_INACTIVE'
        WHEN lv.id IS NULL THEN 'NOT_DOWNLOADED'
        WHEN lv.acc_version_id IS DISTINCT FROM i.latest_version_id THEN 'UPDATE_AVAILABLE'
        WHEN lv.status IN ('ACTIVE','READY') THEN 'READY'
        ELSE lv.status
    END AS sync_status
FROM public.bim_projects bp
JOIN public.bim_project_models m ON m.project_id = bp.id
LEFT JOIN public.acc_items i ON i.item_id = m.acc_item_id
LEFT JOIN LATERAL (
    SELECT v.*
    FROM public.bim_local_versions v
    WHERE v.model_id = m.id
    ORDER BY v.is_active DESC, v.downloaded_at DESC NULLS LAST, v.id DESC
    LIMIT 1
) lv ON true;

CREATE OR REPLACE VIEW public.v_bim_portfolio AS
SELECT
    bp.id AS project_id,
    bp.project_code,
    bp.cui,
    bp.wvs,
    bp.project_type,
    bp.viewer_enabled,
    bp.managerial_profile,
    bp.local_project_key,
    ap.name AS acc_project_name,
    ap.id_guid AS acc_project_guid,
    pa.nombre_proyecto,
    pa.direccion_encargada,
    pa.unidad_funcional,
    pa.paquete,
    pa.departamento,
    pa.costo,
    pa.devengado_acumulado,
    pa.porcentaje_avance_financiero,
    pa.a_diseno,
    pa.a_fisico,
    pa.a_financiero,
    pa.mes_reporte,
    pa.anio_reporte,
    pa.fecha_carga AS fecha_indicadores,
    loc.latitud,
    loc.longitud,
    loc.geom,
    dm.model_id AS default_model_id,
    dm.acc_item_name AS default_model_name,
    dm.acc_latest_version_id,
    dm.acc_last_modified AS bim_last_modified,
    dm.sync_status AS bim_sync_status
FROM public.bim_projects bp
LEFT JOIN public.acc_projects ap ON ap.id_guid = bp.acc_project_guid
LEFT JOIN public.v_proyectos_avance_latest pa ON pa.cui = bp.cui
LEFT JOIN LATERAL (
    SELECT p.latitud, p.longitud, p.geom
    FROM public.proyectosanin p
    WHERE bp.cui ~ '^[0-9]+$'
      AND p.cod_cui = bp.cui::numeric
    ORDER BY p.id DESC
    LIMIT 1
) loc ON true
LEFT JOIN LATERAL (
    SELECT mc.*
    FROM public.v_bim_models_current mc
    WHERE mc.project_id = bp.id
      AND mc.is_default = true
      AND mc.model_enabled = true
    LIMIT 1
) dm ON true;

CREATE OR REPLACE VIEW public.v_bim_project_manifest AS
SELECT
    bp.id AS project_id,
    bp.project_code,
    jsonb_build_object(
        'project_code', bp.project_code,
        'cui', bp.cui,
        'wvs', bp.wvs,
        'name', COALESCE(pa.nombre_proyecto, ap.name, bp.project_code),
        'project_type', bp.project_type,
        'profile', bp.managerial_profile,
        'local_project_key', bp.local_project_key,
        'srid', bp.srid,
        'indicators', jsonb_build_object(
            'cost', pa.costo,
            'accrued', pa.devengado_acumulado,
            'financial_progress', COALESCE(pa.a_financiero, pa.porcentaje_avance_financiero),
            'physical_progress', pa.a_fisico,
            'design_progress', pa.a_diseno,
            'report_month', pa.mes_reporte,
            'report_year', pa.anio_reporte,
            'loaded_at', pa.fecha_carga
        ),
        'models', COALESCE((
            SELECT jsonb_agg(
                jsonb_build_object(
                    'model_id', mc.model_id,
                    'code', mc.model_code,
                    'role', mc.model_role,
                    'default', mc.is_default,
                    'acc_item_id', mc.acc_item_id,
                    'filename', mc.acc_item_name,
                    'latest_version_id', mc.acc_latest_version_id,
                    'local_version_id', mc.local_version_id,
                    'local_acc_version_id', mc.local_acc_version_id,
                    'viewable_guid', mc.viewable_guid,
                    'local_root', mc.local_root,
                    'local_entry', mc.local_entry,
                    'layers_cache', mc.layers_cache_path,
                    'sync_status', mc.sync_status
                )
                ORDER BY mc.is_default DESC, mc.model_code
            )
            FROM public.v_bim_models_current mc
            WHERE mc.project_id = bp.id
              AND mc.model_enabled = true
        ), '[]'::jsonb),
        'extensions', COALESCE((
            SELECT jsonb_agg(
                jsonb_build_object(
                    'code', e.extension_code,
                    'enabled', e.enabled,
                    'config', e.config
                )
                ORDER BY e.extension_code
            )
            FROM public.bim_project_extensions e
            WHERE e.project_id = bp.id AND e.enabled = true
        ), '[]'::jsonb)
    ) AS manifest
FROM public.bim_projects bp
LEFT JOIN public.acc_projects ap ON ap.id_guid = bp.acc_project_guid
LEFT JOIN public.v_proyectos_avance_latest pa ON pa.cui = bp.cui;
