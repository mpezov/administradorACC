import hashlib
import re
from sqlalchemy import text
from .. import models
from .portal_db import DEFAULT_PROJECT_EXCLUDE_REGEX


def schema_ready():
    sql = """
    SELECT
      to_regclass('public.bim_projects') IS NOT NULL
      AND to_regclass('public.bim_project_models') IS NOT NULL
      AND to_regclass('public.bim_remote_versions') IS NOT NULL
      AND to_regclass('public.bim_local_versions') IS NOT NULL AS ok
    """
    with models.engine.begin() as conn:
        return bool(conn.execute(text(sql)).scalar())


def _infer_type(name):
    n = (name or "").lower()
    if "carretera" in n or "vial" in n:
        return "ROAD"
    if "drenaje" in n or "canal" in n:
        return "DRAINAGE"
    if any(x in n for x in ("defensa ribere", "inundacion", "inundación", "quebrada")):
        return "RIVER_DEFENSE"
    if "puente" in n:
        return "BRIDGE"
    if any(x in n for x in ("hospital", "centro de salud", "educativo", "colegio", "institucion educativa", "institución educativa")):
        return "BUILDING"
    if any(x in n for x in ("saneamiento", "agua potable", "alcantarillado")):
        return "SANITATION"
    if any(x in n for x in ("electrico", "eléctrico", "subestacion", "subestación")):
        return "ELECTRICAL"
    return "GENERIC"


def _profile_for(project_type):
    return {
        "BUILDING": "BUILDING_MANAGER",
        "ROAD": "ROAD_MANAGER",
        "DRAINAGE": "DRAINAGE_MANAGER",
        "BRIDGE": "BRIDGE_MANAGER",
        "RIVER_DEFENSE": "RIVER_DEFENSE_MANAGER",
        "SANITATION": "SANITATION_MANAGER",
        "ELECTRICAL": "ELECTRICAL_MANAGER",
        "GENERIC": "GENERIC_MANAGER",
    }.get(project_type, "GENERIC_MANAGER")


def select_managerial_model(project_guid, item_id):
    if not schema_ready():
        raise RuntimeError("CATALOGO_BIM_NO_INSTALADO")

    with models.engine.begin() as conn:
        project = conn.execute(text("""
            SELECT p.id_guid, p.cod_wvs, p.name, pa.cui, pa.nombre_proyecto
            FROM public.acc_projects p
            LEFT JOIN LATERAL (
                SELECT x.cui, x.nombre_proyecto
                FROM public.proyectos_avance x
                WHERE NULLIF(trim(p.cod_wvs::text),'') IS NOT NULL
                  AND trim(x.wvs::text)=trim(p.cod_wvs::text)
                ORDER BY x.fecha_carga DESC NULLS LAST,
                         x.anio_reporte DESC NULLS LAST,
                         x.mes_reporte DESC NULLS LAST,
                         x.id DESC
                LIMIT 1
            ) pa ON true
            WHERE p.id_guid=:pg
        """), {"pg": project_guid}).mappings().first()
        if not project:
            raise ValueError("Proyecto ACC no encontrado.")
        if re.search(DEFAULT_PROJECT_EXCLUDE_REGEX, (project["name"] or "").lower()):
            raise ValueError("Este proyecto está excluido del análisis gerencial.")

        item = conn.execute(text("""
            SELECT item_id, item_name, ext, folder_id, folder_path_cached,
                   latest_version_id, last_modified, is_active
            FROM public.acc_items
            WHERE project_guid=:pg
              AND item_id=:item_id
              AND COALESCE(is_active,true)=true
              AND lower(COALESCE(ext,''))='nwd'
        """), {"pg": project_guid, "item_id": item_id}).mappings().first()
        if not item:
            raise ValueError("El item no es un NWD activo de este proyecto.")

        bp = conn.execute(text("""
            SELECT * FROM public.bim_projects WHERE acc_project_guid=:pg
        """), {"pg": project_guid}).mappings().first()

        if not bp:
            base_code = (str(project["cod_wvs"] or "").strip() or f"ACC-{project_guid[:8]}").upper()
            code = base_code
            local_key = f"P{base_code}"
            conflict = conn.execute(text("""
                SELECT id FROM public.bim_projects
                WHERE project_code=:code OR local_project_key=:local_key
                LIMIT 1
            """), {"code": code, "local_key": local_key}).first()
            if conflict:
                suffix = project_guid[:8].upper()
                code = f"{base_code}-{suffix}"
                local_key = f"P{base_code}-{suffix}"

            project_type = _infer_type(project["nombre_proyecto"] or project["name"])
            profile = _profile_for(project_type)
            bp = conn.execute(text("""
                INSERT INTO public.bim_projects(
                    project_code,cui,wvs,acc_project_guid,project_type,
                    viewer_enabled,managerial_profile,local_project_key,metadata
                )
                VALUES(:code,:cui,:wvs,:pg,:project_type,true,:profile,:local_key,'{}'::jsonb)
                RETURNING *
            """), {
                "code": code,
                "cui": str(project["cui"]) if project["cui"] is not None else None,
                "wvs": str(project["cod_wvs"]) if project["cod_wvs"] is not None else None,
                "pg": project_guid,
                "project_type": project_type,
                "profile": profile,
                "local_key": local_key,
            }).mappings().first()

        conn.execute(text("""
            UPDATE public.bim_project_models SET is_default=false
            WHERE project_id=:pid AND model_role='MANAGERIAL' AND is_default=true
        """), {"pid": bp["id"]})

        existing = conn.execute(text("""
            SELECT * FROM public.bim_project_models
            WHERE project_id=:pid AND acc_item_id=:item_id AND model_role='MANAGERIAL'
            ORDER BY id LIMIT 1
        """), {"pid": bp["id"], "item_id": item_id}).mappings().first()

        if existing:
            model = conn.execute(text("""
                UPDATE public.bim_project_models
                SET source_filename_cache=:name,
                    acc_folder_id_cache=:folder_id,
                    acc_folder_path_cache=:folder_path,
                    format='NWD', is_default=true, enabled=true, updated_at=now()
                WHERE id=:id RETURNING *
            """), {
                "id": existing["id"], "name": item["item_name"],
                "folder_id": item["folder_id"], "folder_path": item["folder_path_cached"],
            }).mappings().first()
        else:
            digest = hashlib.sha1(item_id.encode("utf-8")).hexdigest()[:10].upper()
            model = conn.execute(text("""
                INSERT INTO public.bim_project_models(
                    project_id,model_code,model_role,acc_item_id,
                    source_filename_cache,acc_folder_id_cache,acc_folder_path_cache,
                    format,is_default,enabled,download_policy,metadata
                )
                VALUES(:pid,:model_code,'MANAGERIAL',:item_id,:name,:folder_id,:folder_path,
                       'NWD',true,true,'ON_DEMAND','{}'::jsonb)
                RETURNING *
            """), {
                "pid": bp["id"], "model_code": f"MANAGERIAL-{digest}", "item_id": item_id,
                "name": item["item_name"], "folder_id": item["folder_id"],
                "folder_path": item["folder_path_cached"],
            }).mappings().first()
        return dict(model)


def get_selected_model(project_guid):
    if not schema_ready():
        return None
    sql = """
    SELECT bp.id AS project_id, bp.project_code, bp.local_project_key, bp.project_type,
           m.id AS model_id, m.model_code, m.acc_item_id,
           i.item_name, i.folder_path_cached, i.latest_version_id,
           i.last_modified, i.is_active,
           rv.derivative_urn, rv.manifest_url, rv.derivative_format,
           rv.default_viewable_guid, rv.status AS remote_status,
           rv.checked_at, rv.last_error AS remote_error,
           lv.id AS local_version_id, lv.acc_version_id AS local_acc_version_id,
           lv.derivative_format AS local_derivative_format,
           lv.local_root, lv.local_entry, lv.status AS local_status,
           lv.is_active AS local_is_active,
           lv.download_started_at, lv.downloaded_at, lv.verified_at,
           CASE
             WHEN COALESCE(i.is_active,false)=false THEN 'ACC_ITEM_INACTIVE'
             WHEN lv.id IS NOT NULL AND lv.acc_version_id=i.latest_version_id
                  AND lv.is_active=true AND lv.status IN ('READY','ACTIVE') THEN 'LOCAL_READY'
             WHEN rv.status='READY' THEN 'REMOTE_READY'
             WHEN rv.status='PROCESSING' THEN 'PROCESSING'
             WHEN rv.status='MISSING' THEN 'SOURCE_ONLY'
             WHEN rv.status='ERROR' THEN 'ERROR'
             WHEN lv.id IS NOT NULL AND lv.acc_version_id IS DISTINCT FROM i.latest_version_id THEN 'UPDATE_AVAILABLE'
             ELSE 'NEED_CHECK'
           END AS viewer_status
    FROM public.bim_projects bp
    JOIN public.bim_project_models m
      ON m.project_id=bp.id AND m.model_role='MANAGERIAL' AND m.is_default=true AND m.enabled=true
    LEFT JOIN public.acc_items i ON i.item_id=m.acc_item_id
    LEFT JOIN public.bim_remote_versions rv
      ON rv.model_id=m.id AND rv.acc_version_id=i.latest_version_id
    LEFT JOIN LATERAL (
      SELECT x.* FROM public.bim_local_versions x
      WHERE x.model_id=m.id
      ORDER BY (x.acc_version_id=i.latest_version_id) DESC,
               x.is_active DESC, x.downloaded_at DESC NULLS LAST, x.id DESC
      LIMIT 1
    ) lv ON true
    WHERE bp.acc_project_guid=:pg
    LIMIT 1
    """
    with models.engine.begin() as conn:
        row = conn.execute(text(sql), {"pg": project_guid}).mappings().first()
    return dict(row) if row else None


def cache_remote_result(model_id, acc_version_id, source_last_modified, result):
    sql = """
    INSERT INTO public.bim_remote_versions(
        model_id,acc_version_id,derivative_urn,manifest_url,derivative_format,
        default_viewable_guid,status,source_last_modified,checked_at,last_error,metadata
    )
    VALUES(:model_id,:acc_version_id,:derivative_urn,:manifest_url,:derivative_format,
           :viewable_guid,:status,:source_last_modified,now(),:last_error,
           jsonb_build_object('manifest_status',CAST(:manifest_status AS text),
                              'manifest_progress',CAST(:manifest_progress AS text)))
    ON CONFLICT(model_id,acc_version_id)
    DO UPDATE SET derivative_urn=EXCLUDED.derivative_urn,
                  manifest_url=EXCLUDED.manifest_url,
                  derivative_format=EXCLUDED.derivative_format,
                  default_viewable_guid=EXCLUDED.default_viewable_guid,
                  status=EXCLUDED.status,
                  source_last_modified=EXCLUDED.source_last_modified,
                  checked_at=now(), last_error=EXCLUDED.last_error,
                  metadata=EXCLUDED.metadata, updated_at=now()
    RETURNING *
    """
    params = {
        "model_id": model_id, "acc_version_id": acc_version_id,
        "derivative_urn": result.get("derivative_urn"),
        "manifest_url": result.get("manifest_url"),
        "derivative_format": result.get("derivative_format"),
        "viewable_guid": result.get("default_viewable_guid"),
        "status": result.get("status", "ERROR"),
        "source_last_modified": source_last_modified,
        "last_error": result.get("last_error"),
        "manifest_status": result.get("manifest_status"),
        "manifest_progress": result.get("manifest_progress"),
    }
    with models.engine.begin() as conn:
        row = conn.execute(text(sql), params).mappings().first()
    return dict(row)


def refresh_selected_remote(project_guid, resolver):
    selected = get_selected_model(project_guid)
    if not selected:
        raise ValueError("No hay modelo gerencial seleccionado.")
    if not selected.get("latest_version_id"):
        raise ValueError("El modelo seleccionado no tiene latest_version_id.")
    result = resolver(project_guid, selected["latest_version_id"])
    cache_remote_result(selected["model_id"], selected["latest_version_id"],
                        selected.get("last_modified"), result)
    return result


def get_viewer_model(project_guid):
    selected = get_selected_model(project_guid)
    if not selected:
        raise ValueError("No hay modelo gerencial seleccionado.")
    if selected.get("remote_status") != "READY" or not selected.get("derivative_urn"):
        raise ValueError("El derivative remoto todavía no está listo.")
    return selected


def mirror_schema_ready():
    sql = """
    SELECT
      to_regclass('public.bim_local_versions') IS NOT NULL
      AND to_regclass('public.bim_local_cache_files') IS NOT NULL AS ok
    """
    with models.engine.begin() as conn:
        return bool(conn.execute(text(sql)).scalar())


def upsert_local_mirror(selected, local_root, local_entry, manifest_path):
    if not mirror_schema_ready():
        raise RuntimeError("MIRROR_SCHEMA_NO_INSTALADO")

    version_id = selected.get("latest_version_id")
    if not version_id:
        raise ValueError("El modelo seleccionado no tiene latest_version_id.")

    match = re.search(r"[?&]version=(\d+)", version_id)
    version_number = int(match.group(1)) if match else None

    sql = """
    INSERT INTO public.bim_local_versions(
        model_id,acc_version_id,version_urn,version_number,viewable_guid,
        derivative_format,local_root,local_entry,manifest_path,status,is_active,
        source_last_modified,download_started_at,last_error,metadata
    )
    VALUES(
        :model_id,:acc_version_id,:version_urn,:version_number,:viewable_guid,
        :derivative_format,:local_root,:local_entry,:manifest_path,'DOWNLOADING',false,
        :source_last_modified,now(),NULL,
        jsonb_build_object('mirror_mode','PROXY_CACHE','offline_ready',false)
    )
    ON CONFLICT(model_id,acc_version_id)
    DO UPDATE SET
        version_urn=EXCLUDED.version_urn,
        version_number=EXCLUDED.version_number,
        viewable_guid=EXCLUDED.viewable_guid,
        derivative_format=EXCLUDED.derivative_format,
        local_root=EXCLUDED.local_root,
        local_entry=EXCLUDED.local_entry,
        manifest_path=EXCLUDED.manifest_path,
        status=CASE
            WHEN public.bim_local_versions.status IN ('READY','ACTIVE')
            THEN public.bim_local_versions.status
            ELSE 'DOWNLOADING'
        END,
        source_last_modified=EXCLUDED.source_last_modified,
        download_started_at=COALESCE(public.bim_local_versions.download_started_at,now()),
        last_error=NULL,
        metadata=public.bim_local_versions.metadata ||
                 jsonb_build_object('mirror_mode','PROXY_CACHE','offline_ready',false),
        updated_at=now()
    RETURNING *
    """

    params = {
        "model_id": selected["model_id"],
        "acc_version_id": version_id,
        "version_urn": version_id,
        "version_number": version_number,
        "viewable_guid": selected.get("default_viewable_guid"),
        "derivative_format": selected.get("derivative_format"),
        "local_root": str(local_root),
        "local_entry": local_entry,
        "manifest_path": str(manifest_path),
        "source_last_modified": selected.get("last_modified"),
    }

    with models.engine.begin() as conn:
        row = conn.execute(text(sql), params).mappings().first()
    return dict(row)


def get_local_mirror(local_version_id):
    if not mirror_schema_ready():
        return None

    sql = """
    SELECT
        lv.*,
        m.acc_item_id,
        bp.acc_project_guid,
        bp.project_code,
        bp.local_project_key,
        i.item_name,
        i.latest_version_id,
        rv.derivative_urn,
        rv.derivative_format AS remote_derivative_format,
        rv.default_viewable_guid,
        rv.status AS remote_status,
        COALESCE(cs.cached_files,0) AS cached_files,
        COALESCE(cs.cached_bytes,0) AS cached_bytes,
        cs.last_cached_at
    FROM public.bim_local_versions lv
    JOIN public.bim_project_models m ON m.id=lv.model_id
    JOIN public.bim_projects bp ON bp.id=m.project_id
    LEFT JOIN public.acc_items i ON i.item_id=m.acc_item_id
    LEFT JOIN public.bim_remote_versions rv
      ON rv.model_id=m.id AND rv.acc_version_id=lv.acc_version_id
    LEFT JOIN LATERAL (
      SELECT COUNT(*) AS cached_files,
             COALESCE(SUM(cf.size_bytes),0) AS cached_bytes,
             MAX(cf.cached_at) AS last_cached_at
      FROM public.bim_local_cache_files cf
      WHERE cf.local_version_id=lv.id
    ) cs ON true
    WHERE lv.id=:id
    LIMIT 1
    """
    with models.engine.begin() as conn:
        row = conn.execute(text(sql), {"id": int(local_version_id)}).mappings().first()
    return dict(row) if row else None


def find_cached_file(local_version_id, cache_key):
    if not mirror_schema_ready():
        return None
    sql = """
    SELECT *
    FROM public.bim_local_cache_files
    WHERE local_version_id=:local_version_id
      AND cache_key=:cache_key
    LIMIT 1
    """
    with models.engine.begin() as conn:
        row = conn.execute(text(sql), {
            "local_version_id": int(local_version_id),
            "cache_key": cache_key,
        }).mappings().first()
        if row:
            conn.execute(text("""
                UPDATE public.bim_local_cache_files
                SET last_accessed_at=now()
                WHERE id=:id
            """), {"id": row["id"]})
    return dict(row) if row else None


def register_cached_file(local_version_id, cache_key, remote_path, range_header,
                         local_relpath, status_code, headers, size_bytes):
    sql = """
    INSERT INTO public.bim_local_cache_files(
        local_version_id,cache_key,remote_path,range_header,local_relpath,
        status_code,content_type,content_encoding,content_range,accept_ranges,
        etag,last_modified,size_bytes,cached_at,last_accessed_at
    )
    VALUES(
        :local_version_id,:cache_key,:remote_path,:range_header,:local_relpath,
        :status_code,:content_type,:content_encoding,:content_range,:accept_ranges,
        :etag,:last_modified,:size_bytes,now(),now()
    )
    ON CONFLICT(local_version_id,cache_key)
    DO UPDATE SET
        remote_path=EXCLUDED.remote_path,
        range_header=EXCLUDED.range_header,
        local_relpath=EXCLUDED.local_relpath,
        status_code=EXCLUDED.status_code,
        content_type=EXCLUDED.content_type,
        content_encoding=EXCLUDED.content_encoding,
        content_range=EXCLUDED.content_range,
        accept_ranges=EXCLUDED.accept_ranges,
        etag=EXCLUDED.etag,
        last_modified=EXCLUDED.last_modified,
        size_bytes=EXCLUDED.size_bytes,
        cached_at=now(),
        last_accessed_at=now()
    RETURNING *
    """
    params = {
        "local_version_id": int(local_version_id),
        "cache_key": cache_key,
        "remote_path": remote_path,
        "range_header": range_header,
        "local_relpath": local_relpath,
        "status_code": int(status_code),
        "content_type": headers.get("Content-Type"),
        "content_encoding": headers.get("Content-Encoding"),
        "content_range": headers.get("Content-Range"),
        "accept_ranges": headers.get("Accept-Ranges"),
        "etag": headers.get("ETag"),
        "last_modified": headers.get("Last-Modified"),
        "size_bytes": int(size_bytes or 0),
    }
    with models.engine.begin() as conn:
        row = conn.execute(text(sql), params).mappings().first()
        conn.execute(text("""
            UPDATE public.bim_local_versions
            SET size_bytes=(
                    SELECT COALESCE(SUM(size_bytes),0)
                    FROM public.bim_local_cache_files
                    WHERE local_version_id=:id
                ),
                file_count=(
                    SELECT COUNT(*)
                    FROM public.bim_local_cache_files
                    WHERE local_version_id=:id
                ),
                updated_at=now()
            WHERE id=:id
        """), {"id": int(local_version_id)})
    return dict(row)


def mark_local_mirror_error(local_version_id, message):
    if not mirror_schema_ready():
        return
    with models.engine.begin() as conn:
        conn.execute(text("""
            UPDATE public.bim_local_versions
            SET last_error=:message, updated_at=now()
            WHERE id=:id
        """), {"id": int(local_version_id), "message": str(message)[:4000]})
