# app/utils/token_manager.py
import os
import json
import time
import threading
import requests

BASE_URL = "https://developer.api.autodesk.com"
from pathlib import Path
APS_TOKEN_FILE = Path(__file__).parent.parent / "acc_tokens.json"

class TokenManager:
    def __init__(self):
        self.token = None
        self.expires_at = 0
        self.lock = threading.Lock()
        self.client_id = os.getenv("APS_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("APS_CLIENT_SECRET", "").strip()
        self.client_id_s2s = os.getenv("APS_CLIENT_ID_S2S", self.client_id).strip()
        self.client_secret_s2s = os.getenv("APS_CLIENT_SECRET_S2S", self.client_secret).strip()
        #self.token_file = os.getenv("APS_TOKEN_FILE", "acc_token.json")
        self.allow_2lo_only = os.getenv("ACC_ALLOW_2LO_ONLY") == "1"

    def _get_2lo_token(self):
        if not self.client_id_s2s or not self.client_secret_s2s:
            return None
        try:
            resp = requests.post(
                f"{BASE_URL}/authentication/v2/token",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                data={
                    "client_id": self.client_id_s2s,
                    "client_secret": self.client_secret_s2s,
                    "grant_type": "client_credentials",
                    "scope": "data:read data:search account:read"
                },
                timeout=30
            )
            resp.raise_for_status()
            return resp.json()["access_token"]
        except Exception:
            return None

    def _get_3lo_token(self):
        if not os.path.exists(self.token_file):
            return None
        try:
            with open(self.token_file, 'r', encoding='utf-8') as f:
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
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "refresh_token": refresh_token
                    },
                    timeout=30
                )
                resp.raise_for_status()
                new_data = resp.json()
                new_data["_obtained_at"] = time.time()
                with open(self.token_file, 'w', encoding='utf-8') as f:
                    json.dump(new_data, f, indent=2)
                return new_data["access_token"]
            else:
                return data["access_token"]
        except Exception:
            return None

    def get_token(self):
        with self.lock:
            if self.token and time.time() < self.expires_at - 60:
                return self.token

            token = None
            # Intentar 3LO primero (si hay archivo de token)
            token = self._get_3lo_token()
            if not token:
                token = self._get_2lo_token()   # fallback a 2LO

            if not token:
                raise RuntimeError("No se pudo obtener token de acceso")

            self.token = token
            self.expires_at = time.time() + 3600
            return token

token_manager = TokenManager()