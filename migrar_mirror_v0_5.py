import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import psycopg2

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

SQL_FILE = ROOT / "viewer_migrations/sql/06_local_mirror_cache.sql"

def open_conn():
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=os.getenv("POSTGRES_PORT", "5432"),
        dbname=os.getenv("POSTGRES_DB", "bpmbimgis"),
        user=os.getenv("POSTGRES_USER"),
        password=os.getenv("POSTGRES_PASSWORD"),
    )

def check(conn):
    with conn.cursor() as cur:
        cur.execute("""
            SELECT
                to_regclass('public.bim_local_versions'),
                to_regclass('public.bim_local_cache_files')
        """)
        local_versions, cache_files = cur.fetchone()

    print("=" * 72)
    print("bim_local_versions           ", "OK" if local_versions else "FALTA")
    print("bim_local_cache_files        ", "OK" if cache_files else "FALTA")
    print("=" * 72)
    return bool(local_versions and cache_files)

def main():
    conn = open_conn()
    try:
        if "--check" in sys.argv:
            raise SystemExit(0 if check(conn) else 2)

        if not SQL_FILE.exists():
            raise SystemExit(f"No existe {SQL_FILE}")

        sql = SQL_FILE.read_text(encoding="utf-8").strip()
        if sql.upper().startswith("BEGIN;"):
            sql = sql[6:].lstrip()
        if sql.upper().endswith("COMMIT;"):
            sql = sql[:-7].rstrip()

        try:
            with conn.cursor() as cur:
                cur.execute(sql)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        if not check(conn):
            raise SystemExit("Migración incompleta.")

        print("✅ Mirror Cache v0.5 instalado.")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
