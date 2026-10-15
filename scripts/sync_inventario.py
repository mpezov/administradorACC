# acc_sync_v3_final.py
# -*- coding: utf-8 -*-
"""
ACC → PostgreSQL Sincronización Avanzada v3 (final)
- Inserción de proyectos bajo demanda
- Manejo de GUID con prefijos (b., urn)
- Corrección de errores de clave foránea y variables no inicializadas
"""

import os
import sys
import json
import time
import random
import logging
import threading
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from zoneinfo import ZoneInfo
import re

import requests
import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv

load_dotenv()

# ===================== CONFIGURACIÓN =====================
BASE_URL = "https://developer.api.autodesk.com"
REGION = (os.getenv("APS_REGION") or "US").strip().upper()
HUB_NAME_FILTER = (os.getenv("ACC_HUB_NAME") or "").strip().lower()
PROJECT_GUID_FILTER = (os.getenv("ACC_PROJECT_GUID") or "").strip().lower()

# Credenciales
CID = (os.getenv("APS_CLIENT_ID") or "").strip()
CSEC = (os.getenv("APS_CLIENT_SECRET") or "").strip()
CID_S2S = (os.getenv("APS_CLIENT_ID_S2S") or CID).strip()
CSEC_S2S = (os.getenv("APS_CLIENT_SECRET_S2S") or CSEC).strip()
TOKEN_FILE = os.getenv("APS_TOKEN_FILE", "acc_token.json")
ALLOW_2LO_ONLY = os.getenv("ACC_ALLOW_2LO_ONLY") == "1"

# PostgreSQL
PG_DB = os.getenv("POSTGRES_DB", "postgres")
PG_USER = os.getenv("POSTGRES_USER", "postgres")
PG_PASS = os.getenv("POSTGRES_PASSWORD", "")
PG_HOST = os.getenv("POSTGRES_HOST", "localhost")
PG_PORT = int(os.getenv("POSTGRES_PORT", "5432"))

# Parámetros de sincronización
MAX_WORKERS = int(os.getenv("ACC_MAX_WORKERS", "5"))
API_RATE_LIMIT = float(os.getenv("ACC_API_RATE_LIMIT", "5"))
REQUEST_TIMEOUT = int(os.getenv("ACC_REQUEST_TIMEOUT", "60"))
MAX_ITEMS_PER_PROJECT = int(os.getenv("ACC_MAX_ITEMS", "100000000"))
ORPHAN_DAYS = int(os.getenv("ORPHAN_DAYS", "30"))
NAME_REGEX_PATTERN = os.getenv("ACC_NAMING_REGEX", r"^[A-Z0-9_\-]+(\s[A-Z0-9_\-]+)*\.[A-Za-z0-9]+$")
REPORT_MAX = int(os.getenv("ACC_REPORT_MAX", "50"))
REPORT_JSON_PATH = os.getenv("ACC_REPORT_JSON_PATH")
TIMEZONE = ZoneInfo(os.getenv("ACC_TZ", "America/Lima"))

# Logging
LOG_LEVEL = getattr(logging, os.getenv("ACC_LOG_LEVEL", "INFO").upper())
LOG_FILE = os.getenv("ACC_LOG_FILE")

# Checkpoint
CHECKPOINT_FILE = os.getenv("ACC_CHECKPOINT_FILE", "acc_checkpoint.json")

# ===================== CONFIGURAR LOGGING =====================
log_handlers = [logging.StreamHandler(sys.stdout)]
if LOG_FILE:
    log_handlers.append(logging.FileHandler(LOG_FILE, encoding='utf-8'))
logging.basicConfig(
    level=LOG_LEVEL,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=log_handlers
)
logger = logging.getLogger('acc_sync')

# ===================== UTILIDADES =====================
def utcnow():
    return dt.datetime.now(dt.timezone.utc)

def to_aware(dt_like):
    if dt_like is None:
        return None
    if dt_like.tzinfo is None:
        return dt_like.replace(tzinfo=dt.timezone.utc)
    return dt_like.astimezone(dt.timezone.utc)

def parse_iso_date(s):
    if not s:
        return None
    s = s.replace('Z', '')
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            d = dt.datetime.strptime(s[:26], fmt)
            return d.replace(tzinfo=dt.timezone.utc)
        except ValueError:
            continue
    logger.warning(f"Formato de fecha no reconocido: {s}")
    return None

def ext_of(filename):
    if not filename:
        return ""
    name = filename.lower()
    if name.endswith(".jpeg"):
        name = name[:-5] + ".jpg"
    if name.endswith(".geotiff"):
        name = name[:-8] + ".tif"
    return name.rsplit('.', 1)[-1] if '.' in name else ""

def clean_project_id(project_id):
    """Extrae el GUID de un project_id que puede venir con prefijos como 'b.' o 'urn:adsk.projects:'"""
    if not project_id:
        return None
    # Si contiene ':', tomar la última parte
    if ':' in project_id:
        project_id = project_id.split(':')[-1]
    # Si contiene '.', tomar la última parte (quita prefijos como 'b.')
    if '.' in project_id:
        project_id = project_id.split('.')[-1]
    return project_id

# ===================== ESQUEMA DE BASE DE DATOS =====================
DDL = """
CREATE TABLE IF NOT EXISTS acc_projects(
  id_guid      TEXT PRIMARY KEY,
  hub_id       TEXT NOT NULL,
  hub_name     TEXT,
  name         TEXT,
  region       TEXT,
  updated_at   TIMESTAMPTZ DEFAULT now()
);
CREATE TABLE IF NOT EXISTS acc_folders(
  folder_id    TEXT PRIMARY KEY,
  project_guid TEXT NOT NULL REFERENCES acc_projects(id_guid),
  folder_path  TEXT NOT NULL,
  parent_id    TEXT,
  updated_at   TIMESTAMPTZ DEFAULT now(),
  last_modified TIMESTAMPTZ,
  is_active    BOOLEAN DEFAULT TRUE
);
CREATE TABLE IF NOT EXISTS acc_items(
  item_id            TEXT PRIMARY KEY,
  project_guid       TEXT NOT NULL REFERENCES acc_projects(id_guid),
  folder_id          TEXT NOT NULL REFERENCES acc_folders(folder_id),
  item_name          TEXT NOT NULL,
  ext                TEXT,
  version_count      INT,
  latest_version_id  TEXT,
  last_modified      TIMESTAMPTZ,
  size_bytes         BIGINT,
  uploader           TEXT,
  naming_ok          SMALLINT,
  is_orphan          SMALLINT,
  folder_path_cached TEXT,
  updated_at         TIMESTAMPTZ DEFAULT now(),
  is_active          BOOLEAN DEFAULT TRUE
);
CREATE TABLE IF NOT EXISTS acc_item_versions(
  version_id         TEXT PRIMARY KEY,
  item_id            TEXT NOT NULL REFERENCES acc_items(item_id),
  created_at         TIMESTAMPTZ,
  last_modified_time TIMESTAMPTZ,
  storage_bucket     TEXT,
  storage_key        TEXT,
  size_bytes         BIGINT,
  uploader_name      TEXT
);
CREATE TABLE IF NOT EXISTS acc_item_changes(
  id           BIGSERIAL PRIMARY KEY,
  item_id      TEXT NOT NULL,
  change_type  TEXT NOT NULL,
  detected_at  TIMESTAMPTZ DEFAULT now(),
  from_value   TEXT,
  to_value     TEXT
);
CREATE TABLE IF NOT EXISTS acc_etl_runs(
  id          BIGSERIAL PRIMARY KEY,
  started_at  TIMESTAMPTZ DEFAULT now(),
  finished_at TIMESTAMPTZ,
  ok          BOOLEAN,
  rows_ins    INT,
  rows_upd    INT,
  note        TEXT
);
CREATE INDEX IF NOT EXISTS idx_items_project ON acc_items(project_guid);
CREATE INDEX IF NOT EXISTS idx_items_folder  ON acc_items(folder_id);
CREATE INDEX IF NOT EXISTS idx_items_ext     ON acc_items(ext);
CREATE INDEX IF NOT EXISTS idx_folders_project ON acc_folders(project_guid);
"""

def ensure_schema():
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute(DDL)
        conn.commit()
    logger.info("Esquema de base de datos verificado/creado.")

# ===================== MANEJO DE TOKENS =====================
class TokenManager:
    def __init__(self):
        self.token = None
        self.expires_at = 0
        self.lock = threading.Lock()

    def _get_2lo_token(self):
        if not CID_S2S or not CSEC_S2S:
            return None
        try:
            resp = requests.post(
                f"{BASE_URL}/authentication/v2/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "client_id": CID_S2S,
                    "client_secret": CSEC_S2S,
                    "grant_type": "client_credentials",
                    "scope": "data:read data:search account:read"
                },
                timeout=30
            )
            resp.raise_for_status()
            return resp.json()["access_token"]
        except Exception as e:
            logger.debug(f"2LO falló: {e}")
            return None

    def _get_3lo_token(self):
        if not os.path.exists(TOKEN_FILE):
            return None
        try:
            with open(TOKEN_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            obtained = data.get("_obtained_at", 0)
            expires_in = data.get("expires_in", 3600)
            if time.time() >= obtained + expires_in - 60:
                refresh_token = data.get("refresh_token")
                if not refresh_token:
                    return None
                resp = requests.post(
                    f"{BASE_URL}/authentication/v2/token",
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                    data={
                        "grant_type": "refresh_token",
                        "client_id": CID,
                        "client_secret": CSEC,
                        "refresh_token": refresh_token
                    },
                    timeout=30
                )
                resp.raise_for_status()
                new_data = resp.json()
                new_data["_obtained_at"] = time.time()
                with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
                    json.dump(new_data, f, indent=2)
                return new_data["access_token"]
            else:
                return data["access_token"]
        except Exception as e:
            logger.debug(f"3LO falló: {e}")
            return None

    def get_token(self):
        with self.lock:
            if self.token and time.time() < self.expires_at - 60:
                return self.token

            token = None
            if ALLOW_2LO_ONLY:
                token = self._get_2lo_token()
                if not token:
                    token = self._get_3lo_token()
            else:
                token = self._get_3lo_token()
                if not token:
                    token = self._get_2lo_token()

            if not token:
                raise RuntimeError("No se pudo obtener ningún token de acceso")

            self.token = token
            self.expires_at = time.time() + 3600
            return token

token_manager = TokenManager()

# ===================== CLIENTE HTTP CON REINTENTOS Y RATE LIMIT =====================
class APSClient:
    def __init__(self):
        self.session = requests.Session()
        self.rate_limit = API_RATE_LIMIT
        self.last_request = 0
        self.lock = threading.Lock()

    def _wait_rate(self):
        with self.lock:
            now = time.time()
            elapsed = now - self.last_request
            if elapsed < 1.0 / self.rate_limit:
                time.sleep(1.0 / self.rate_limit - elapsed)
            self.last_request = time.time()

    def request(self, method, url, params=None, json=None, headers=None, retries=5):
        token = token_manager.get_token()
        headers = headers or {}
        headers["Authorization"] = f"Bearer {token}"
        headers["Accept"] = "application/json"
        if REGION:
            headers["x-ads-region"] = REGION

        for attempt in range(retries):
            self._wait_rate()
            try:
                resp = self.session.request(
                    method, url, headers=headers, params=params, json=json,
                    timeout=REQUEST_TIMEOUT
                )
                if resp.status_code == 401:
                    logger.debug("Token 401, forzando renovación")
                    token_manager.token = None
                    token = token_manager.get_token()
                    headers["Authorization"] = f"Bearer {token}"
                    continue
                if resp.status_code in (429, 502, 503, 504):
                    wait = float(resp.headers.get("Retry-After", 5 * (attempt + 1)))
                    logger.debug(f"Rate limited ({resp.status_code}), esperando {wait:.1f}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp
            except (requests.ConnectionError, requests.Timeout) as e:
                logger.warning(f"Error de conexión (intento {attempt+1}/{retries}): {e}")
                if attempt == retries - 1:
                    raise
                time.sleep(2 ** attempt + random.random())
            except requests.HTTPError as e:
                if e.response.status_code in (429, 502, 503, 504) and attempt < retries - 1:
                    wait = float(e.response.headers.get("Retry-After", 5 * (attempt + 1)))
                    logger.debug(f"Error {e.response.status_code}, reintentando en {wait:.1f}s")
                    time.sleep(wait)
                else:
                    raise
        raise RuntimeError(f"Falló después de {retries} intentos: {url}")

    def get_paginated(self, url, params=None, data_key='data', limit=200):
        all_items = []
        page_params = params.copy() if params else {}
        page_params.setdefault('page[limit]', limit)
        next_url = url
        while next_url:
            resp = self.request('GET', next_url, params=page_params if next_url == url else None)
            j = resp.json()
            items = j.get(data_key, [])
            all_items.extend(items)
            links = j.get('links', {})
            next_link = links.get('next')
            if isinstance(next_link, dict):
                next_link = next_link.get('href')
            if not next_link:
                break
            next_url = next_link
            page_params = {}
        return all_items

client = APSClient()

# ===================== FUNCIONES API ESPECÍFICAS =====================
def get_hubs():
    return client.get_paginated(f"{BASE_URL}/project/v1/hubs", data_key='data')

def get_projects(hub_id):
    return client.get_paginated(f"{BASE_URL}/project/v1/hubs/{hub_id}/projects", data_key='data')

def get_top_folders(hub_id, project_id):
    urls = [
        f"{BASE_URL}/project/v1/hubs/{hub_id}/projects/{project_id}/topFolders",
        f"{BASE_URL}/data/v2/hubs/{hub_id}/projects/{project_id}/topFolders",
        f"{BASE_URL}/data/v1/projects/{project_id}/topFolders"
    ]
    for url in urls:
        try:
            items = client.get_paginated(url, data_key='data')
            if items:
                return items
        except requests.HTTPError as e:
            if e.response.status_code in (401, 403, 404):
                continue
            raise
    return []

def get_folder_contents(project_id, folder_id):
    url = f"{BASE_URL}/data/v1/projects/{project_id}/folders/{folder_id}/contents"
    return client.get_paginated(url, data_key='data')

def get_item_versions(project_id, item_id):
    url = f"{BASE_URL}/data/v1/projects/{project_id}/items/{item_id}/versions"
    return client.get_paginated(url, data_key='data')

def get_storage_from_version(version):
    rel = (version.get("relationships") or {}).get("storage") or {}
    data = rel.get("data") or {}
    urn = data.get("id") or ""
    path = urn.split(":", 3)[-1] if ":" in urn else urn
    if "/" in path:
        bucket, key = path.split("/", 1)
        return bucket, key
    return None, None

# ===================== BASE DE DATOS =====================
def get_db_connection():
    return psycopg2.connect(
        dbname=PG_DB, user=PG_USER, password=PG_PASS,
        host=PG_HOST, port=PG_PORT
    )

# ===================== PROCESAMIENTO DE UN PROYECTO =====================
class ProjectSync:
    def __init__(self, project_data, hub_name, region, last_run_time):
        self.project = project_data
        self.guid = clean_project_id(project_data["id"])
        self.name = (project_data.get("attributes") or {}).get("name", "")
        self.hub_name = hub_name
        self.region = region
        self.last_run_time = last_run_time
        self.stats = {
            'folders_new': 0,
            'folders_updated': 0,
            'items_new': 0,
            'items_updated': 0,
            'versions_new': 0,
            'folders_seen': set(),
            'items_seen': set()
        }
        self.report = {
            'new_folders': [],
            'updated_folders': [],
            'new_items': [],
            'new_versions': []
        }

    def run(self):
        logger.info(f"Iniciando proyecto {self.name} [{self.guid}]")
        try:
            # Asegurar que el proyecto existe en la BD
            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT 1 FROM acc_projects WHERE id_guid = %s", (self.guid,))
                if not cur.fetchone():
                    cur.execute("""
                        INSERT INTO acc_projects (id_guid, hub_id, hub_name, name, region, updated_at)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        ON CONFLICT (id_guid) DO NOTHING
                    """, (self.guid, self.project["hub_id"], self.hub_name, self.name, self.region, utcnow()))
                    conn.commit()
                    logger.info(f"  Proyecto {self.guid} insertado bajo demanda")

            roots = get_top_folders(self.project["hub_id"], self.project["id"])
            if not roots:
                logger.warning(f"  Proyecto {self.guid} sin carpetas raíz")
                return self.stats

            with get_db_connection() as conn, conn.cursor() as cur:
                cur.execute("SELECT folder_id, folder_path, parent_id, last_modified FROM acc_folders WHERE project_guid=%s AND is_active", (self.guid,))
                db_folders = {row[0]: {'path': row[1], 'parent': row[2], 'last_mod': row[3]} for row in cur}
                cur.execute("SELECT item_id, item_name, folder_path_cached, latest_version_id, last_modified FROM acc_items WHERE project_guid=%s AND is_active", (self.guid,))
                db_items = {row[0]: {'name': row[1], 'path': row[2], 'latest': row[3], 'last_mod': row[4]} for row in cur}

            queue = [(root["id"], [self._folder_name(root)], None) for root in roots]

            folders_batch = []
            items_batch = []
            versions_batch = []
            changes = []

            while queue:
                folder_id, path_parts, parent_id = queue.pop(0)
                if folder_id in self.stats['folders_seen']:
                    continue
                self.stats['folders_seen'].add(folder_id)

                folder_path = "/" + "/".join(path_parts)
                folders_batch.append((folder_id, self.guid, folder_path, parent_id, utcnow(), True))

                if folder_id not in db_folders:
                    self.stats['folders_new'] += 1
                    self.report['new_folders'].append({
                        'project': self.guid, 'folder_id': folder_id, 'path': folder_path
                    })
                else:
                    old = db_folders[folder_id]
                    if old['path'] != folder_path or old['parent'] != parent_id:
                        self.stats['folders_updated'] += 1
                        self.report['updated_folders'].append({
                            'project': self.guid, 'folder_id': folder_id,
                            'from': old['path'], 'to': folder_path
                        })

                contents = get_folder_contents(self.project["id"], folder_id)

                subfolders = []
                for elem in contents:
                    if elem.get("type") == "folders":
                        subfolders.append(elem)
                    elif elem.get("type") == "items":
                        self._process_item(elem, folder_id, folder_path, db_items, items_batch, versions_batch, changes)

                for sf in subfolders:
                    sf_id = sf["id"]
                    sf_name = (sf.get("attributes") or {}).get("name", "(sin nombre)")
                    queue.append((sf_id, path_parts + [sf_name], folder_id))

                if len(items_batch) >= 500:
                    self._flush(folders_batch, items_batch, versions_batch, changes)
                    folders_batch, items_batch, versions_batch, changes = [], [], [], []

                if len(self.stats['items_seen']) >= MAX_ITEMS_PER_PROJECT:
                    logger.warning(f"  Límite de {MAX_ITEMS_PER_PROJECT} items alcanzado en proyecto {self.guid}, deteniendo escaneo")
                    break

            if folders_batch or items_batch or versions_batch:
                self._flush(folders_batch, items_batch, versions_batch, changes)

            self._detect_deletions(db_folders, db_items)

            logger.info(f"  Proyecto finalizado: folders nuevos={self.stats['folders_new']}, actualizados={self.stats['folders_updated']}, "
                        f"items nuevos={self.stats['items_new']}, versiones nuevas={self.stats['versions_new']}")
            return self.stats

        except Exception as e:
            logger.exception(f"Error procesando proyecto {self.guid}: {e}")
            raise

    def _folder_name(self, folder):
        return (folder.get("attributes") or {}).get("name", "(sin nombre)")

    def _process_item(self, item, folder_id, folder_path, db_items, items_batch, versions_batch, changes):
        item_id = item["id"]
        self.stats['items_seen'].add(item_id)

        attrs = item.get("attributes") or {}
        display_name = attrs.get("displayName") or attrs.get("name") or item_id
        ext = ext_of(display_name)
        item_last_modified = parse_iso_date(attrs.get("lastModifiedTime"))

        db_item = db_items.get(item_id)
        need_versions = False

        # Inicializar variables
        latest_id = None
        last_mod = None
        uploader = ""
        size_bytes = None
        vcount = None

        if db_item is None:
            need_versions = True
            self.stats['items_new'] += 1
            self.report['new_items'].append({
                'project': self.guid, 'item_id': item_id, 'name': display_name, 'path': folder_path
            })
            changes.append((item_id, 'new_file', None, display_name))
        else:
            if item_last_modified and db_item['last_mod']:
                if item_last_modified > db_item['last_mod']:
                    need_versions = True
            if db_item['path'] != folder_path:
                changes.append((item_id, 'moved_folder', db_item['path'], folder_path))
            if db_item['name'] != display_name:
                changes.append((item_id, 'renamed', db_item['name'], display_name))

        if need_versions:
            versions = get_item_versions(self.project["id"], item_id)
            vcount = len(versions)
            latest = versions[0] if versions else None
            latest_id = latest["id"] if latest else None
            last_mod = parse_iso_date((latest.get("attributes") or {}).get("lastModifiedTime")) if latest else None
            uploader = (latest.get("attributes") or {}).get("lastModifiedUserName", "") if latest else ""
            size_bytes = None  # No disponible

            for v in versions:
                vid = v["id"]
                vattrs = v.get("attributes") or {}
                v_created = parse_iso_date(vattrs.get("createTime") or vattrs.get("createdTime"))
                v_lastmod = parse_iso_date(vattrs.get("lastModifiedTime") or vattrs.get("updatedAt"))
                bucket, key = get_storage_from_version(v)
                v_uploader = vattrs.get("lastModifiedUserName", "")
                versions_batch.append((vid, item_id, v_created, v_lastmod, bucket, key, None, v_uploader))

            if db_item and db_item['latest'] != latest_id:
                self.stats['versions_new'] += 1
                self.report['new_versions'].append({
                    'project': self.guid, 'item_id': item_id,
                    'from': db_item['latest'], 'to': latest_id
                })
                changes.append((item_id, 'new_version', db_item['latest'], latest_id))
        else:
            # Usar datos existentes en BD
            if db_item:
                latest_id = db_item['latest']
                last_mod = db_item['last_mod']
                # vcount se queda como None (no lo tenemos)
                uploader = ""
                # size_bytes ya es None

        name_ok = 1 if re.match(NAME_REGEX_PATTERN, display_name) else 0
        is_orphan = 1 if last_mod and (utcnow().date() - last_mod.date()).days > ORPHAN_DAYS else 0

        items_batch.append((
            item_id, self.guid, folder_id, display_name, ext, vcount,
            latest_id, last_mod, size_bytes, uploader,
            name_ok, is_orphan, folder_path, utcnow(), True
        ))

    def _flush(self, folders_batch, items_batch, versions_batch, changes):
        if not any([folders_batch, items_batch, versions_batch, changes]):
            return
        with get_db_connection() as conn, conn.cursor() as cur:
            if folders_batch:
                execute_values(cur, """
                    INSERT INTO acc_folders (folder_id, project_guid, folder_path, parent_id, updated_at, is_active)
                    VALUES %s
                    ON CONFLICT (folder_id) DO UPDATE SET
                        project_guid = EXCLUDED.project_guid,
                        folder_path = EXCLUDED.folder_path,
                        parent_id = EXCLUDED.parent_id,
                        updated_at = EXCLUDED.updated_at,
                        is_active = EXCLUDED.is_active
                """, folders_batch, page_size=1000)
            if items_batch:
                execute_values(cur, """
                    INSERT INTO acc_items (
                        item_id, project_guid, folder_id, item_name, ext, version_count,
                        latest_version_id, last_modified, size_bytes, uploader,
                        naming_ok, is_orphan, folder_path_cached, updated_at, is_active
                    ) VALUES %s
                    ON CONFLICT (item_id) DO UPDATE SET
                        project_guid = EXCLUDED.project_guid,
                        folder_id = EXCLUDED.folder_id,
                        item_name = EXCLUDED.item_name,
                        ext = EXCLUDED.ext,
                        version_count = EXCLUDED.version_count,
                        latest_version_id = EXCLUDED.latest_version_id,
                        last_modified = EXCLUDED.last_modified,
                        size_bytes = EXCLUDED.size_bytes,
                        uploader = EXCLUDED.uploader,
                        naming_ok = EXCLUDED.naming_ok,
                        is_orphan = EXCLUDED.is_orphan,
                        folder_path_cached = EXCLUDED.folder_path_cached,
                        updated_at = EXCLUDED.updated_at,
                        is_active = EXCLUDED.is_active
                """, items_batch, page_size=1000)
            if versions_batch:
                execute_values(cur, """
                    INSERT INTO acc_item_versions (version_id, item_id, created_at, last_modified_time,
                        storage_bucket, storage_key, size_bytes, uploader_name)
                    VALUES %s
                    ON CONFLICT (version_id) DO NOTHING
                """, versions_batch, page_size=2000)
            for ch in changes:
                cur.execute("""
                    INSERT INTO acc_item_changes (item_id, change_type, from_value, to_value)
                    VALUES (%s, %s, %s, %s)
                """, ch)
            conn.commit()
        logger.debug(f"Flushed: folders={len(folders_batch)}, items={len(items_batch)}, versions={len(versions_batch)}, changes={len(changes)}")

    def _detect_deletions(self, db_folders, db_items):
        folders_to_deactivate = set(db_folders.keys()) - self.stats['folders_seen']
        items_to_deactivate = set(db_items.keys()) - self.stats['items_seen']

        if not folders_to_deactivate and not items_to_deactivate:
            return

        with get_db_connection() as conn, conn.cursor() as cur:
            if folders_to_deactivate:
                cur.execute("""
                    UPDATE acc_folders SET is_active = FALSE, updated_at = now()
                    WHERE folder_id = ANY(%s) AND project_guid = %s
                """, (list(folders_to_deactivate), self.guid))
                logger.info(f"  {cur.rowcount} carpetas marcadas como inactivas")
            if items_to_deactivate:
                cur.execute("""
                    UPDATE acc_items SET is_active = FALSE, updated_at = now()
                    WHERE item_id = ANY(%s) AND project_guid = %s
                """, (list(items_to_deactivate), self.guid))
                logger.info(f"  {cur.rowcount} items marcados como inactivos")
            for item_id in items_to_deactivate:
                cur.execute("""
                    INSERT INTO acc_item_changes (item_id, change_type, from_value, to_value)
                    VALUES (%s, 'deleted', NULL, NULL)
                """, (item_id,))
            conn.commit()

# ===================== ORQUESTADOR PRINCIPAL =====================
def main():
    logger.info("=== Iniciando sincronización ACC → PostgreSQL v3 (final) ===")
    ensure_schema()

    # Obtener última ejecución exitosa
    last_run_time = None
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("SELECT MAX(finished_at) FROM acc_etl_runs WHERE ok = TRUE")
        row = cur.fetchone()
        if row and row[0]:
            last_run_time = to_aware(row[0])
            logger.info(f"Última ejecución exitosa: {last_run_time.astimezone(TIMEZONE)}")
        else:
            logger.info("No hay ejecuciones previas exitosas, se sincronizará todo")

    # Leer checkpoint
    start_project = None
    if os.path.exists(CHECKPOINT_FILE):
        try:
            with open(CHECKPOINT_FILE, 'r') as f:
                cp = json.load(f)
            start_project = cp.get('last_project')
            logger.info(f"Checkpoint encontrado: reanudar desde proyecto {start_project}")
        except Exception as e:
            logger.warning(f"No se pudo leer checkpoint: {e}")

    # Obtener hubs y proyectos
    hubs = get_hubs()
    if HUB_NAME_FILTER:
        hubs = [h for h in hubs if HUB_NAME_FILTER in (h.get("attributes", {}).get("name", "").lower())]
    if not hubs:
        logger.error("No se encontraron hubs con el filtro especificado")
        sys.exit(1)

    all_projects = []
    for hub in hubs:
        hub_id = hub["id"]
        hub_name = hub.get("attributes", {}).get("name", "")
        region = hub.get("attributes", {}).get("region") or REGION
        projects = get_projects(hub_id)
        if PROJECT_GUID_FILTER:
            projects = [p for p in projects if clean_project_id(p["id"]).lower() == PROJECT_GUID_FILTER]
        for p in projects:
            p['hub_id'] = hub_id
            p['hub_name'] = hub_name
            p['region'] = region
        all_projects.extend(projects)

    if not all_projects:
        logger.error("No hay proyectos para sincronizar")
        sys.exit(1)

    # Insertar todos los proyectos en la BD (masivo)
    proj_values = []
    for p in all_projects:
        guid = clean_project_id(p["id"])
        name = (p.get("attributes") or {}).get("name", "")
        hub_id = p.get("hub_id")
        hub_name = p.get("hub_name", "")
        region = p.get("region", REGION)
        proj_values.append((guid, hub_id, hub_name, name, region, utcnow()))

    if proj_values:
        with get_db_connection() as conn, conn.cursor() as cur:
            execute_values(cur, """
                INSERT INTO acc_projects (id_guid, hub_id, hub_name, name, region, updated_at)
                VALUES %s
                ON CONFLICT (id_guid) DO UPDATE SET
                    hub_id = EXCLUDED.hub_id,
                    hub_name = EXCLUDED.hub_name,
                    name = EXCLUDED.name,
                    region = EXCLUDED.region,
                    updated_at = EXCLUDED.updated_at
            """, proj_values, page_size=500)
            conn.commit()
        logger.info(f"Proyectos insertados/actualizados: {len(proj_values)}")

    # Ordenar proyectos por GUID (para checkpoint)
    all_projects.sort(key=lambda x: clean_project_id(x["id"]))

    skip = True if start_project else False
    projects_to_process = []
    for p in all_projects:
        guid = clean_project_id(p["id"])
        if skip:
            if guid == start_project:
                skip = False
            else:
                continue
        projects_to_process.append(p)

    logger.info(f"Se procesarán {len(projects_to_process)} proyectos")

    # Ejecutar en paralelo
    overall_stats = defaultdict(int)
    all_reports = {
        'new_folders': [],
        'updated_folders': [],
        'new_items': [],
        'new_versions': []
    }

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_proj = {}
        for p in projects_to_process:
            sync = ProjectSync(p, p['hub_name'], p['region'], last_run_time)
            future = executor.submit(sync.run)
            future_to_proj[future] = (p, sync)

        for future in as_completed(future_to_proj):
            p, sync = future_to_proj[future]
            try:
                stats = future.result()
                for k, v in stats.items():
                    if isinstance(v, int):
                        overall_stats[k] += v
                for key in ['new_folders', 'updated_folders', 'new_items', 'new_versions']:
                    all_reports[key].extend(sync.report.get(key, []))
                # Guardar checkpoint
                with open(CHECKPOINT_FILE, 'w') as f:
                    json.dump({'last_project': clean_project_id(p["id"])}, f)
            except Exception as e:
                logger.error(f"Fallo en proyecto {p['id']}: {e}")
                # No guardamos checkpoint de este proyecto

    # Eliminar checkpoint si todo fue bien
    if os.path.exists(CHECKPOINT_FILE):
        os.remove(CHECKPOINT_FILE)

    # Registrar ejecución
    with get_db_connection() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO acc_etl_runs (started_at, finished_at, ok, rows_ins, rows_upd, note)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            last_run_time or utcnow(), utcnow(), True,
            overall_stats.get('items_new', 0) + overall_stats.get('folders_new', 0),
            overall_stats.get('items_updated', 0) + overall_stats.get('folders_updated', 0) + overall_stats.get('versions_new', 0),
            f"Proyectos procesados: {len(projects_to_process)}"
        ))
        conn.commit()

    # Generar reporte final
    logger.info("===== RESUMEN DE CAMBIOS =====")
    logger.info(f"Carpetas nuevas: {overall_stats.get('folders_new', 0)}")
    logger.info(f"Carpetas actualizadas: {overall_stats.get('folders_updated', 0)}")
    logger.info(f"Archivos nuevos: {overall_stats.get('items_new', 0)}")
    logger.info(f"Nuevas versiones: {overall_stats.get('versions_new', 0)}")

    if REPORT_JSON_PATH:
        report_data = {
            'started_at': (last_run_time or utcnow()).astimezone(TIMEZONE).isoformat(),
            'finished_at': utcnow().astimezone(TIMEZONE).isoformat(),
            'stats': dict(overall_stats),
            'new_folders': all_reports['new_folders'][:REPORT_MAX],
            'updated_folders': all_reports['updated_folders'][:REPORT_MAX],
            'new_items': all_reports['new_items'][:REPORT_MAX],
            'new_versions': all_reports['new_versions'][:REPORT_MAX]
        }
        with open(REPORT_JSON_PATH, 'w', encoding='utf-8') as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        logger.info(f"Reporte JSON guardado en {REPORT_JSON_PATH}")

    logger.info("Sincronización completada exitosamente.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("Interrumpido por usuario")
        sys.exit(0)
    except Exception as e:
        logger.exception("Error fatal")
        sys.exit(1)