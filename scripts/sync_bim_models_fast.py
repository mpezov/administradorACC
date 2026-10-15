# -*- coding: utf-8 -*-
"""
Sincronización rápida v0.4: catálogo persistente, discovery NWD y Viewer Manager.

Objetivo:
- NO recorrer carpetas ACC.
- Leer únicamente modelos registrados en public.bim_project_models.
- Considerar solo archivos .nwd.
- Consultar directamente la última versión de cada item_id.
- Por defecto: modo diagnóstico (sin escrituras).
- Con --apply: actualizar únicamente columnas reconocidas de la tabla de modelos
  y/o public.bim_remote_versions cuando el esquema sea compatible.

Uso:
  python scripts/sync_bim_models_fast.py
  python scripts/sync_bim_models_fast.py --apply
  python scripts/sync_bim_models_fast.py --limit 10
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Cargar .env desde la raíz del administrador aunque se ejecute desde otro cwd.
ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

# Reutilizamos autenticación, reintentos y rate limit ya probados por sync_inventario.
try:
    from scripts.sync_inventario import client, clean_project_id
except Exception:
    # Si el script se ejecuta directamente desde /scripts
    from sync_inventario import client, clean_project_id


PG = dict(
    host=os.getenv("POSTGRES_HOST", "localhost"),
    port=os.getenv("POSTGRES_PORT", "5432"),
    dbname=os.getenv("POSTGRES_DB", "bpmbimgis"),
    user=os.getenv("POSTGRES_USER"),
    password=os.getenv("POSTGRES_PASSWORD"),
)

MODEL_TABLE = "bim_project_models"
REMOTE_TABLE = "bim_remote_versions"

# Nombres conocidos/razonables. Se detectan dinámicamente para no forzar un esquema.
ALIASES = {
    "model_pk": ["id", "model_id", "project_model_id", "bim_project_model_id"],
    "item_id": ["item_id", "acc_item_id", "model_item_id", "aps_item_id"],
    "project_id": ["project_id", "project_guid", "acc_project_id", "aps_project_id"],
    "name": ["source_filename_cache", "file_name", "filename", "model_name", "name", "display_name"],
    "format": ["format", "file_format", "extension", "ext"],
    "active": ["is_active", "active", "enabled"],
    "latest_version_id": ["latest_version_id", "version_id", "remote_version_id"],
    "version_number": ["version_number", "version_no", "version_index"],
    "last_modified": ["last_modified", "last_modified_time", "remote_last_modified", "updated_at_remote"],
    "derivative_urn": ["derivative_urn", "urn", "version_urn"],
    "updated_at": ["updated_at", "synced_at", "last_sync_at"],
}


def qident(name: str) -> str:
    if not name.replace("_", "").isalnum():
        raise ValueError(f"Identificador no seguro: {name}")
    return '"' + name + '"'


def connect():
    return psycopg2.connect(**PG)


def table_columns(conn, table: str) -> List[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s
            ORDER BY ordinal_position
            """,
            (table,),
        )
        return [r[0] for r in cur.fetchall()]


def first_col(cols: Sequence[str], key: str) -> Optional[str]:
    s = set(cols)
    for c in ALIASES[key]:
        if c in s:
            return c
    return None


def pick_value(row: Dict[str, Any], key: str) -> Any:
    for c in ALIASES[key]:
        if c in row and row[c] not in (None, ""):
            return row[c]
    return None


def looks_nwd(name: Any, fmt: Any = None) -> bool:
    by_name = isinstance(name, str) and name.strip().lower().endswith(".nwd")
    if isinstance(fmt, str):
        f = fmt.strip().lower().lstrip(".")
        by_format = f == "nwd"
    else:
        by_format = False
    return by_name or by_format


def get_models(conn, limit: Optional[int]) -> Tuple[List[Dict[str, Any]], List[str]]:
    cols = table_columns(conn, MODEL_TABLE)
    if not cols:
        raise RuntimeError(f"No existe public.{MODEL_TABLE}")

    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(f"SELECT * FROM public.{qident(MODEL_TABLE)}")
        rows = [dict(r) for r in cur.fetchall()]

    # Filtrar activos si la tabla tiene una columna reconocible.
    active_col = first_col(cols, "active")
    if active_col:
        rows = [r for r in rows if r.get(active_col) is not False]

    # Solo federados NWD. Se usa el nombre cacheado y, como respaldo, format='nwd'.
    name_col = first_col(cols, "name")
    format_col = first_col(cols, "format")
    if name_col or format_col:
        rows = [r for r in rows if looks_nwd(r.get(name_col) if name_col else None, r.get(format_col) if format_col else None)]
    else:
        rows = []

    if limit:
        rows = rows[:limit]
    return rows, cols



def looks_like_acc_project_id(value: Any) -> bool:
    import re
    if value is None:
        return False
    s = str(value).strip()
    if s.startswith("b.") or s.startswith("urn:"):
        return True
    return bool(re.fullmatch(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", s))


def resolve_acc_project_id(conn, project_ref: Any) -> Optional[str]:
    """Resuelve bim_project_models.project_id cuando es FK interno hacia bim_projects."""
    if project_ref is None:
        return None
    raw = str(project_ref).strip()
    if looks_like_acc_project_id(raw):
        return raw

    pcols = table_columns(conn, "bim_projects")
    if not pcols:
        return None
    pk = "id" if "id" in pcols else None
    if not pk:
        return None
    candidates = [
        "acc_project_id", "aps_project_id", "acc_project_guid", "project_guid",
        "acc_id", "aps_id", "project_id_acc", "project_id_aps"
    ]
    select_cols = [c for c in candidates if c in pcols]
    if not select_cols:
        return None
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"SELECT {', '.join(qident(c) for c in select_cols)} FROM public.{qident('bim_projects')} WHERE {qident(pk)}=%s LIMIT 1",
            (project_ref,),
        )
        row = cur.fetchone()
    if not row:
        return None
    for c in select_cols:
        v = row.get(c)
        if v not in (None, "") and looks_like_acc_project_id(v):
            return str(v).strip()
    return None


def local_version_from_remote_table(conn, model_row: Dict[str, Any], model_cols: Sequence[str]) -> Optional[str]:
    """Busca la versión ya registrada en bim_remote_versions cuando no vive en bim_project_models."""
    rcols = table_columns(conn, REMOTE_TABLE)
    if not rcols:
        return None
    version_candidates = ["acc_version_id", "version_id", "remote_version_id", "latest_version_id"]
    version_col = next((c for c in version_candidates if c in rcols), None)
    if not version_col:
        return None

    model_pk = first_col(model_cols, "model_pk")
    model_fk_candidates = ["project_model_id", "model_id", "bim_project_model_id", "project_model_fk", "bim_project_model_fk"]
    model_fk = next((c for c in model_fk_candidates if c in rcols), None)
    item_col_model = first_col(model_cols, "item_id")
    item_candidates = ["acc_item_id", "item_id", "model_item_id", "aps_item_id"]
    item_col_remote = next((c for c in item_candidates if c in rcols), None)

    where = None
    value = None
    if model_fk and model_pk and model_row.get(model_pk) is not None:
        where, value = model_fk, model_row.get(model_pk)
    elif item_col_remote and item_col_model and model_row.get(item_col_model):
        where, value = item_col_remote, model_row.get(item_col_model)
    if not where:
        return None

    order_candidates = ["is_current", "is_latest", "updated_at", "created_at", "version_number", "id"]
    order_sql = ""
    for c in order_candidates:
        if c in rcols:
            if c in ("is_current", "is_latest"):
                order_sql = f" ORDER BY {qident(c)} DESC NULLS LAST"
            else:
                order_sql = f" ORDER BY {qident(c)} DESC NULLS LAST"
            break
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT {qident(version_col)} FROM public.{qident(REMOTE_TABLE)} WHERE {qident(where)}=%s{order_sql} LIMIT 1",
            (value,),
        )
        row = cur.fetchone()
    return str(row[0]) if row and row[0] not in (None, "") else None

def latest_remote_version(project_id: str, item_id: str) -> Optional[Dict[str, Any]]:
    project_id = str(project_id).strip()
    # Data API usa normalmente b.<guid>. Si recibimos GUID limpio, se normaliza.
    if not project_id.startswith("b.") and not project_id.startswith("urn:"):
        project_id = "b." + clean_project_id(project_id)

    url = f"https://developer.api.autodesk.com/data/v1/projects/{project_id}/items/{item_id}/versions"
    # page[limit]=1 evita descargar todo el historial. La API devuelve las versiones recientes primero.
    r = client.request("GET", url, params={"page[limit]": 1})
    data = (r.json() or {}).get("data") or []
    return data[0] if data else None


def version_info(v: Dict[str, Any]) -> Dict[str, Any]:
    attrs = v.get("attributes") or {}
    rels = v.get("relationships") or {}
    derivatives = rels.get("derivatives") or {}
    deriv_data = derivatives.get("data") or {}
    return {
        "version_id": v.get("id"),
        "version_number": attrs.get("versionNumber") or attrs.get("version") or attrs.get("revision"),
        "last_modified": attrs.get("lastModifiedTime") or attrs.get("updatedAt") or attrs.get("createTime"),
        "derivative_urn": deriv_data.get("id") if isinstance(deriv_data, dict) else None,
    }


def known_local_version(row: Dict[str, Any]) -> Optional[str]:
    return pick_value(row, "latest_version_id")


def update_model_row(conn, row: Dict[str, Any], cols: Sequence[str], info: Dict[str, Any]) -> bool:
    pk_col = first_col(cols, "model_pk")
    if not pk_col or row.get(pk_col) is None:
        return False

    assignments: List[str] = []
    params: List[Any] = []

    mapping = [
        ("latest_version_id", info.get("version_id")),
        ("version_number", info.get("version_number")),
        ("last_modified", info.get("last_modified")),
        ("derivative_urn", info.get("derivative_urn")),
    ]
    for key, value in mapping:
        col = first_col(cols, key)
        if col and value is not None:
            assignments.append(f"{qident(col)}=%s")
            params.append(value)

    updated_col = first_col(cols, "updated_at")
    if updated_col:
        assignments.append(f"{qident(updated_col)}=now()")

    if not assignments:
        return False

    params.append(row[pk_col])
    sql = f"UPDATE public.{qident(MODEL_TABLE)} SET {', '.join(assignments)} WHERE {qident(pk_col)}=%s"
    with conn.cursor() as cur:
        cur.execute(sql, params)
    return True


def remote_table_capabilities(conn) -> Tuple[List[str], Optional[str], Optional[str], Optional[str]]:
    cols = table_columns(conn, REMOTE_TABLE)
    if not cols:
        return [], None, None, None
    # Para evitar escrituras ambiguas, solo se usa la tabla si encontramos claves claras.
    model_fk = first_col(cols, "model_pk")
    version_col = first_col(cols, "latest_version_id")
    updated_col = first_col(cols, "updated_at")
    return cols, model_fk, version_col, updated_col


def print_header(apply: bool, model_cols: Sequence[str]) -> None:
    print("=" * 86)
    print("SYNC BIM FAST - SOLO FEDERADOS NWD")
    print("Modo            :", "APPLY (escritura)" if apply else "CHECK (sin escrituras)")
    print("PostgreSQL      :", f"{PG['host']}:{PG['port']}/{PG['dbname']}")
    print("Tabla modelos   :", f"public.{MODEL_TABLE}")
    print("Columnas        :", ", ".join(model_cols))
    print("=" * 86)



# ===================== DESCUBRIMIENTO RECURSIVO NWD =====================
PROJECT_CODE_ALIASES = ["wvs", "project_code", "code", "project_number", "numero_proyecto", "project_no", "number", "wbs"]
PROJECT_GUID_ALIASES = ["acc_project_id", "aps_project_id", "acc_project_guid", "project_guid", "acc_id", "aps_id", "project_id_acc", "project_id_aps"]
HUB_ID_ALIASES = ["hub_id", "acc_hub_id", "aps_hub_id"]


def table_column_meta(conn, table: str) -> Dict[str, Dict[str, Any]]:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT column_name, is_nullable, column_default, data_type, udt_name
            FROM information_schema.columns
            WHERE table_schema='public' AND table_name=%s
            ORDER BY ordinal_position
        """, (table,))
        return {r['column_name']: dict(r) for r in cur.fetchall()}




def choose_download_policy(conn, internal_project_id: Any, meta: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """Choose a schema-compatible download_policy without inventing a value.

    Priority:
    1) If the column has a DB default, return None so INSERT omits the column.
    2) Reuse the most common non-null value from the same project.
    3) Reuse the most common non-null value from the whole table.
    4) If no value can be inferred, return None and let validation decide.
    """
    m = meta.get("download_policy")
    if not m:
        return None
    if m.get("column_default") is not None:
        return None
    with conn.cursor() as cur:
        if internal_project_id is not None and "project_id" in table_columns(conn, MODEL_TABLE):
            cur.execute(
                f"SELECT download_policy, count(*) FROM public.{qident(MODEL_TABLE)} "
                "WHERE project_id=%s AND download_policy IS NOT NULL "
                "GROUP BY download_policy ORDER BY count(*) DESC LIMIT 1",
                (internal_project_id,),
            )
            row = cur.fetchone()
            if row and row[0] not in (None, ""):
                return str(row[0])
        cur.execute(
            f"SELECT download_policy, count(*) FROM public.{qident(MODEL_TABLE)} "
            "WHERE download_policy IS NOT NULL GROUP BY download_policy ORDER BY count(*) DESC LIMIT 1"
        )
        row = cur.fetchone()
        if row and row[0] not in (None, ""):
            return str(row[0])
    return None


def download_policy_constraint(conn) -> Optional[str]:
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT pg_get_constraintdef(c.oid)
            FROM pg_constraint c
            JOIN pg_class t ON t.oid=c.conrelid
            JOIN pg_namespace n ON n.oid=t.relnamespace
            WHERE n.nspname='public'
              AND t.relname=%s
              AND c.contype='c'
              AND c.conname='bim_project_models_download_policy_check'
            LIMIT 1
            """,
            (MODEL_TABLE,),
        )
        row=cur.fetchone()
        return row[0] if row else None


def resolve_project_by_code(conn, project_code: str, project_guid: Optional[str] = None) -> Dict[str, Any]:
    cols = table_columns(conn, "bim_projects")
    if not cols:
        raise RuntimeError("No existe public.bim_projects")
    pk = "id" if "id" in cols else cols[0]

    # 1) Si se entrega GUID, buscarlo directamente.
    if project_guid:
        clean = clean_project_id(str(project_guid))
        for c in PROJECT_GUID_ALIASES:
            if c in cols:
                with conn.cursor(cursor_factory=RealDictCursor) as cur:
                    cur.execute(f"SELECT * FROM public.bim_projects WHERE regexp_replace(CAST({qident(c)} AS text), '^b\\.', '')=%s LIMIT 1", (clean,))
                    row = cur.fetchone()
                    if row:
                        return dict(row)

    # 2) Buscar por código/WVS en columnas conocidas.
    for c in PROJECT_CODE_ALIASES:
        if c in cols:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(f"SELECT * FROM public.bim_projects WHERE CAST({qident(c)} AS text)=%s LIMIT 1", (str(project_code),))
                row = cur.fetchone()
                if row:
                    return dict(row)

    # 3) Intentar por metadata JSON/text si existe.
    if "metadata" in cols:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT * FROM public.bim_projects WHERE CAST(metadata AS text) ILIKE %s LIMIT 1", (f"%{project_code}%",))
            row = cur.fetchone()
            if row:
                return dict(row)

    raise RuntimeError(
        f"No pude resolver el proyecto {project_code} en public.bim_projects. "
        f"Columnas disponibles: {', '.join(cols)}"
    )


def project_internal_id(project_row: Dict[str, Any]) -> Any:
    for c in ("id", "project_id", "bim_project_id"):
        if c in project_row and project_row[c] is not None:
            return project_row[c]
    return None


def project_acc_guid(project_row: Dict[str, Any]) -> Optional[str]:
    for c in PROJECT_GUID_ALIASES:
        v = project_row.get(c)
        if v not in (None, ""):
            return clean_project_id(str(v))
    # En algunos esquemas el project_id ya es ACC.
    for c in ("project_id", "id"):
        v = project_row.get(c)
        if v not in (None, "") and looks_like_acc_project_id(v):
            return clean_project_id(str(v))
    return None


def project_hub_id(conn, project_row: Dict[str, Any], guid: str) -> Optional[str]:
    for c in HUB_ID_ALIASES:
        v = project_row.get(c)
        if v not in (None, ""):
            return str(v)
    # Inventario general suele guardar esta relación.
    if table_columns(conn, "acc_projects"):
        with conn.cursor() as cur:
            cur.execute("SELECT hub_id FROM public.acc_projects WHERE id_guid=%s LIMIT 1", (clean_project_id(guid),))
            r = cur.fetchone()
            if r and r[0]:
                return str(r[0])
    return None


def get_folder_detail(project_id: str, folder_id: str) -> Dict[str, Any]:
    pid = project_id if str(project_id).startswith("b.") else "b." + clean_project_id(project_id)
    url = f"https://developer.api.autodesk.com/data/v1/projects/{pid}/folders/{folder_id}"
    r = client.request("GET", url)
    return r.json().get("data") or {}


def latest_remote_version_light(project_id: str, item_id: str) -> Optional[Dict[str, Any]]:
    return latest_remote_version(project_id, item_id)


def existing_models_for_project(conn, internal_project_id: Any) -> Tuple[Dict[str, Dict[str, Any]], List[Dict[str, Any]], List[str]]:
    cols = table_columns(conn, MODEL_TABLE)
    pcol = first_col(cols, "project_id")
    icol = first_col(cols, "item_id")
    if not pcol or not icol:
        return {}, [], cols
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            f"SELECT * FROM public.{qident(MODEL_TABLE)} WHERE {qident(pcol)}=%s",
            (internal_project_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return {str(r.get(icol)): r for r in rows if r.get(icol)}, rows, cols


def safe_json(v: Any) -> Any:
    try:
        json.dumps(v)
        return v
    except Exception:
        return str(v)


def refresh_existing_model(conn, row: Dict[str, Any], cols: Sequence[str], cand: Dict[str, Any]) -> bool:
    pk = first_col(cols, "model_pk")
    if not pk or row.get(pk) is None:
        return False
    values = {
        "source_filename_cache": cand["name"],
        "acc_folder_id_cache": cand["folder_id"],
        "acc_folder_path_cache": cand["folder_path"],
        "format": "nwd",
        "enabled": True,
    }
    sets, params = [], []
    for c, v in values.items():
        if c in cols:
            sets.append(f"{qident(c)}=%s")
            params.append(v)
    if "updated_at" in cols:
        sets.append("updated_at=now()")
    if not sets:
        return False
    params.append(row[pk])
    with conn.cursor() as cur:
        cur.execute(f"UPDATE public.{qident(MODEL_TABLE)} SET {', '.join(sets)} WHERE {qident(pk)}=%s", params)
    return True


def ensure_candidate_table(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
        CREATE TABLE IF NOT EXISTS public.bim_nwd_candidates (
            id BIGSERIAL PRIMARY KEY,
            project_code TEXT NOT NULL,
            bim_project_id TEXT,
            acc_project_guid TEXT NOT NULL,
            acc_item_id TEXT NOT NULL,
            filename TEXT NOT NULL,
            acc_folder_id TEXT,
            acc_folder_path TEXT,
            remote_version_id TEXT,
            version_number TEXT,
            remote_last_modified TEXT,
            derivative_urn TEXT,
            catalog_status TEXT NOT NULL DEFAULT 'CANDIDATE',
            discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
            UNIQUE(acc_project_guid, acc_item_id)
        )
        """)


def upsert_candidate(conn, project_code: str, internal_project_id: Any, guid: str, cand: Dict[str, Any]) -> None:
    ensure_candidate_table(conn)
    info = cand.get("remote") or {}
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO public.bim_nwd_candidates
              (project_code,bim_project_id,acc_project_guid,acc_item_id,filename,
               acc_folder_id,acc_folder_path,remote_version_id,version_number,
               remote_last_modified,derivative_urn,catalog_status,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
            ON CONFLICT (acc_project_guid,acc_item_id) DO UPDATE SET
              project_code=EXCLUDED.project_code,
              bim_project_id=EXCLUDED.bim_project_id,
              filename=EXCLUDED.filename,
              acc_folder_id=EXCLUDED.acc_folder_id,
              acc_folder_path=EXCLUDED.acc_folder_path,
              remote_version_id=EXCLUDED.remote_version_id,
              version_number=EXCLUDED.version_number,
              remote_last_modified=EXCLUDED.remote_last_modified,
              derivative_urn=EXCLUDED.derivative_urn,
              catalog_status=EXCLUDED.catalog_status,
              updated_at=now()
        """, (
            str(project_code), str(internal_project_id) if internal_project_id is not None else None,
            clean_project_id(guid), str(cand["item_id"]), cand["name"], cand["folder_id"], cand["folder_path"],
            info.get("version_id"), str(info.get("version_number")) if info.get("version_number") is not None else None,
            str(info.get("last_modified")) if info.get("last_modified") is not None else None,
            info.get("derivative_urn"), cand.get("catalog_status", "CANDIDATE")
        ))


def discover_nwds_recursive(conn, project_code: str, project_guid_override: Optional[str], folder_urn: Optional[str], apply: bool, limit: Optional[int]) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    t0 = time.perf_counter()
    prow = resolve_project_by_code(conn, project_code, project_guid_override)
    internal_id = project_internal_id(prow)
    guid = project_acc_guid(prow) or (clean_project_id(project_guid_override) if project_guid_override else None)
    if not guid:
        raise RuntimeError("El proyecto fue encontrado, pero no pude resolver su GUID ACC.")
    aps_pid = "b." + clean_project_id(guid)
    hub_id = project_hub_id(conn, prow, guid)

    existing_by_item, existing_rows, model_cols = existing_models_for_project(conn, internal_id)

    if folder_urn:
        d = get_folder_detail(aps_pid, folder_urn)
        root_name = ((d.get("attributes") or {}).get("displayName") or (d.get("attributes") or {}).get("name") or "CARPETA_INICIAL")
        queue: List[Tuple[str, List[str]]] = [(folder_urn, [root_name])]
    else:
        if not hub_id:
            raise RuntimeError(
                "No pude resolver hub_id. Para escanear desde raíz, el proyecto debe existir en acc_projects "
                "o bim_projects debe tener hub_id. Como alternativa usa --folder-urn."
            )
        try:
            from scripts.sync_inventario import get_top_folders, get_folder_contents
        except Exception:
            from sync_inventario import get_top_folders, get_folder_contents
        roots = get_top_folders(hub_id, aps_pid)
        queue = []
        for root in roots:
            nm = ((root.get("attributes") or {}).get("displayName") or (root.get("attributes") or {}).get("name") or "ROOT")
            queue.append((root["id"], [nm]))

    try:
        from scripts.sync_inventario import get_folder_contents
    except Exception:
        from sync_inventario import get_folder_contents

    seen_folders = set()
    candidates: List[Dict[str, Any]] = []
    items_seen = 0
    folder_errors = 0

    print("=" * 96)
    print("DISCOVERY NWD RECURSIVO")
    print("Proyecto código  :", project_code)
    print("ACC project GUID :", clean_project_id(guid))
    print("BIM project id   :", internal_id)
    print("Inicio           :", folder_urn or "TODAS LAS CARPETAS RAÍZ")
    print("Modo             :", "APPLY" if apply else "CHECK")
    print("=" * 96)

    while queue:
        folder_id, parts = queue.pop(0)
        if folder_id in seen_folders:
            continue
        seen_folders.add(folder_id)
        folder_path = "/" + "/".join(parts)
        try:
            contents = get_folder_contents(aps_pid, folder_id)
        except Exception as e:
            folder_errors += 1
            print(f"[CARPETA ERROR] {folder_path}: {e}")
            continue

        for elem in contents:
            typ = elem.get("type")
            attrs = elem.get("attributes") or {}
            if typ == "folders":
                nm = attrs.get("displayName") or attrs.get("name") or "(sin nombre)"
                queue.append((elem["id"], parts + [nm]))
                continue
            if typ != "items":
                continue

            items_seen += 1
            name = attrs.get("displayName") or attrs.get("name") or ""
            if not (isinstance(name, str) and name.lower().endswith(".nwd")):
                continue

            item_id = elem.get("id")
            cand: Dict[str, Any] = {
                "item_id": item_id,
                "name": name,
                "folder_id": folder_id,
                "folder_path": folder_path,
            }
            old = existing_by_item.get(str(item_id))
            cand["catalog_status"] = "REGISTERED" if old else "NEW_CANDIDATE"

            try:
                v = latest_remote_version_light(aps_pid, str(item_id))
                cand["remote"] = version_info(v) if v else {}
            except Exception as e:
                cand["remote"] = {}
                cand["version_error"] = str(e)

            candidates.append(cand)
            tag = "REGISTRADO" if old else "NUEVO"
            rv = (cand.get("remote") or {}).get("version_number") or "?"
            print(f"[{tag:10}] V{rv!s:>3}  {name}")
            print(f"             {folder_path}")

            if apply:
                try:
                    if old:
                        refresh_existing_model(conn, old, model_cols, cand)
                    upsert_candidate(conn, project_code, internal_id, guid, cand)
                    conn.commit()
                except Exception:
                    conn.rollback()
                    raise

            if limit and len(candidates) >= limit:
                queue.clear()
                break

    elapsed = time.perf_counter() - t0
    new_count = sum(1 for c in candidates if c["catalog_status"] == "NEW_CANDIDATE")
    registered_count = len(candidates) - new_count
    stats = {
        "project_code": str(project_code),
        "acc_project_guid": clean_project_id(guid),
        "bim_project_id": internal_id,
        "folders_scanned": len(seen_folders),
        "folder_errors": folder_errors,
        "items_seen": items_seen,
        "nwd_found": len(candidates),
        "registered": registered_count,
        "new_candidates": new_count,
        "elapsed_seconds": round(elapsed, 3),
        "apply": apply,
    }
    return stats, candidates


def run_discovery(args) -> int:
    with connect() as conn:
        stats, candidates = discover_nwds_recursive(
            conn,
            project_code=args.project_code,
            project_guid_override=args.project_guid,
            folder_urn=args.folder_urn,
            apply=args.apply,
            limit=args.limit,
        )
    print("\n" + "=" * 96)
    print("RESUMEN DISCOVERY NWD")
    print(f"Carpetas recorridas : {stats['folders_scanned']}")
    print(f"Items revisados     : {stats['items_seen']}")
    print(f"NWD encontrados     : {stats['nwd_found']}")
    print(f"Ya registrados      : {stats['registered']}")
    print(f"Nuevos candidatos   : {stats['new_candidates']}")
    print(f"Errores carpeta     : {stats['folder_errors']}")
    print(f"Tiempo total        : {stats['elapsed_seconds']:.2f} s")
    print("=" * 96)

    out = Path(args.json_path or (ROOT / f"sync_bim_discovery_{args.project_code}.json"))
    out.write_text(json.dumps({"stats": stats, "candidates": candidates}, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print("Reporte JSON        :", out)
    if args.apply:
        print("Candidatos BD       : public.bim_nwd_candidates")
        print("NOTA: los NWD nuevos se guardan como candidatos; no se insertan automáticamente en bim_project_models.")
    return 0 if stats['folder_errors'] == 0 else 1



# ===================== V0.4: CATÁLOGO PERSISTENTE Y PROMOCIÓN EXPLÍCITA =====================

def catalog_rows(conn, project_code: str) -> List[Dict[str, Any]]:
    ensure_candidate_table(conn)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """
            SELECT *
            FROM public.bim_nwd_candidates
            WHERE project_code=%s
            ORDER BY acc_folder_path, filename, acc_item_id
            """,
            (str(project_code),),
        )
        return [dict(r) for r in cur.fetchall()]


def candidate_to_cand(row: Dict[str, Any], remote: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "item_id": row.get("acc_item_id"),
        "name": row.get("filename"),
        "folder_id": row.get("acc_folder_id"),
        "folder_path": row.get("acc_folder_path"),
        "catalog_status": row.get("catalog_status") or "CANDIDATE",
        "remote": remote or {
            "version_id": row.get("remote_version_id"),
            "version_number": row.get("version_number"),
            "last_modified": row.get("remote_last_modified"),
            "derivative_urn": row.get("derivative_urn"),
        },
    }


def import_discovery_json(conn, project_code: str, json_path: str, apply: bool) -> int:
    path = Path(json_path)
    if not path.exists():
        raise RuntimeError(f"No existe el JSON: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    candidates = payload.get("candidates") or []
    stats = payload.get("stats") or {}
    source_code = str(stats.get("project_code") or project_code)
    if source_code != str(project_code):
        print(f"ADVERTENCIA: JSON corresponde a proyecto {source_code}; solicitado {project_code}.")

    prow = resolve_project_by_code(conn, project_code, stats.get("acc_project_guid"))
    internal_id = project_internal_id(prow)
    guid = project_acc_guid(prow) or stats.get("acc_project_guid")
    if not guid:
        raise RuntimeError("No se pudo resolver GUID ACC para importar el catálogo.")

    existing_by_item, _, _ = existing_models_for_project(conn, internal_id)
    print("=" * 96)
    print("IMPORTAR DISCOVERY JSON A CATÁLOGO NWD")
    print("Proyecto         :", project_code)
    print("Archivo          :", path)
    print("NWD en JSON      :", len(candidates))
    print("Modo             :", "APPLY" if apply else "CHECK")
    print("=" * 96)

    for c in candidates:
        item_id = str(c.get("item_id") or "")
        if not item_id:
            continue
        c["catalog_status"] = "REGISTERED" if item_id in existing_by_item else "CANDIDATE"
        tag = "REGISTRADO" if item_id in existing_by_item else "CANDIDATO"
        rv = (c.get("remote") or {}).get("version_number") or "?"
        print(f"[{tag:10}] V{str(rv):>3}  {c.get('name') or '(sin nombre)'}")
        print(f"             {c.get('folder_path') or '-'}")
        if apply:
            upsert_candidate(conn, project_code, internal_id, guid, c)
    if apply:
        conn.commit()
        print(f"Catálogo guardado: {len(candidates)} NWD en public.bim_nwd_candidates")
    else:
        print("CHECK: no se escribió en PostgreSQL. Use --apply para importar.")
    return 0


def list_catalog(conn, project_code: str) -> int:
    rows = catalog_rows(conn, project_code)
    print("=" * 112)
    print(f"CATÁLOGO NWD - PROYECTO {project_code}")
    print("=" * 112)
    if not rows:
        print("Sin NWD catalogados. Ejecute --discover --apply o importe el JSON previo.")
        return 0
    for i, r in enumerate(rows, 1):
        print(f"[{i:02}] {r.get('catalog_status','CANDIDATE'):<12} V{str(r.get('version_number') or '?'):>3}  {r.get('filename')}")
        print(f"     item : {r.get('acc_item_id')}")
        print(f"     path : {r.get('acc_folder_path') or '-'}")
    print("-" * 112)
    print(f"Total: {len(rows)} NWD")
    return 0


def run_catalog_fast(conn, project_code: str, apply: bool, limit: Optional[int], json_path: Optional[str]) -> int:
    started = time.perf_counter()
    rows = catalog_rows(conn, project_code)
    if limit:
        rows = rows[:limit]
    if not rows:
        print(f"No hay NWD catalogados para {project_code}.")
        print(f"Primero ejecute: python run_sync_bim_fast.py --discover --project-code {project_code} --apply")
        print("o importe un JSON de discovery previo con --import-discovery-json.")
        return 2

    prow = resolve_project_by_code(conn, project_code)
    internal_id = project_internal_id(prow)
    guid = project_acc_guid(prow)
    if not guid:
        raise RuntimeError("No pude resolver el GUID ACC del proyecto.")
    existing_by_item, _, model_cols = existing_models_for_project(conn, internal_id)

    stats = {"revisados":0, "sin_cambios":0, "cambiados":0, "modelos_registrados":0, "candidatos":0, "errores":0}
    report=[]
    print("=" * 96)
    print("SYNC BIM FAST V0.4 - CATÁLOGO NWD")
    print("Proyecto         :", project_code)
    print("ACC project GUID :", guid)
    print("NWD catalogados  :", len(rows))
    print("Modo             :", "APPLY" if apply else "CHECK")
    print("=" * 96)

    for idx, row in enumerate(rows, 1):
        item_id = str(row.get("acc_item_id") or "")
        name = row.get("filename") or "(sin nombre)"
        old_vid = row.get("remote_version_id")
        registered = existing_by_item.get(item_id)
        stats["modelos_registrados" if registered else "candidatos"] += 1
        entry={"item_id":item_id, "name":name, "folder_path":row.get("acc_folder_path"), "registered":bool(registered), "local_version_id":old_vid}
        try:
            v=latest_remote_version(guid,item_id)
            if not v:
                raise RuntimeError("ACC no devolvió versiones")
            info=version_info(v)
            new_vid=info.get("version_id")
            stats["revisados"] += 1
            changed = str(old_vid or "") != str(new_vid or "")
            if changed:
                stats["cambiados"] += 1
                status="NUEVA VERSION"
            else:
                stats["sin_cambios"] += 1
                status="SIN CAMBIOS"
            regtag="REG" if registered else "CAND"
            print(f"[{idx:03}/{len(rows):03}] {status:<13} [{regtag}] V{str(info.get('version_number') or '?'):>3}  {name}")
            if changed:
                print(f"                  catálogo: {old_vid or '-'}")
                print(f"                  ACC     : {new_vid or '-'}")
            entry.update(status=status.replace(" ","_"), remote=info)
            if apply:
                cand=candidate_to_cand(row, info)
                cand["catalog_status"] = "REGISTERED" if registered else "CANDIDATE"
                upsert_candidate(conn, project_code, internal_id, guid, cand)
                if registered:
                    refresh_existing_model(conn, registered, model_cols, cand)
                    update_model_row(conn, registered, model_cols, info)
                conn.commit()
                entry["applied"] = True
            report.append(entry)
        except Exception as e:
            conn.rollback()
            stats["errores"] += 1
            entry.update(status="ERROR", reason=str(e))
            report.append(entry)
            print(f"[{idx:03}/{len(rows):03}] ERROR         {name}: {e}")

    elapsed=time.perf_counter()-started
    print("\n"+"="*96)
    print("RESUMEN FAST NWD CATALOG")
    print(f"Revisados          : {stats['revisados']}")
    print(f"Sin cambios        : {stats['sin_cambios']}")
    print(f"Con nueva versión  : {stats['cambiados']}")
    print(f"Registrados visor  : {stats['modelos_registrados']}")
    print(f"Solo candidatos    : {stats['candidatos']}")
    print(f"Errores            : {stats['errores']}")
    print(f"Tiempo total       : {elapsed:.2f} s")
    if stats["revisados"]:
        print(f"Promedio/NWD       : {elapsed/stats['revisados']:.2f} s")
    print("="*96)
    out=Path(json_path or (ROOT/f"sync_bim_fast_{project_code}_last.json"))
    out.write_text(json.dumps({"stats":stats,"elapsed_seconds":elapsed,"models":report}, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    print("Reporte JSON       :",out)
    return 0 if stats["errores"]==0 else 1


def _column_required_without_default(meta: Dict[str, Dict[str, Any]], col: str) -> bool:
    m=meta.get(col) or {}
    return m.get("is_nullable")=="NO" and m.get("column_default") is None


def promote_candidate(conn, project_code: str, item_id: str, apply: bool, model_code: Optional[str], model_role: str, set_default: bool) -> int:
    ensure_candidate_table(conn)
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM public.bim_nwd_candidates WHERE project_code=%s AND acc_item_id=%s LIMIT 1", (str(project_code), str(item_id)))
        cand=cur.fetchone()
    if not cand:
        raise RuntimeError(f"No existe el item {item_id} en el catálogo del proyecto {project_code}.")
    cand=dict(cand)
    prow=resolve_project_by_code(conn, project_code, cand.get("acc_project_guid"))
    internal_id=project_internal_id(prow)
    existing_by_item, _, cols=existing_models_for_project(conn, internal_id)
    if str(item_id) in existing_by_item:
        print("El NWD ya está registrado en bim_project_models; no se insertará duplicado.")
        if apply:
            c=candidate_to_cand(cand)
            refresh_existing_model(conn, existing_by_item[str(item_id)], cols, c)
            conn.commit()
            print("Cache de ruta/nombre actualizado.")
        return 0

    suggested=model_code or Path(cand.get("filename") or "modelo_nwd").stem
    print("="*96)
    print("PROMOCIÓN EXPLÍCITA DE NWD")
    print("Proyecto    :", project_code)
    print("Item ID     :", item_id)
    print("Archivo     :", cand.get("filename"))
    print("Ruta        :", cand.get("acc_folder_path"))
    print("Versión     :", cand.get("version_number"))
    print("Model code  :", suggested)
    print("Model role  :", model_role)
    print("Default     :", set_default)
    print("Modo        :", "APPLY" if apply else "CHECK")
    print("="*96)
    if not apply:
        print("CHECK: no se insertó nada. Para promover use --apply y, de preferencia, --model-code.")
        return 0
    if not model_code:
        raise RuntimeError("Por seguridad, --apply para promoción requiere --model-code explícito.")

    meta=table_column_meta(conn, MODEL_TABLE)
    vals: Dict[str, Any]={}
    def put(c,v):
        if c in cols: vals[c]=v
    put("project_id", internal_id)
    put("model_code", model_code)
    put("model_role", model_role)
    put("acc_item_id", str(item_id))
    put("source_filename_cache", cand.get("filename"))
    put("acc_folder_id_cache", cand.get("acc_folder_id"))
    put("acc_folder_path_cache", cand.get("acc_folder_path"))
    put("format", "nwd")
    put("is_default", bool(set_default))
    put("enabled", True)
    # Nunca imponer un valor fijo: respetar el esquema real de cada instalación.
    policy = choose_download_policy(conn, internal_id, meta)
    if policy is not None:
        put("download_policy", policy)
        print("Download policy inferido:", policy)
    elif "download_policy" in cols:
        cdef = download_policy_constraint(conn)
        if meta.get("download_policy", {}).get("column_default") is not None:
            print("Download policy     : se usará DEFAULT de PostgreSQL")
        else:
            print("Download policy     : no se enviará valor explícito")
            if cdef:
                print("CHECK detectado     :", cdef)
    put("notes", "Promovido explícitamente desde bim_nwd_candidates por sync_bim_fast v0.4")

    covered=set(vals) | {c for c,m in meta.items() if m.get("column_default") is not None} | {c for c,m in meta.items() if m.get("is_nullable")=="YES"}
    missing=[c for c in cols if _column_required_without_default(meta,c) and c not in covered and c not in ("id","created_at","updated_at")]
    if missing:
        raise RuntimeError("No puedo promover de forma segura; faltan valores para columnas obligatorias: "+", ".join(missing))

    col_sql=[]; ph=[]; params=[]
    for c,v in vals.items():
        col_sql.append(qident(c)); ph.append("%s"); params.append(v)
    if "created_at" in cols and _column_required_without_default(meta,"created_at"):
        col_sql.append(qident("created_at")); ph.append("now()")
    if "updated_at" in cols and _column_required_without_default(meta,"updated_at"):
        col_sql.append(qident("updated_at")); ph.append("now()")
    with conn.cursor() as cur:
        cur.execute(f"INSERT INTO public.{qident(MODEL_TABLE)} ({', '.join(col_sql)}) VALUES ({', '.join(ph)}) RETURNING id", params)
        new_id=cur.fetchone()[0]
        cur.execute("UPDATE public.bim_nwd_candidates SET catalog_status='REGISTERED', updated_at=now() WHERE project_code=%s AND acc_item_id=%s", (str(project_code), str(item_id)))
    conn.commit()
    print(f"PROMOVIDO: bim_project_models.id={new_id}")
    print("NOTA: la promoción registra el modelo; no cambia automáticamente otros modelos a is_default=false.")
    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Aplica cambios compatibles; en discovery guarda candidatos y refresca cache de registrados")
    ap.add_argument("--discover", action="store_true", help="Alias recomendado de --scan-folders: descubrimiento recursivo de nuevos NWD")
    ap.add_argument("--list-candidates", action="store_true", help="Lista el catálogo NWD persistente del proyecto")
    ap.add_argument("--import-discovery-json", default=None, help="Importa al catálogo un sync_bim_discovery_<proyecto>.json ya generado, sin reescanear ACC")
    ap.add_argument("--promote-item", default=None, help="Promueve explícitamente un acc_item_id del catálogo hacia bim_project_models")
    ap.add_argument("--model-code", default=None, help="Código de modelo requerido al promover con --apply")
    ap.add_argument("--model-role", default="federated", help="Rol al promover; por defecto federated")
    ap.add_argument("--set-default", action="store_true", help="Marca is_default=true al promover; no desmarca otros automáticamente")
    ap.add_argument("--scan-folders", action="store_true", help="Recorre carpetas ACC recursivamente buscando solo .nwd")
    ap.add_argument("--project-code", default=None, help="Código/WVS del proyecto, por ejemplo 100021")
    ap.add_argument("--project-guid", default=None, help="GUID ACC opcional; respaldo si el catálogo no lo expone")
    ap.add_argument("--folder-urn", default=None, help="URN de carpeta ACC desde la cual iniciar el recorrido; si se omite, recorre raíces")
    ap.add_argument("--limit", type=int, default=None, help="Limita cantidad de federados para una prueba corta")
    ap.add_argument("--json", dest="json_path", default=None, help="Guarda reporte JSON")
    args = ap.parse_args(argv)

    if args.discover:
        args.scan_folders = True

    if args.import_discovery_json:
        if not args.project_code:
            ap.error("--import-discovery-json requiere --project-code")
        with connect() as conn:
            return import_discovery_json(conn, args.project_code, args.import_discovery_json, args.apply)

    if args.list_candidates:
        if not args.project_code:
            ap.error("--list-candidates requiere --project-code")
        with connect() as conn:
            return list_catalog(conn, args.project_code)

    if args.promote_item:
        if not args.project_code:
            ap.error("--promote-item requiere --project-code")
        with connect() as conn:
            return promote_candidate(conn, args.project_code, args.promote_item, args.apply, args.model_code, args.model_role, args.set_default)

    if args.scan_folders:
        if not args.project_code:
            ap.error("--discover/--scan-folders requiere --project-code")
        return run_discovery(args)

    # V0.4: cuando se entrega --project-code, el modo normal usa el catálogo persistente
    # y NO vuelve a recorrer carpetas.
    if args.project_code:
        with connect() as conn:
            return run_catalog_fast(conn, args.project_code, args.apply, args.limit, args.json_path)

    started = time.perf_counter()
    stats = {"revisados": 0, "sin_cambios": 0, "cambiados": 0, "actualizados": 0, "omitidos": 0, "errores": 0}
    report: List[Dict[str, Any]] = []

    with connect() as conn:
        models, model_cols = get_models(conn, args.limit)
        print_header(args.apply, model_cols)

        item_col = first_col(model_cols, "item_id")
        project_col = first_col(model_cols, "project_id")
        name_col = first_col(model_cols, "name")

        if not item_col or not project_col:
            print("ERROR: para sincronización directa se necesitan columnas de item_id y project_id.")
            print("       No se modificó nada.")
            print("       Columnas disponibles:", ", ".join(model_cols))
            return 2

        format_col = first_col(model_cols, "format")
        if not name_col and not format_col:
            print("ADVERTENCIA: no se encontró nombre de archivo ni columna format reconocible.")
            print("             Por seguridad no se procesará ningún registro como NWD.")
            return 2

        print(f"Columna nombre   : {name_col or '-'}")
        print(f"Columna formato  : {format_col or '-'}")
        print(f"Federados NWD encontrados: {len(models)}\n")

        for idx, row in enumerate(models, 1):
            name = str(row.get(name_col) or "")
            item_id = row.get(item_col)
            project_ref = row.get(project_col)
            project_id = resolve_acc_project_id(conn, project_ref)
            local_vid = known_local_version(row) or local_version_from_remote_table(conn, row, model_cols)
            entry: Dict[str, Any] = {"name": name, "item_id": item_id, "project_ref": project_ref, "project_id": project_id, "local_version_id": local_vid}

            if not item_id or not project_ref:
                stats["omitidos"] += 1
                entry.update(status="OMITIDO", reason="falta item_id/project_id")
                report.append(entry)
                print(f"[{idx:03}/{len(models):03}] OMITIDO      {name}  (sin item_id/project_id)")
                continue
            if not project_id:
                stats["omitidos"] += 1
                entry.update(status="OMITIDO", reason="no se pudo resolver acc_project_id desde bim_projects")
                report.append(entry)
                print(f"[{idx:03}/{len(models):03}] OMITIDO      {name}  (project_id interno={project_ref}; no pude resolver GUID ACC)")
                continue

            try:
                v = latest_remote_version(str(project_id), str(item_id))
                if not v:
                    stats["errores"] += 1
                    entry.update(status="ERROR", reason="ACC no devolvió versiones")
                    report.append(entry)
                    print(f"[{idx:03}/{len(models):03}] ERROR        {name}  (sin versiones)")
                    continue

                info = version_info(v)
                remote_vid = info.get("version_id")
                stats["revisados"] += 1
                entry.update(remote_version_id=remote_vid, remote=info)

                if local_vid and str(local_vid) == str(remote_vid):
                    stats["sin_cambios"] += 1
                    entry["status"] = "SIN_CAMBIOS"
                    print(f"[{idx:03}/{len(models):03}] SIN CAMBIOS  {name}")
                    report.append(entry)
                    continue

                stats["cambiados"] += 1
                entry["status"] = "NUEVA_VERSION"
                print(f"[{idx:03}/{len(models):03}] CAMBIO       {name}")
                print(f"                  local : {local_vid or '-'}")
                print(f"                  ACC   : {remote_vid or '-'}")

                if args.apply:
                    if update_model_row(conn, row, model_cols, info):
                        conn.commit()
                        stats["actualizados"] += 1
                        entry["applied"] = True
                        print("                  BD    : ACTUALIZADA")
                    else:
                        conn.rollback()
                        entry["applied"] = False
                        entry["apply_reason"] = "esquema no tiene columnas actualizables reconocidas"
                        print("                  BD    : NO MODIFICADA (esquema no reconocido para escritura)")

                report.append(entry)

            except Exception as e:
                conn.rollback()
                stats["errores"] += 1
                entry.update(status="ERROR", reason=str(e))
                report.append(entry)
                print(f"[{idx:03}/{len(models):03}] ERROR        {name}: {e}")

    elapsed = time.perf_counter() - started
    print("\n" + "=" * 86)
    print("RESUMEN")
    print(f"Revisados       : {stats['revisados']}")
    print(f"Sin cambios     : {stats['sin_cambios']}")
    print(f"Con cambio      : {stats['cambiados']}")
    print(f"Actualizados BD : {stats['actualizados']}")
    print(f"Omitidos        : {stats['omitidos']}")
    print(f"Errores         : {stats['errores']}")
    print(f"Tiempo total    : {elapsed:.2f} s")
    if stats["revisados"]:
        print(f"Promedio/modelo : {elapsed / stats['revisados']:.2f} s")
    print("=" * 86)

    json_path = args.json_path or str(ROOT / "sync_bim_fast_last.json")
    try:
        Path(json_path).write_text(json.dumps({"stats": stats, "elapsed_seconds": elapsed, "models": report}, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        print("Reporte JSON    :", json_path)
    except Exception as e:
        print("No se pudo guardar reporte JSON:", e)

    return 0 if stats["errores"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
