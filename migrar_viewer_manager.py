import os
import sys
from pathlib import Path
from dotenv import load_dotenv
import psycopg2

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")

FILES = [
    ROOT / "viewer_migrations/sql/01_catalogo_bim.sql",
    ROOT / "viewer_migrations/sql/02_vistas_catalogo_bim.sql",
    ROOT / "viewer_migrations/sql/03_seed_perfiles_gerenciales.sql",
    ROOT / "viewer_migrations/sql/05_viewer_manager.sql",
]

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
          to_regclass('public.bim_projects'),
          to_regclass('public.bim_project_models'),
          to_regclass('public.bim_remote_versions'),
          to_regclass('public.bim_local_versions'),
          to_regclass('public.v_bim_viewer_state')
        """)
        row = cur.fetchone()

    labels = [
        "bim_projects",
        "bim_project_models",
        "bim_remote_versions",
        "bim_local_versions",
        "v_bim_viewer_state",
    ]
    print("=" * 72)
    for label, value in zip(labels, row):
        print(f"{label:28} {'OK' if value else 'FALTA'}")
    print("=" * 72)
    return all(row)

def main():
    conn = open_conn()
    try:
        if "--check" in sys.argv:
            ok = check(conn)
            raise SystemExit(0 if ok else 2)

        print("Aplicando catálogo BIM + Viewer Manager...")
        try:
            with conn.cursor() as cur:
                for path in FILES:
                    print("  ->", path.name)
                    sql = path.read_text(encoding="utf-8").strip()
                    # Los SQL originales son ejecutables por separado y traen BEGIN/COMMIT.
                    # Aquí se retiran para aplicar toda la migración en una sola transacción.
                    if sql.upper().startswith("BEGIN;"):
                        sql = sql[6:].lstrip()
                    if sql.upper().endswith("COMMIT;"):
                        sql = sql[:-7].rstrip()
                    cur.execute(sql)
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        if not check(conn):
            raise SystemExit("Migración incompleta.")
        print("✅ Migración Viewer Manager completada.")
    finally:
        conn.close()

if __name__ == "__main__":
    main()
