import json
import os
import threading
import time
from pathlib import Path
from urllib.parse import quote

import requests

APS_BASE = "https://developer.api.autodesk.com"
AUTH_URL = f"{APS_BASE}/authentication/v2/token"
ROOT = Path(__file__).resolve().parents[2]

_lock = threading.Lock()
_tokens = {}

def _s2s_credentials():
    cid = (
        os.getenv("APS_CLIENT_ID_S2S")
        or os.getenv("APS_CLIENT_ID")
        or os.getenv("ACC_CLIENT_ID")
        or ""
    ).strip()
    sec = (
        os.getenv("APS_CLIENT_SECRET_S2S")
        or os.getenv("APS_CLIENT_SECRET")
        or os.getenv("ACC_CLIENT_SECRET")
        or ""
    ).strip()
    return cid, sec

def _token_file():
    configured = (os.getenv("APS_TOKEN_FILE") or os.getenv("ACC_TOKEN_FILE") or "").strip()
    if configured:
        return Path(configured)
    fallback = ROOT / "acc_tokens.json"
    return fallback if fallback.exists() else None

def _token_2lo(scopes):
    key = ("2lo", " ".join(sorted(scopes.split())))
    now = int(time.time())
    with _lock:
        cached = _tokens.get(key)
        if cached and now + 300 < cached["expires_at"]:
            return cached["access_token"], cached["expires_at"] - now

    cid, sec = _s2s_credentials()
    if not cid or not sec:
        raise RuntimeError(
            "Faltan credenciales S2S. Configure APS_CLIENT_ID_S2S y "
            "APS_CLIENT_SECRET_S2S."
        )

    r = requests.post(
        AUTH_URL,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "client_id": cid,
            "client_secret": sec,
            "grant_type": "client_credentials",
            "scope": scopes,
        },
        timeout=30,
    )
    r.raise_for_status()
    payload = r.json()
    expires_in = int(payload.get("expires_in", 3600))
    token = payload["access_token"]

    with _lock:
        _tokens[key] = {
            "access_token": token,
            "expires_at": now + expires_in,
        }
    return token, expires_in

def _token_pkce_data_read():
    path = _token_file()
    if not path or not path.exists():
        raise RuntimeError("No existe token PKCE reutilizable.")

    tok = json.loads(path.read_text(encoding="utf-8"))
    now = int(time.time())
    exp = int(tok.get("expires_in", 3600))
    obtained = int(tok.get("_obtained_at", now - exp + 10))

    if obtained + exp - now < 300:
        cid = (
            tok.get("_client_id")
            or os.getenv("APS_CLIENT_ID")
            or os.getenv("ACC_CLIENT_ID")
            or ""
        ).strip()
        refresh = tok.get("refresh_token")
        if not cid or not refresh:
            raise RuntimeError("El token PKCE venció y no tiene refresh utilizable.")

        data = {
            "grant_type": "refresh_token",
            "client_id": cid,
            "refresh_token": refresh,
        }
        sec = (os.getenv("APS_CLIENT_SECRET") or os.getenv("ACC_CLIENT_SECRET") or "").strip()
        if sec:
            data["client_secret"] = sec

        r = requests.post(
            AUTH_URL,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data=data,
            timeout=30,
        )
        r.raise_for_status()
        tok = r.json()
        tok["_obtained_at"] = now
        tok["_client_id"] = cid
        path.write_text(json.dumps(tok, ensure_ascii=False, indent=2), encoding="utf-8")

    access = tok.get("access_token")
    if not access:
        raise RuntimeError("Token PKCE sin access_token.")
    return access

def internal_token():
    # Token de servidor. No se devuelve al navegador.
    try:
        return _token_2lo("data:read viewables:read")[0]
    except Exception as s2s_error:
        try:
            return _token_pkce_data_read()
        except Exception as pkce_error:
            raise RuntimeError(
                f"No hay autenticación APS utilizable. "
                f"S2S: {s2s_error}; PKCE: {pkce_error}"
            )

def public_viewer_token():
    # El navegador recibe únicamente un token 2LO con viewables:read.
    token, expires = _token_2lo("viewables:read")
    return {
        "access_token": token,
        "expires_in": max(60, int(expires)),
    }

def _get_json(url, token=None, timeout=60):
    last_error = None
    for attempt in range(5):
        try:
            r = requests.get(
                url,
                headers={
                    "Authorization": f"Bearer {token or internal_token()}",
                    "Accept": "application/vnd.api+json, application/json",
                },
                timeout=timeout,
            )
            if r.status_code == 429:
                wait = min(20, int(r.headers.get("Retry-After") or (2 ** attempt)))
                time.sleep(wait)
                continue
            if r.status_code >= 500:
                time.sleep(min(10, 2 ** attempt))
                continue
            if r.status_code == 404:
                return None, 404
            r.raise_for_status()
            return r.json(), r.status_code
        except Exception as exc:
            last_error = exc
            if attempt == 4:
                raise
            time.sleep(min(5, 2 ** attempt))
    raise RuntimeError(str(last_error or "Error APS"))

def _find_derivative_relationship(payload):
    if not isinstance(payload, dict):
        return None, None

    nodes = []
    data = payload.get("data")
    if isinstance(data, dict):
        nodes.append(data)
    elif isinstance(data, list):
        nodes.extend(x for x in data if isinstance(x, dict))

    included = payload.get("included")
    if isinstance(included, list):
        nodes.extend(x for x in included if isinstance(x, dict))

    for node in nodes:
        rel = (node.get("relationships") or {}).get("derivatives") or {}
        rel_data = rel.get("data")
        if isinstance(rel_data, dict) and rel_data.get("id"):
            meta = rel.get("meta") or {}
            link = meta.get("link") or {}
            return rel_data.get("id"), link.get("href")

    return None, None

def _walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value)

def _manifest_info(manifest):
    formats = set()
    viewable_guid = None

    for node in _walk(manifest):
        output_type = str(node.get("outputType") or "").lower()
        mime = str(node.get("mime") or "").lower()

        if output_type in ("svf", "svf2"):
            formats.add(output_type.upper())
        if "autodesk-svf2" in mime:
            formats.add("SVF2")
        elif "autodesk-svf" in mime:
            formats.add("SVF")

        if not viewable_guid:
            role = str(node.get("role") or "").lower()
            typ = str(node.get("type") or "").lower()
            if node.get("guid") and role == "3d" and typ in ("geometry", "view"):
                viewable_guid = node["guid"]

    derivative_format = (
        "SVF2" if "SVF2" in formats
        else ("SVF" if "SVF" in formats else None)
    )
    return derivative_format, viewable_guid

def resolve_acc_derivative(project_guid, version_id):
    if not project_guid or not version_id:
        return {
            "status": "ERROR",
            "last_error": "Falta project_guid o latest_version_id.",
        }

    project_id = project_guid if str(project_guid).startswith("b.") else f"b.{project_guid}"
    encoded_version = quote(str(version_id), safe="")
    token = internal_token()

    version_url = f"{APS_BASE}/data/v1/projects/{project_id}/versions/{encoded_version}"
    payload, _ = _get_json(version_url, token=token)
    derivative_urn, manifest_url = _find_derivative_relationship(payload)

    if not derivative_urn:
        refs_url = (
            f"{APS_BASE}/data/v1/projects/{project_id}/versions/"
            f"{encoded_version}/relationships/refs"
        )
        refs, _ = _get_json(refs_url, token=token)
        derivative_urn, manifest_url = _find_derivative_relationship(refs)

    if not derivative_urn:
        return {
            "status": "MISSING",
            "derivative_urn": None,
            "manifest_url": None,
            "derivative_format": None,
            "default_viewable_guid": None,
            "last_error": "ACC no devolvió relación derivatives para esta versión.",
        }

    if not manifest_url:
        manifest_url = (
            f"{APS_BASE}/modelderivative/v2/designdata/"
            f"{derivative_urn}/manifest"
        )

    manifest, manifest_code = _get_json(manifest_url, token=token)

    if manifest_code == 404 or manifest is None:
        return {
            "status": "PROCESSING",
            "derivative_urn": derivative_urn,
            "manifest_url": manifest_url,
            "derivative_format": None,
            "default_viewable_guid": None,
            "last_error": "Derivative encontrado; manifest todavía no disponible.",
        }

    md_status = str(manifest.get("status") or "").lower()
    progress = manifest.get("progress")
    derivative_format, viewable_guid = _manifest_info(manifest)

    if md_status in ("pending", "inprogress", "in_progress"):
        status = "PROCESSING"
        error = f"Traducción en proceso ({progress or md_status})."
    elif md_status in ("failed", "timeout"):
        status = "ERROR"
        error = f"Manifest APS con estado {md_status}."
    else:
        status = "READY"
        error = None

    return {
        "status": status,
        "derivative_urn": derivative_urn,
        "manifest_url": manifest_url,
        "derivative_format": derivative_format,
        "default_viewable_guid": viewable_guid,
        "last_error": error,
        "manifest_status": manifest.get("status"),
        "manifest_progress": progress,
    }
