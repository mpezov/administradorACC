# app/utils/acc_client.py
import os
import json
import time
import requests
from urllib.parse import quote
from typing import List, Optional

AUTH_BASE = "https://developer.api.autodesk.com/authentication/v2"
DM_BASE = "https://developer.api.autodesk.com/data/v1"
OSS_BASE = "https://developer.api.autodesk.com/oss/v2"
REVIEWS_BASE = "https://developer.api.autodesk.com/construction/reviews/v1"
ISSUES_BASE = "https://developer.api.autodesk.com/construction/issues/v1"

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOKENS_FILE = os.path.join(BASE_DIR, "acc_tokens.json")
def load_tokens() -> dict:
    if not os.path.exists(TOKENS_FILE):
        raise RuntimeError(f"No existe {TOKENS_FILE}. Ejecuta primero tu bootstrap OAuth 3LO.")
    with open(TOKENS_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def save_tokens(tok: dict) -> None:
    with open(TOKENS_FILE, "w", encoding="utf-8") as f:
        json.dump(tok, f, indent=2)

def refresh_access_token(client_id: str, client_secret: str) -> str:
    tok = load_tokens()
    rt = tok.get("refresh_token")
    if not rt:
        raise RuntimeError("acc_tokens.json no tiene refresh_token.")
    r = requests.post(
        f"{AUTH_BASE}/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={
            "grant_type": "refresh_token",
            "refresh_token": rt,
            "client_id": client_id,
            "client_secret": client_secret,
        },
        timeout=60
    )
    r.raise_for_status()
    newt = r.json()
    tok["access_token"] = newt["access_token"]
    if "refresh_token" in newt:
        tok["refresh_token"] = newt["refresh_token"]
    save_tokens(tok)
    return tok["access_token"]

class APSClient:
    def __init__(self, client_id: str, client_secret: str, region: str = "US"):
        self.client_id = client_id
        self.client_secret = client_secret
        self.region = region
        self.token = refresh_access_token(client_id, client_secret)
        self.s = requests.Session()

    def _headers(self, json_ct=False) -> dict:
        h = {
            "Authorization": f"Bearer {self.token}",
            "x-ads-region": self.region,
            "Accept": "application/json",
        }
        if json_ct:
            h["Content-Type"] = "application/json"
        return h

    def _ensure_project_prefix(self, project_id: str) -> str:
        """Asegura que el project_id tenga el prefijo 'b.' para las APIs de Data Management."""
        if not project_id.startswith('b.'):
            return f"b.{project_id}"
        return project_id

    def _do(self, method: str, url: str, *, params=None, json_body=None, data_body=None, timeout=60, max_retries=3):
        backoff = 0.7
        for i in range(max_retries):
            try:
                headers = self._headers(json_ct=(json_body is not None))
                r = self.s.request(
                    method=method,
                    url=url,
                    headers=headers,
                    params=params or {},
                    json=json_body,
                    data=data_body,
                    timeout=timeout
                )
                if r.status_code in (401, 403):
                    self.token = refresh_access_token(self.client_id, self.client_secret)
                    headers = self._headers(json_ct=(json_body is not None))
                    r = self.s.request(
                        method=method,
                        url=url,
                        headers=headers,
                        params=params or {},
                        json=json_body,
                        data=data_body,
                        timeout=timeout
                    )
                if r.status_code in (429, 502, 503, 504):
                    retry_after = float(r.headers.get("Retry-After", backoff))
                    time.sleep(retry_after)
                    backoff = min(backoff * 2, 8.0)
                    continue
                r.raise_for_status()
                return r
            except requests.RequestException as e:
                print(f"⚠️ Intento {i+1} falló: {e}")
                if i == max_retries - 1:
                    raise
                time.sleep(backoff)
                backoff = min(backoff * 2, 8.0)

    def get_json(self, url: str, *, params=None, timeout=60) -> dict:
        r = self._do("GET", url, params=params, timeout=timeout)
        return r.json() if r.content else {}

    def post_json(self, url: str, *, json_body: dict, params=None, timeout=60) -> dict:
        r = self._do("POST", url, params=params, json_body=json_body, timeout=timeout)
        return r.json() if r.content else {}

    # Métodos específicos
    def get_issue_types(self, project_no_b: str) -> dict:
        # Para Issues, no necesita prefijo
        url = f"{ISSUES_BASE}/projects/{project_no_b}/issue-types"
        return self.get_json(url, params={"include": "subtypes"})

    def search_issues(self, project_no_b: str, title: str) -> dict:
        url = f"{ISSUES_BASE}/projects/{project_no_b}/issues"
        return self.get_json(url, params={"filter[search]": title})

    def create_issue(self, project_no_b: str, issue_data: dict) -> dict:
        url = f"{ISSUES_BASE}/projects/{project_no_b}/issues"
        return self.post_json(url, json_body=issue_data)

    def get_user_issue_settings(self, project_no_b: str) -> dict:
        url = f"{ISSUES_BASE}/projects/{project_no_b}/users/me"
        return self.get_json(url)

    def get_signed_download_url(self, bucket_key: str, object_key: str) -> str:
        url = f"{OSS_BASE}/buckets/{bucket_key}/objects/{object_key}/signeds3download"
        return self.get_json(url).get('url')

    def download_file(self, storage_path: str, local_path: str):
        bucket_key, object_key = storage_path.split('/', 1)
        signed_url = self.get_signed_download_url(bucket_key, object_key)
        r = requests.get(signed_url, timeout=180)
        r.raise_for_status()
        os.makedirs(os.path.dirname(local_path), exist_ok=True)
        with open(local_path, 'wb') as f:
            f.write(r.content)

    def list_folder_contents(self, project_id: str, folder_id: str) -> List[dict]:
        # Asegurar prefijo para Data Management
        project_id_full = self._ensure_project_prefix(project_id)
        encoded_folder = quote(folder_id, safe='')
        url = f"{DM_BASE}/projects/{project_id_full}/folders/{encoded_folder}/contents"
        print(f"📡 URL solicitada: {url}")
        return self.get_json(url).get("data", [])

    def get_item_versions(self, project_id: str, item_id: str) -> List[dict]:
        project_id_full = self._ensure_project_prefix(project_id)
        url = f"{DM_BASE}/projects/{project_id_full}/items/{item_id}/versions"
        return self.get_json(url).get("data", [])