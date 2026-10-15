# app/utils/token_manager.py
import os
import json
import time
import requests
from pathlib import Path

BASE_URL = "https://developer.api.autodesk.com"
TOKEN_FILE = Path("acc_tokens.json")  # Busca el archivo en la raíz del proyecto

class TokenManager:
    def __init__(self):
        self.token = None
        self.expires_at = 0
        self.client_id = os.getenv("ACC_CLIENT_ID", "").strip()
        self.client_secret = os.getenv("ACC_CLIENT_SECRET", "").strip()

    def _load_and_refresh_3lo_token(self):
        """Carga el token 3LO y lo refresca si es necesario."""
        if not TOKEN_FILE.exists():
            return None
        try:
            with open(TOKEN_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)

            obtained = data.get("_obtained_at", 0)
            expires_in = data.get("expires_in", 3600)

            # Si el token está por expirar (menos de 5 minutos), lo refrescamos
            if time.time() >= obtained + expires_in - 300:
                refresh_token = data.get("refresh_token")
                if not refresh_token:
                    return None
                # --- Lógica de refresh (idéntica a la de tu script) ---
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
                new_data["_obtained_at"] = int(time.time())
                with open(TOKEN_FILE, 'w', encoding='utf-8') as f:
                    json.dump(new_data, f, indent=2)
                return new_data["access_token"]
            else:
                return data["access_token"]
        except Exception:
            return None

    def get_token(self):
        if not TOKEN_FILE.exists():
            raise RuntimeError(f"No existe {TOKEN_FILE}")
        with open(TOKEN_FILE, 'r') as f:
            data = json.load(f)
        token = data.get("access_token")
        if not token:
            raise RuntimeError("No hay access_token en el archivo")
        return token
# Instancia global para usar en toda la app
token_manager = TokenManager()