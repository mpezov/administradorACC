# app/utils/db.py
from sqlalchemy import text, inspect
from typing import List, Dict, Optional, Tuple
from dataclasses import dataclass

from .. import models

@dataclass
class Project:
    project_guid: str
    hub_name: str
    name: str

@dataclass
class Folder:
    folder_id: str
    parent_id: Optional[str]
    label: str          # Nombre de la carpeta (no la ruta completa)
    folder_path: Optional[str]  # Ruta completa (opcional)

def list_projects() -> List[Project]:
    query = """
    SELECT
      f.project_guid AS project_guid,
      COALESCE(p.hub_name, '') AS hub_name,
      COALESCE(p.name, '[SIN METADATA EN acc_projects]') AS name
    FROM (
      SELECT DISTINCT project_guid
      FROM public.acc_folders
    ) f
    LEFT JOIN public.acc_projects p
      ON p.id_guid = f.project_guid
    ORDER BY hub_name, name, project_guid;
    """
    with models.engine.begin() as conn:
        rows = conn.execute(text(query)).mappings().all()
    return [Project(r["project_guid"], r["hub_name"], r["name"]) for r in rows]

def acc_folders_has_name_column() -> bool:
    insp = inspect(models.engine)
    cols = [c["name"] for c in insp.get_columns("acc_folders", schema="public")]
    return "name" in cols

_folders_has_name = None

def list_folders(project_guid: str) -> List[Folder]:
    global _folders_has_name
    if _folders_has_name is None:
        _folders_has_name = acc_folders_has_name_column()

    if _folders_has_name:
        query = """
        SELECT folder_id, parent_id, COALESCE(name,'') AS name, folder_path
        FROM public.acc_folders
        WHERE project_guid = :pg;
        """
        with models.engine.begin() as conn:
            rows = conn.execute(text(query), {"pg": project_guid}).mappings().all()
        out = []
        for r in rows:
            fid = r.get("folder_id")
            pid = r.get("parent_id")
            name = (r.get("name") or "").strip()
            label = name if name else "(sin nombre)"
            fpath = r.get("folder_path")
            if not fid:
                continue
            out.append(Folder(folder_id=fid, parent_id=pid, label=label, folder_path=fpath))
    else:
        query = """
        SELECT folder_id, parent_id, folder_path
        FROM public.acc_folders
        WHERE project_guid = :pg;
        """
        with models.engine.begin() as conn:
            rows = conn.execute(text(query), {"pg": project_guid}).mappings().all()
        out = []
        for r in rows:
            fid = r.get("folder_id")
            pid = r.get("parent_id")
            fpath = r.get("folder_path") or ""
            # Extraer el nombre base de la ruta (último segmento después de '/')
            if fpath:
                # Ejemplo: "/Project Files/Subcarpeta" -> "Subcarpeta"
                # Para la raíz (solo "/Project Files") -> "Project Files"
                segments = fpath.strip('/').split('/')
                label = segments[-1] if segments else "(sin nombre)"
            else:
                label = "(sin nombre)"
            if not fid:
                continue
            out.append(Folder(folder_id=fid, parent_id=pid, label=label, folder_path=fpath))
    return out

def build_tree(folders: List[Folder]) -> Tuple[Dict[Optional[str], List[str]], Dict[str, Folder]]:
    by_id = {f.folder_id: f for f in folders}
    children: Dict[Optional[str], List[str]] = {}
    for f in folders:
        children.setdefault(f.parent_id, []).append(f.folder_id)
    for pid in children:
        children[pid].sort(key=lambda fid: (by_id[fid].label or "").lower())
    return children, by_id

def get_flags(project_guid: str, folder_id: str) -> Tuple[bool, bool]:
    query = """
    SELECT tipo_gis, incidencias
    FROM public.acc_utils_folder
    WHERE project_guid = :pg AND folder_id = :fid;
    """
    with models.engine.begin() as conn:
        row = conn.execute(text(query), {"pg": project_guid, "fid": folder_id}).mappings().first()
    if not row:
        return False, False
    return bool(row["tipo_gis"]), bool(row["incidencias"])

def upsert_flags(project_guid: str, folder_id: str, tipo_gis: bool, incidencias: bool):
    query = """
    INSERT INTO public.acc_utils_folder (project_guid, folder_id, tipo_gis, incidencias, updated_at)
    VALUES (:pg, :fid, :tg, :inc, now())
    ON CONFLICT (project_guid, folder_id)
    DO UPDATE SET
      tipo_gis = EXCLUDED.tipo_gis,
      incidencias = EXCLUDED.incidencias,
      updated_at = now();
    """
    with models.engine.begin() as conn:
        conn.execute(text(query), {"pg": project_guid, "fid": folder_id, "tg": tipo_gis, "inc": incidencias})

def compute_name_path(folder_id: str, by_id: Dict[str, Folder]) -> str:
    parts = []
    cur = folder_id
    seen = set()
    while cur and cur not in seen:
        seen.add(cur)
        f = by_id.get(cur)
        if not f:
            break
        parts.append(f.label)
        cur = f.parent_id
    parts.reverse()
    return " / ".join(parts) if parts else "(sin ruta)"
# ... (para estadisticas)

def get_stats_summary():
    """Devuelve un diccionario con totales generales."""
    with models.engine.begin() as conn:
        projects = conn.execute(text("SELECT COUNT(DISTINCT project_guid) FROM acc_folders")).scalar() or 0
        folders = conn.execute(text("SELECT COUNT(*) FROM acc_folders")).scalar() or 0
        items = conn.execute(text("SELECT COUNT(*) FROM acc_items")).scalar() or 0
        versions = conn.execute(text("SELECT COUNT(*) FROM acc_item_versions")).scalar() or 0
    return {
        "projects": projects,
        "folders": folders,
        "items": items,
        "versions": versions
    }

def get_stats_by_extension(limit=10):
    """Devuelve lista de (ext, count) ordenada descendente."""
    with models.engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT ext, COUNT(*) as total
                FROM acc_items
                WHERE ext IS NOT NULL AND ext != ''
                GROUP BY ext
                ORDER BY total DESC
                LIMIT :lim
            """),
            {"lim": limit}
        ).fetchall()
    return [{"ext": r[0], "count": r[1]} for r in rows]

def get_top_items_by_versions(limit=20):
    """Devuelve los items con más versiones."""
    with models.engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT i.project_guid, p.name as project_name, i.folder_path_cached, i.item_name, i.version_count
                FROM acc_items i
                LEFT JOIN acc_projects p ON i.project_guid = p.id_guid
                WHERE i.version_count > 1
                ORDER BY i.version_count DESC
                LIMIT :lim
            """),
            {"lim": limit}
        ).mappings().all()
    return [dict(r) for r in rows]

def get_top_projects_by_items(limit=10):
    """Proyectos con más archivos."""
    with models.engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT i.project_guid, p.name as project_name, COUNT(*) as total
                FROM acc_items i
                LEFT JOIN acc_projects p ON i.project_guid = p.id_guid
                GROUP BY i.project_guid, p.name
                ORDER BY total DESC
                LIMIT :lim
            """),
            {"lim": limit}
        ).mappings().all()
    return [dict(r) for r in rows]

def get_top_folders_by_items(limit=20):
    """Carpetas con más archivos (incluye proyecto y ruta)."""
    with models.engine.begin() as conn:
        rows = conn.execute(
            text("""
                SELECT f.project_guid, p.name as project_name, f.folder_id, f.folder_path, COUNT(i.item_id) as total
                FROM acc_folders f
                LEFT JOIN acc_items i ON f.folder_id = i.folder_id
                LEFT JOIN acc_projects p ON f.project_guid = p.id_guid
                GROUP BY f.project_guid, p.name, f.folder_id, f.folder_path
                ORDER BY total DESC
                LIMIT :lim
            """),
            {"lim": limit}
        ).mappings().all()
    return [dict(r) for r in rows]

def list_items_for_folder(project_guid: str, folder_id: str, limit: int = 500):
    query = """
    SELECT
        item_id,
        project_guid,
        folder_id,
        item_name,
        ext,
        version_count,
        latest_version_id,
        last_modified,
        size_bytes,
        uploader,
        naming_ok,
        is_orphan
    FROM public.acc_items
    WHERE project_guid = :pg
      AND folder_id = :fid
    ORDER BY item_name
    LIMIT :lim;
    """
    with models.engine.begin() as conn:
        rows = conn.execute(
            text(query),
            {"pg": project_guid, "fid": folder_id, "lim": limit}
        ).mappings().all()
    return [dict(r) for r in rows]