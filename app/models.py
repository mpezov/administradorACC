# app/models.py
import os
from sqlalchemy import create_engine, text

engine = None

def init_app(app):
    global engine
    print("⚙️ Inicializando models...")
    db_user = os.getenv('POSTGRES_USER')
    db_pass = os.getenv('POSTGRES_PASSWORD')
    db_host = os.getenv('POSTGRES_HOST')
    db_port = os.getenv('POSTGRES_PORT')
    db_name = os.getenv('POSTGRES_DB')

    if not db_port:
        db_port = '5432'
    db_port = str(db_port)

    db_url = f"postgresql+psycopg2://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
    engine = create_engine(db_url, future=True)
    print(f"✅ Engine creado: {engine}")

    # Crear tablas necesarias si no existen
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS acc_utils_folder (
                project_guid text NOT NULL,
                folder_id    text NOT NULL,
                tipo_gis     boolean NOT NULL DEFAULT false,
                incidencias  boolean NOT NULL DEFAULT false,
                updated_at   timestamptz NOT NULL DEFAULT now(),
                PRIMARY KEY (project_guid, folder_id)
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS acc_jobs (
                id BIGSERIAL PRIMARY KEY,
                job_type TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT now(),
                started_at TIMESTAMPTZ,
                finished_at TIMESTAMPTZ,
                progress INT,
                result JSONB,
                error TEXT
            )
        """))
    print("✅ Tablas verificadas/creadas")