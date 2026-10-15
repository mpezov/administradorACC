from sqlalchemy import text
from .. import models

DEFAULT_BIM_EXTENSIONS = ("nwd", "nwc", "rvt", "ifc")
CAD_EXTENSIONS = ("dwg", "dgn", "dxf")

# Proyectos fuera del análisis gerencial por defecto.
# No se eliminan de ACC ni de la base; solo se omiten de portafolio/estadísticas.
DEFAULT_PROJECT_EXCLUDE_REGEX = (
    r"(^|[^a-z0-9])"
    r"(no[ _-]*vale|pruebas?|tests?|repositorio|mantenimiento)"
    r"([^a-z0-9]|$)"
)


def _norm_exts(exts):
    clean = []
    for e in exts:
        ext = str(e).strip().lower().lstrip(".")
        if ext and ext not in CAD_EXTENSIONS and ext not in clean:
            clean.append(ext)
    return clean or list(DEFAULT_BIM_EXTENSIONS)


def get_inventory_run_status():
    sql = """
    SELECT id, started_at, finished_at, ok, rows_ins, rows_upd, note
    FROM public.acc_etl_runs
    ORDER BY id DESC
    LIMIT 1
    """
    with models.engine.begin() as conn:
        row = conn.execute(text(sql)).mappings().first()
    if not row:
        return None
    out = dict(row)
    out["status"] = "RUNNING" if out.get("finished_at") is None else ("OK" if out.get("ok") is True else "FAILED")
    return out


def list_project_portfolio(q="", only_bim=None, bim_exts=DEFAULT_BIM_EXTENSIONS, include_excluded=False, exclude_regex=DEFAULT_PROJECT_EXCLUDE_REGEX):
    exts = _norm_exts(bim_exts)
    sql = """
    WITH folder_stats AS (
        SELECT project_guid,
               COUNT(*) FILTER (WHERE COALESCE(is_active,true)) AS folders_active
        FROM public.acc_folders
        GROUP BY project_guid
    ),
    item_stats AS (
        SELECT project_guid,
               COUNT(*) FILTER (WHERE COALESCE(is_active,true)) AS items_active,
               COUNT(*) FILTER (WHERE COALESCE(is_active,true)
                                AND lower(COALESCE(ext,'')) = ANY(:bim_exts)) AS bim_files,
               COUNT(*) FILTER (WHERE COALESCE(is_active,true) AND lower(COALESCE(ext,''))='nwd') AS nwd_count,
               COUNT(*) FILTER (WHERE COALESCE(is_active,true) AND lower(COALESCE(ext,''))='rvt') AS rvt_count,
               COUNT(*) FILTER (WHERE COALESCE(is_active,true) AND lower(COALESCE(ext,''))='ifc') AS ifc_count,
               COUNT(*) FILTER (WHERE COALESCE(is_active,true) AND lower(COALESCE(ext,''))='nwc') AS nwc_count,
               COALESCE(SUM(size_bytes) FILTER (WHERE COALESCE(is_active,true)
                                AND lower(COALESCE(ext,'')) = ANY(:bim_exts)),0) AS bim_size_bytes,
               MAX(last_modified) FILTER (WHERE COALESCE(is_active,true)
                                AND lower(COALESCE(ext,'')) = ANY(:bim_exts)) AS last_bim_modified
        FROM public.acc_items
        GROUP BY project_guid
    )
    SELECT p.id_guid AS project_guid, p.cod_wvs, p.name AS acc_name, p.hub_name, p.region,
           (lower(COALESCE(p.name,'')) ~ :exclude_regex) AS analysis_excluded,
           COALESCE(fs.folders_active,0) AS folders_active,
           COALESCE(ins.items_active,0) AS items_active,
           COALESCE(ins.bim_files,0) AS bim_files,
           COALESCE(ins.nwd_count,0) AS nwd_count,
           COALESCE(ins.rvt_count,0) AS rvt_count,
           COALESCE(ins.ifc_count,0) AS ifc_count,
           COALESCE(ins.nwc_count,0) AS nwc_count,
           COALESCE(ins.bim_size_bytes,0) AS bim_size_bytes,
           ins.last_bim_modified,
           pa.cui, pa.nombre_proyecto, pa.direccion_encargada, pa.unidad_funcional,
           pa.paquete, pa.departamento, pa.costo, pa.devengado_acumulado,
           pa.porcentaje_avance_financiero, pa.a_diseno, pa.a_fisico, pa.a_financiero,
           pa.mes_reporte, pa.anio_reporte, pa.fecha_carga AS indicadores_fecha_carga
    FROM public.acc_projects p
    LEFT JOIN folder_stats fs ON fs.project_guid=p.id_guid
    LEFT JOIN item_stats ins ON ins.project_guid=p.id_guid
    LEFT JOIN LATERAL (
        SELECT pav.*
        FROM public.proyectos_avance pav
        WHERE NULLIF(trim(p.cod_wvs::text),'') IS NOT NULL
          AND trim(pav.wvs::text)=trim(p.cod_wvs::text)
        ORDER BY pav.fecha_carga DESC NULLS LAST,
                 pav.anio_reporte DESC NULLS LAST,
                 pav.mes_reporte DESC NULLS LAST,
                 pav.id DESC
        LIMIT 1
    ) pa ON true
    WHERE (:q = ''
       OR lower(COALESCE(p.name,'')) LIKE '%%' || lower(:q) || '%%'
       OR lower(COALESCE(p.cod_wvs::text,'')) LIKE '%%' || lower(:q) || '%%'
       OR lower(COALESCE(pa.cui::text,'')) LIKE '%%' || lower(:q) || '%%'
       OR lower(COALESCE(pa.nombre_proyecto,'')) LIKE '%%' || lower(:q) || '%%'
       OR lower(COALESCE(pa.departamento,'')) LIKE '%%' || lower(:q) || '%%')
      AND (:include_excluded = true
           OR NOT (lower(COALESCE(p.name,'')) ~ :exclude_regex))
      AND (:only_bim IS NULL
       OR (:only_bim = true AND COALESCE(ins.bim_files,0)>0)
       OR (:only_bim = false AND COALESCE(ins.bim_files,0)=0))
    ORDER BY CASE WHEN COALESCE(ins.bim_files,0)>0 THEN 0 ELSE 1 END,
             COALESCE(pa.nombre_proyecto,p.name), p.id_guid
    """
    with models.engine.begin() as conn:
        rows = conn.execute(text(sql), {"q": (q or "").strip(), "only_bim": only_bim, "bim_exts": exts, "include_excluded": bool(include_excluded), "exclude_regex": exclude_regex}).mappings().all()
    return [dict(r) for r in rows]


def get_project_overview(project_guid, bim_exts=DEFAULT_BIM_EXTENSIONS):
    exts = _norm_exts(bim_exts)
    sql = """
    WITH fs AS (
      SELECT COUNT(*) FILTER (WHERE COALESCE(is_active,true)) AS folders_active
      FROM public.acc_folders WHERE project_guid=:pg
    ), its AS (
      SELECT COUNT(*) FILTER (WHERE COALESCE(is_active,true)) AS items_active,
             COUNT(*) FILTER (WHERE COALESCE(is_active,true) AND lower(COALESCE(ext,''))=ANY(:bim_exts)) AS bim_files,
             COUNT(*) FILTER (WHERE COALESCE(is_active,true) AND lower(COALESCE(ext,''))='nwd') AS nwd_count,
             COUNT(*) FILTER (WHERE COALESCE(is_active,true) AND lower(COALESCE(ext,''))='rvt') AS rvt_count,
             COUNT(*) FILTER (WHERE COALESCE(is_active,true) AND lower(COALESCE(ext,''))='ifc') AS ifc_count,
             COUNT(*) FILTER (WHERE COALESCE(is_active,true) AND lower(COALESCE(ext,''))='nwc') AS nwc_count,
             COALESCE(SUM(size_bytes) FILTER (WHERE COALESCE(is_active,true) AND lower(COALESCE(ext,''))=ANY(:bim_exts)),0) AS bim_size_bytes,
             MAX(last_modified) FILTER (WHERE COALESCE(is_active,true) AND lower(COALESCE(ext,''))=ANY(:bim_exts)) AS last_bim_modified
      FROM public.acc_items WHERE project_guid=:pg
    )
    SELECT p.id_guid AS project_guid, p.cod_wvs, p.name AS acc_name, p.hub_name, p.region,
           (lower(COALESCE(p.name,'')) ~ :exclude_regex) AS analysis_excluded,
           p.created_at_acc, p.updated_at AS acc_project_updated_at,
           COALESCE(fs.folders_active,0) AS folders_active,
           COALESCE(its.items_active,0) AS items_active,
           COALESCE(its.bim_files,0) AS bim_files,
           COALESCE(its.nwd_count,0) AS nwd_count,
           COALESCE(its.rvt_count,0) AS rvt_count,
           COALESCE(its.ifc_count,0) AS ifc_count,
           COALESCE(its.nwc_count,0) AS nwc_count,
           COALESCE(its.bim_size_bytes,0) AS bim_size_bytes,
           its.last_bim_modified,
           pa.cui, pa.nombre_proyecto, pa.direccion_encargada, pa.unidad_funcional,
           pa.paquete, pa.departamento, pa.costo, pa.devengado_acumulado,
           pa.porcentaje_avance_financiero, pa.a_diseno, pa.a_fisico, pa.a_financiero,
           pa.mes_reporte, pa.anio_reporte, pa.fecha_carga AS indicadores_fecha_carga
    FROM public.acc_projects p CROSS JOIN fs CROSS JOIN its
    LEFT JOIN LATERAL (
      SELECT pav.* FROM public.proyectos_avance pav
      WHERE NULLIF(trim(p.cod_wvs::text),'') IS NOT NULL
        AND trim(pav.wvs::text)=trim(p.cod_wvs::text)
      ORDER BY pav.fecha_carga DESC NULLS LAST, pav.anio_reporte DESC NULLS LAST,
               pav.mes_reporte DESC NULLS LAST, pav.id DESC LIMIT 1
    ) pa ON true
    WHERE p.id_guid=:pg
    """
    with models.engine.begin() as conn:
        row = conn.execute(text(sql), {"pg": project_guid, "bim_exts": exts, "exclude_regex": DEFAULT_PROJECT_EXCLUDE_REGEX}).mappings().first()
    return dict(row) if row else None


def list_current_bim_items(project_guid, bim_exts=DEFAULT_BIM_EXTENSIONS):
    """Todos los modelos 3D/BIM activos. CAD 2D no entra en esta consulta."""
    exts = _norm_exts(bim_exts)
    sql = """
    SELECT
        i.item_id, i.folder_id, i.folder_path_cached, i.item_name,
        lower(COALESCE(i.ext,'')) AS ext, i.version_count, i.latest_version_id,
        i.last_modified, i.size_bytes, i.uploader, i.naming_ok, i.is_orphan,
        COUNT(*) OVER (PARTITION BY lower(COALESCE(i.item_name,''))) AS same_name_count,
        CASE
          WHEN lower(COALESCE(i.ext,''))='nwd' THEN 'NAVISWORKS_NWD'
          WHEN lower(COALESCE(i.ext,''))='nwc' THEN 'NAVISWORKS_CACHE'
          WHEN lower(COALESCE(i.ext,''))='rvt' THEN 'REVIT'
          WHEN lower(COALESCE(i.ext,''))='ifc' THEN 'IFC'
          ELSE upper(COALESCE(i.ext,'OTRO'))
        END AS bim_kind,
        CASE
          WHEN lower(COALESCE(i.folder_path_cached,'')) LIKE '%%03_pub%%' THEN 'PUB'
          WHEN lower(COALESCE(i.folder_path_cached,'')) LIKE '%%02_com%%' THEN 'COM'
          WHEN lower(COALESCE(i.folder_path_cached,'')) LIKE '%%01_wip%%' THEN 'WIP'
          ELSE 'OTRA'
        END AS stage,
        CASE
          WHEN lower(i.item_name) ~ '[-_](est|str)[-_]' THEN 'ESTRUCTURAS'
          WHEN lower(i.item_name) ~ '[-_](hid|dre)[-_]' THEN 'HIDRÁULICA'
          WHEN lower(i.item_name) ~ '[-_](via|vial)[-_]' THEN 'VIAL'
          WHEN lower(i.item_name) ~ '[-_](top|tgs)[-_]' THEN 'TOPOGRAFÍA'
          WHEN lower(i.item_name) ~ '[-_](ar|arq)[-_]' THEN 'ARQUITECTURA'
          WHEN lower(i.item_name) ~ '[-_](ee|ele)[-_]' THEN 'ELÉCTRICA'
          WHEN lower(i.item_name) ~ '[-_](me|mec)[-_]' THEN 'MECÁNICA'
          WHEN lower(i.item_name) ~ '[-_](bim|coo)[-_]' THEN 'COORDINACIÓN'
          ELSE 'GENERAL / OTRA'
        END AS discipline,
        'ACTIVO ACC' AS current_status
    FROM public.acc_items i
    WHERE i.project_guid=:pg
      AND COALESCE(i.is_active,true)=true
      AND lower(COALESCE(i.ext,''))=ANY(:bim_exts)
    ORDER BY
      CASE lower(COALESCE(i.ext,''))
        WHEN 'nwd' THEN 1 WHEN 'rvt' THEN 2 WHEN 'ifc' THEN 3 WHEN 'nwc' THEN 4 ELSE 9
      END,
      i.last_modified DESC NULLS LAST, i.item_name
    """
    with models.engine.begin() as conn:
        rows = conn.execute(text(sql), {"pg": project_guid, "bim_exts": exts}).mappings().all()
    return [dict(r) for r in rows]


def list_federated_candidates(project_guid, min_score=70, limit=8):
    """Reduce los NWD a pocos candidatos gerenciales, agrupando copias por nombre."""
    sql = r"""
    WITH base AS (
      SELECT i.item_id, i.project_guid, i.folder_id, i.folder_path_cached, i.item_name,
             i.version_count, i.latest_version_id, i.last_modified, i.size_bytes,
             p.cod_wvs,
             lower(COALESCE(i.item_name,'')) AS name_l,
             lower(COALESCE(i.folder_path_cached,'')) AS path_l
      FROM public.acc_items i
      JOIN public.acc_projects p ON p.id_guid=i.project_guid
      WHERE i.project_guid=:pg
        AND COALESCE(i.is_active,true)=true
        AND lower(COALESCE(i.ext,''))='nwd'
    ), features AS (
      SELECT b.*,
        CASE WHEN path_l LIKE '%%03_pub%%' THEN 'PUB'
             WHEN path_l LIKE '%%02_com%%' THEN 'COM'
             WHEN path_l LIKE '%%01_wip%%' THEN 'WIP' ELSE 'OTRA' END AS stage,
        (name_l ~ '(federad|federat|master|integrado|integrated)') AS name_federated,
        (name_l ~ '(coord|bim)') AS name_coordination,
        (path_l ~ '(federad|federat)') AS folder_federated,
        (path_l LIKE '%%gestion bim%%' OR path_l LIKE '%%gestión bim%%' OR path_l LIKE '%%modelos bim%%' OR path_l LIKE '%%/bim/%%') AS folder_bim,
        (NULLIF(trim(COALESCE(cod_wvs,'')),'') IS NOT NULL AND position(lower(trim(cod_wvs)) in name_l)>0) AS project_code_match,
        (name_l ~ '(puente|bridge|alcantarill|viaduct)') AS partial_scope,
        (name_l ~ '[-_](est|str|hid|dre|via|vial|top|tgs|ar|arq|ee|ele|me|mec)[-_]') AS discipline_specific,
        (path_l ~ '(informe|anexo|interferenc|valoriz|lamina|lámina|reporte mensual|informes mensuales)') AS documentary_copy,
        (path_l ~ '(rechazad|rejected|obsolet|backup|respaldo|temporal|borrador)') AS hard_excluded
      FROM base b
    ), scored AS (
      SELECT f.*,
        (40
         + CASE WHEN name_federated THEN 30 ELSE 0 END
         + CASE WHEN name_coordination THEN 12 ELSE 0 END
         + CASE WHEN folder_federated THEN 18 ELSE 0 END
         + CASE WHEN folder_bim THEN 8 ELSE 0 END
         + CASE WHEN project_code_match THEN 8 ELSE 0 END
         + CASE stage WHEN 'PUB' THEN 12 WHEN 'COM' THEN 8 WHEN 'WIP' THEN 3 ELSE 0 END
         - CASE WHEN partial_scope THEN 18 ELSE 0 END
         - CASE WHEN discipline_specific AND NOT name_federated THEN 12 ELSE 0 END
         - CASE WHEN documentary_copy THEN 35 ELSE 0 END
         - CASE WHEN hard_excluded THEN 100 ELSE 0 END)::integer AS candidate_score
      FROM features f
    ), ranked AS (
      SELECT s.*,
        COUNT(*) OVER (PARTITION BY lower(regexp_replace(COALESCE(item_name,''), '\\s+', ' ', 'g'))) AS same_name_count,
        ROW_NUMBER() OVER (
          PARTITION BY lower(regexp_replace(COALESCE(item_name,''), '\\s+', ' ', 'g'))
          ORDER BY hard_excluded ASC, candidate_score DESC, last_modified DESC NULLS LAST, item_id
        ) AS name_rank
      FROM scored s
    )
    SELECT * FROM ranked
    WHERE name_rank=1 AND hard_excluded=false
    ORDER BY candidate_score DESC, last_modified DESC NULLS LAST
    """
    with models.engine.begin() as conn:
        rows = [dict(r) for r in conn.execute(text(sql), {"pg": project_guid}).mappings().all()]

    strong = [r for r in rows if int(r.get("candidate_score") or 0) >= int(min_score)]
    selected = strong[:int(limit)] if strong else rows[:min(int(limit), 5)]
    for row in selected:
        score = int(row.get("candidate_score") or 0)
        row["confidence"] = "MUY ALTO" if score >= 95 else ("ALTO" if score >= 80 else ("MEDIO" if score >= 70 else "REVISAR"))
        reasons, warnings = [], []
        if row.get("name_federated"): reasons.append("Nombre federado")
        if row.get("folder_federated"): reasons.append("Carpeta federado")
        if row.get("stage") in ("PUB", "COM"): reasons.append(row["stage"])
        if row.get("folder_bim"): reasons.append("Gestión BIM")
        if row.get("project_code_match"): reasons.append("Código coincide")
        if row.get("name_coordination"): reasons.append("BIM/Coordinación")
        if row.get("partial_scope"): warnings.append("Alcance parcial")
        if row.get("discipline_specific") and not row.get("name_federated"): warnings.append("Disciplina específica")
        if row.get("documentary_copy"): warnings.append("Copia documental")
        if int(row.get("same_name_count") or 1) > 1: warnings.append(f"{row['same_name_count']} ubicaciones")
        row["reasons"], row["warnings"] = reasons, warnings
    return selected

def get_project_bim_extension_stats(project_guid, bim_exts=DEFAULT_BIM_EXTENSIONS):
    exts = _norm_exts(bim_exts)
    sql = """
    SELECT lower(COALESCE(ext,'')) AS ext, COUNT(*) AS total,
           COALESCE(SUM(size_bytes) FILTER (WHERE COALESCE(size_bytes,0)>0),0) AS size_bytes, MAX(last_modified) AS last_modified
    FROM public.acc_items
    WHERE project_guid=:pg AND COALESCE(is_active,true)=true
      AND lower(COALESCE(ext,''))=ANY(:bim_exts)
    GROUP BY lower(COALESCE(ext,'')) ORDER BY total DESC, ext
    """
    with models.engine.begin() as conn:
        rows = conn.execute(text(sql), {"pg": project_guid, "bim_exts": exts}).mappings().all()
    return [dict(r) for r in rows]


def get_project_bim_folder_stats(project_guid, bim_exts=DEFAULT_BIM_EXTENSIONS, limit=20):
    exts = _norm_exts(bim_exts)
    sql = """
    SELECT COALESCE(folder_path_cached,'(sin ruta)') AS folder_path,
           COUNT(*) AS bim_files, MAX(last_modified) AS last_modified
    FROM public.acc_items
    WHERE project_guid=:pg AND COALESCE(is_active,true)=true
      AND lower(COALESCE(ext,''))=ANY(:bim_exts)
    GROUP BY COALESCE(folder_path_cached,'(sin ruta)')
    ORDER BY bim_files DESC, folder_path LIMIT :lim
    """
    with models.engine.begin() as conn:
        rows = conn.execute(text(sql), {"pg": project_guid, "bim_exts": exts, "lim": limit}).mappings().all()
    return [dict(r) for r in rows]


def get_bim_global_stats(bim_exts=DEFAULT_BIM_EXTENSIONS, exclude_regex=DEFAULT_PROJECT_EXCLUDE_REGEX):
    exts = _norm_exts(bim_exts)
    sql = """
    WITH valid_projects AS (
        SELECT id_guid
        FROM public.acc_projects
        WHERE NOT (lower(COALESCE(name,'')) ~ :exclude_regex)
    ),
    excluded AS (
        SELECT COUNT(*) AS total
        FROM public.acc_projects
        WHERE lower(COALESCE(name,'')) ~ :exclude_regex
    )
    SELECT
       (SELECT COUNT(*) FROM valid_projects) AS total_projects,
       (SELECT total FROM excluded) AS excluded_projects,
       (SELECT COUNT(*)
          FROM public.acc_folders f
          JOIN valid_projects vp ON vp.id_guid=f.project_guid
         WHERE COALESCE(f.is_active,true)) AS active_folders,
       (SELECT COUNT(*)
          FROM public.acc_items ai
          JOIN valid_projects vp ON vp.id_guid=ai.project_guid
         WHERE COALESCE(ai.is_active,true)) AS active_items,
       COUNT(*) FILTER (
          WHERE COALESCE(i.is_active,true)
            AND lower(COALESCE(i.ext,''))=ANY(:bim_exts)
       ) AS bim_files,
       COUNT(DISTINCT i.project_guid) FILTER (
          WHERE COALESCE(i.is_active,true)
            AND lower(COALESCE(i.ext,''))=ANY(:bim_exts)
       ) AS projects_with_bim,
       COUNT(*) FILTER (
          WHERE COALESCE(i.is_active,true)
            AND lower(COALESCE(i.ext,''))='nwd'
       ) AS nwd_files,
       COUNT(*) FILTER (
          WHERE COALESCE(i.is_active,true)
            AND lower(COALESCE(i.ext,''))='rvt'
       ) AS rvt_files,
       COUNT(*) FILTER (
          WHERE COALESCE(i.is_active,true)
            AND lower(COALESCE(i.ext,''))='ifc'
       ) AS ifc_files,
       COUNT(*) FILTER (
          WHERE COALESCE(i.is_active,true)
            AND lower(COALESCE(i.ext,''))='nwc'
       ) AS nwc_files,
       MAX(i.last_modified) FILTER (
          WHERE COALESCE(i.is_active,true)
            AND lower(COALESCE(i.ext,''))=ANY(:bim_exts)
       ) AS latest_bim_change
    FROM public.acc_items i
    JOIN valid_projects vp ON vp.id_guid=i.project_guid
    """
    with models.engine.begin() as conn:
        row = conn.execute(
            text(sql),
            {"bim_exts": exts, "exclude_regex": exclude_regex}
        ).mappings().first()
    return dict(row) if row else {}


def get_top_bim_projects(bim_exts=DEFAULT_BIM_EXTENSIONS, limit=25, exclude_regex=DEFAULT_PROJECT_EXCLUDE_REGEX):
    exts = _norm_exts(bim_exts)
    sql = """
    SELECT p.id_guid, p.cod_wvs, p.name AS project_name,
           COUNT(i.item_id) FILTER (
             WHERE COALESCE(i.is_active,true)=true
               AND lower(COALESCE(i.ext,''))=ANY(:bim_exts)
           ) AS bim_files,
           COUNT(i.item_id) FILTER (
             WHERE COALESCE(i.is_active,true)=true
           ) AS total_files,
           MAX(i.last_modified) FILTER (
             WHERE COALESCE(i.is_active,true)=true
               AND lower(COALESCE(i.ext,''))=ANY(:bim_exts)
           ) AS last_bim_modified
    FROM public.acc_projects p
    LEFT JOIN public.acc_items i ON p.id_guid=i.project_guid
    WHERE NOT (lower(COALESCE(p.name,'')) ~ :exclude_regex)
    GROUP BY p.id_guid,p.cod_wvs,p.name
    HAVING COUNT(i.item_id) FILTER (
      WHERE COALESCE(i.is_active,true)=true
        AND lower(COALESCE(i.ext,''))=ANY(:bim_exts)
    ) > 0
    ORDER BY bim_files DESC
    LIMIT :lim
    """
    with models.engine.begin() as conn:
        rows = conn.execute(
            text(sql),
            {"bim_exts": exts, "lim": limit, "exclude_regex": exclude_regex}
        ).mappings().all()
    return [dict(r) for r in rows]


def get_top_bim_versions(bim_exts=DEFAULT_BIM_EXTENSIONS, limit=25, exclude_regex=DEFAULT_PROJECT_EXCLUDE_REGEX):
    exts = _norm_exts(bim_exts)
    sql = """
    SELECT i.project_guid, p.name AS project_name, i.folder_path_cached,
           i.item_name, i.ext, i.version_count, i.latest_version_id, i.last_modified
    FROM public.acc_items i
    JOIN public.acc_projects p ON i.project_guid=p.id_guid
    WHERE COALESCE(i.is_active,true)=true
      AND NOT (lower(COALESCE(p.name,'')) ~ :exclude_regex)
      AND lower(COALESCE(i.ext,''))=ANY(:bim_exts)
      AND COALESCE(i.version_count,0)>1
    ORDER BY i.version_count DESC, i.last_modified DESC NULLS LAST
    LIMIT :lim
    """
    with models.engine.begin() as conn:
        rows = conn.execute(
            text(sql),
            {"bim_exts": exts, "lim": limit, "exclude_regex": exclude_regex}
        ).mappings().all()
    return [dict(r) for r in rows]

