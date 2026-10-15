from pathlib import Path
from dotenv import load_dotenv
import os

ROOT = Path(__file__).resolve().parent
load_dotenv(dotenv_path=ROOT / ".env")

print("=" * 72)
print("ENV:")
print("POSTGRES_HOST =", os.getenv("POSTGRES_HOST"))
print("POSTGRES_PORT =", os.getenv("POSTGRES_PORT"))
print("POSTGRES_DB   =", os.getenv("POSTGRES_DB"))
print("POSTGRES_USER =", os.getenv("POSTGRES_USER"))
print("POSTGRES_PASSWORD =", "***" if os.getenv("POSTGRES_PASSWORD") else None)
print("=" * 72)

from app import create_app
import app

application = create_app()

print("APP CARGADA DESDE:")
print(app.__file__)
print("=" * 72)
print("RUTAS REGISTRADAS:")
for rule in sorted(application.url_map.iter_rules(), key=lambda r: str(r)):
    methods = ",".join(sorted(m for m in rule.methods if m not in {"HEAD", "OPTIONS"}))
    print(f"{methods:12s} {rule}")
print("=" * 72)

required = ["/", "/admin/projects", "/admin/bim-stats", "/admin/"]
rules = {str(r) for r in application.url_map.iter_rules()}
missing = [r for r in required if r not in rules]

if missing:
    print("FALTAN RUTAS:", ", ".join(missing))
    print("El Portal BIM no está totalmente registrado.")
else:
    print("OK: Portal BIM registrado.")
    print("Abrir: http://127.0.0.1:5000/")
    print("   o : http://127.0.0.1:5000/admin/projects")
