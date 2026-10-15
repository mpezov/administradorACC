import os
import tempfile
from pathlib import Path

from dotenv import load_dotenv
import psycopg2

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def open_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "bpmbimgis"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
    )


def main():
    print("=" * 72)
    print("VERIFICACIÓN MIRROR CACHE v0.5")
    print("=" * 72)

    with open_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                    to_regclass('public.bim_local_versions'),
                    to_regclass('public.bim_local_cache_files')
            """)
            local_versions, cache_files = cur.fetchone()

    print("bim_local_versions      :", "OK" if local_versions else "FALTA")
    print("bim_local_cache_files   :", "OK" if cache_files else "FALTA")

    data_root = Path(
        (os.getenv("BIM_LOCAL_DATA_ROOT") or "").strip()
        or (ROOT / "data" / "projects")
    )
    print("BIM_LOCAL_DATA_ROOT     :", data_root)

    writable = False
    try:
        data_root.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=".mirror_write_test_", dir=str(data_root))
        os.close(fd)
        os.unlink(name)
        writable = True
    except Exception as exc:
        print("Escritura local         : ERROR", exc)
    else:
        print("Escritura local         : OK")

    print("=" * 72)
    if local_versions and cache_files and writable:
        print("OK: Mirror Cache listo.")
        return 0

    print("FALTA: revise migración o permisos de carpeta.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
