import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import unquote

import requests

from .aps_viewer import APS_BASE, internal_token
from .viewer_db import (
    get_local_mirror,
    find_cached_file,
    register_cached_file,
    mark_local_mirror_error,
    upsert_local_mirror,
)

APP_ROOT = Path(__file__).resolve().parents[2]


def _data_root():
    configured = (os.getenv("BIM_LOCAL_DATA_ROOT") or "").strip()
    if configured:
        return Path(configured)
    return APP_ROOT / "data" / "projects"


def _safe_key(value):
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value or "").strip())
    return value.strip("._") or "PROJECT"


def _version_folder(version_id):
    match = re.search(r"[?&]version=(\d+)", str(version_id or ""))
    if match:
        return f"v{int(match.group(1)):06d}"
    digest = hashlib.sha1(str(version_id).encode("utf-8")).hexdigest()[:10]
    return f"v_{digest}"


def _atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)


def _download_manifest(selected, target_path):
    url = selected.get("manifest_url")
    urn = selected.get("derivative_urn")
    if not url and urn:
        url = f"{APS_BASE}/modelderivative/v2/designdata/{urn}/manifest"
    if not url:
        raise RuntimeError("El modelo seleccionado no tiene manifest_url ni derivative_urn.")

    r = requests.get(
        url,
        headers={
            "Authorization": f"Bearer {internal_token()}",
            "Accept": "application/json",
        },
        timeout=90,
    )
    r.raise_for_status()
    payload = r.json()
    _atomic_json(target_path, payload)
    return payload


def prepare_local_mirror(selected, project_guid):
    if not selected:
        raise ValueError("No hay modelo gerencial seleccionado.")
    if selected.get("remote_status") != "READY":
        raise ValueError("El derivative remoto debe estar READY antes de preparar el espejo.")

    fmt = str(selected.get("derivative_format") or "").upper()
    if fmt != "SVF":
        raise ValueError(
            f"El derivative actual es {fmt or 'desconocido'}. "
            "La v0.5 habilita espejo local únicamente para SVF clásico. "
            "SVF2 continúa usando el visor remoto."
        )

    version_id = selected.get("latest_version_id")
    if not version_id:
        raise ValueError("No existe latest_version_id.")

    local_project_key = _safe_key(selected.get("local_project_key") or selected.get("project_code"))
    version_dir = (
        _data_root()
        / local_project_key
        / "models"
        / "managerial"
        / _version_folder(version_id)
    )
    cache_dir = version_dir / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = version_dir / "aps_manifest.json"
    manifest = _download_manifest(selected, manifest_path)

    mirror_meta = {
        "schema": "anin.viewer.mirror.v0.5",
        "project_guid": project_guid,
        "project_code": selected.get("project_code"),
        "model_id": selected.get("model_id"),
        "acc_item_id": selected.get("acc_item_id"),
        "acc_version_id": version_id,
        "derivative_urn": selected.get("derivative_urn"),
        "derivative_format": fmt,
        "viewable_guid": selected.get("default_viewable_guid"),
        "mirror_mode": "PROXY_CACHE",
        "offline_ready": False,
        "note": (
            "El cache crece a medida que Viewer solicita recursos. "
            "DOWNLOADING no implica error; significa espejo en calentamiento."
        ),
    }
    _atomic_json(version_dir / "mirror.json", mirror_meta)

    local_entry = f"/admin/project/{project_guid}/viewer-mirror"
    row = upsert_local_mirror(
        selected,
        local_root=version_dir,
        local_entry=local_entry,
        manifest_path=manifest_path,
    )
    return {
        "local_version": row,
        "local_root": str(version_dir),
        "manifest_path": str(manifest_path),
        "manifest_status": manifest.get("status"),
    }


def _cache_key(remote_path, range_header):
    identity = remote_path + "\n" + (range_header or "")
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _cached_path(local_root, cache_key):
    rel = Path("cache") / cache_key[:2] / f"{cache_key}.bin"
    abs_path = Path(local_root) / rel
    return rel, abs_path


def _response_headers(headers):
    keep = {}
    for key in (
        "Content-Type",
        "Content-Encoding",
        "Content-Range",
        "Accept-Ranges",
        "ETag",
        "Last-Modified",
        "Cache-Control",
        "Content-Length",
    ):
        value = headers.get(key)
        if value:
            keep[key] = value
    return keep


def _upstream_url(proxy_path, query_string):
    path = str(proxy_path or "").lstrip("/")
    decoded_for_check = unquote(path)
    if ".." in decoded_for_check.split("/"):
        raise ValueError("Ruta de proxy inválida.")
    allowed = (
        path.startswith("derivativeservice/v2/")
        or path.startswith("modelderivative/v2/")
        or path.startswith("modeldata/")
    )
    if not allowed:
        raise ValueError(
            f"Ruta fuera del servicio de derivatives: {path}"
        )

    # APS Viewer can request two families of resources:
    # - derivativeservice/v2/... for classic SVF/derivative traffic
    # - modeldata/... for streaming/SVF2 traffic
    # Both are served by Autodesk's derivative CDN.
    # Model Derivative REST endpoints remain on developer.api.autodesk.com.
    if (
        path.startswith("derivativeservice/v2/")
        or path.startswith("modeldata/")
    ):
        host = "https://cdn.derivative.autodesk.com"
    else:
        host = "https://developer.api.autodesk.com"

    url = f"{host}/{path}"
    if query_string:
        url += "?" + query_string
    return url


def get_cached_or_fetch(local_version_id, proxy_path, query_string="", range_header=None, method="GET"):
    mirror = get_local_mirror(local_version_id)
    if not mirror:
        raise ValueError("Espejo local no encontrado.")

    fmt = str(mirror.get("remote_derivative_format") or mirror.get("derivative_format") or "").upper()
    if fmt != "SVF":
        raise ValueError("Este mirror cache v0.5 solo admite derivatives SVF.")

    local_root = Path(mirror["local_root"])
    local_root.mkdir(parents=True, exist_ok=True)

    remote_path = str(proxy_path or "").lstrip("/")
    key = _cache_key(remote_path, range_header)

    if method.upper() == "GET":
        cached = find_cached_file(local_version_id, key)
        if cached:
            local_file = local_root / cached["local_relpath"]
            if local_file.exists() and local_file.is_file():
                return {
                    "source": "CACHE",
                    "path": local_file,
                    "status_code": int(cached.get("status_code") or 200),
                    "headers": {
                        k: v for k, v in {
                            "Content-Type": cached.get("content_type"),
                            "Content-Encoding": cached.get("content_encoding"),
                            "Content-Range": cached.get("content_range"),
                            "Accept-Ranges": cached.get("accept_ranges"),
                            "ETag": cached.get("etag"),
                            "Last-Modified": cached.get("last_modified"),
                        }.items() if v
                    },
                }

    url = _upstream_url(remote_path, query_string)
    req_headers = {
        "Authorization": f"Bearer {internal_token()}",
        "Accept-Encoding": "identity",
    }
    if range_header:
        req_headers["Range"] = range_header

    try:
        r = requests.request(
            method.upper(),
            url,
            headers=req_headers,
            stream=True,
            timeout=(30, 180),
            allow_redirects=True,
        )
    except Exception as exc:
        mark_local_mirror_error(local_version_id, exc)
        raise

    if method.upper() == "HEAD":
        result = {
            "source": "REMOTE",
            "path": None,
            "status_code": r.status_code,
            "headers": _response_headers(r.headers),
        }
        r.close()
        return result

    if r.status_code not in (200, 206):
        body = r.content[:65536]
        headers = _response_headers(r.headers)
        r.close()
        mark_local_mirror_error(
            local_version_id,
            f"Upstream {r.status_code} para {remote_path}",
        )
        return {
            "source": "REMOTE_ERROR",
            "path": None,
            "status_code": r.status_code,
            "headers": headers,
            "body": body,
        }

    rel_path, abs_path = _cached_path(local_root, key)
    abs_path.parent.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(
        prefix=abs_path.name + ".",
        suffix=".tmp",
        dir=str(abs_path.parent),
    )
    size = 0
    try:
        with os.fdopen(fd, "wb") as f:
            r.raw.decode_content = False
            while True:
                chunk = r.raw.read(1024 * 1024)
                if not chunk:
                    break
                f.write(chunk)
                size += len(chunk)
        os.replace(tmp_name, abs_path)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)

    headers = _response_headers(r.headers)
    r.close()
    register_cached_file(
        local_version_id=local_version_id,
        cache_key=key,
        remote_path=remote_path,
        range_header=range_header,
        local_relpath=rel_path.as_posix(),
        status_code=r.status_code,
        headers=headers,
        size_bytes=size,
    )

    return {
        "source": "REMOTE_CACHED",
        "path": abs_path,
        "status_code": r.status_code,
        "headers": headers,
    }
