"""Migración 06: varios federados publicados por proyecto (tramos).

Agrega a public.bim_project_models las columnas publicado, etiqueta y orden, y crea
la vista public.v_bim_viewer_models que lee el visor ANIN 360. Solo agrega: no
modifica ni borra datos. Se puede ejecutar más de una vez.

Uso:  python migrar_federados_publicados.py            (aplica)
      python migrar_federados_publicados.py --check    (solo verifica)
"""
import os
import sys
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")
SQL = ROOT / "viewer_migrations/sql/06_federados_publicados.sql"


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
              EXISTS (SELECT 1 FROM information_schema.columns
                      WHERE table_name='bim_project_models' AND column_name='publicado'),
              to_regclass('public.v_bim_viewer_models') IS NOT NULL
        """)
        columna, vista = cur.fetchone()
    print(f"bim_project_models.publicado  {'OK' if columna else 'FALTA'}")
    print(f"v_bim_viewer_models           {'OK' if vista else 'FALTA'}")
    return columna and vista


def main():
    conn = open_conn()
    try:
        if "--check" in sys.argv:
            raise SystemExit(0 if check(conn) else 2)
        try:
            with conn.cursor() as cur:
                cur.execute(SQL.read_text(encoding="utf-8"))
                # El principal de cada proyecto queda publicado desde el inicio.
                cur.execute("""
                    UPDATE public.bim_project_models SET publicado=true
                    WHERE model_role='MANAGERIAL' AND is_default AND enabled AND NOT publicado
                """)
                print(f"Principales marcados como publicados: {cur.rowcount}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        if not check(conn):
            raise SystemExit("Migración incompleta.")
        print("Migración 06 (federados publicados) completada.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
