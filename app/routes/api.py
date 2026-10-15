# app/routes/api.py
import os
import json
from threading import Thread
from flask import Blueprint, jsonify, request, current_app, send_file, abort
from sqlalchemy import text

from .. import models
from ..utils.acc_client import APSClient
from ..utils.gis_processor import detect_gis_folders, download_gis_folders
from ..utils.auditoria_pdfs import run_audit

api_bp = Blueprint('api', __name__)

# -------------------------------------------------------------------
# Funciones auxiliares para manejo de jobs
# -------------------------------------------------------------------
def create_job(job_type):
    with models.engine.begin() as conn:
        result = conn.execute(
            text("INSERT INTO acc_jobs (job_type, status) VALUES (:type, 'pending') RETURNING id"),
            {"type": job_type}
        )
        return result.scalar()

def update_job(job_id, status=None, progress=None, result=None, error=None):
    with models.engine.begin() as conn:
        query = "UPDATE acc_jobs SET "
        params = {"id": job_id}
        updates = []
        if status:
            updates.append("status = :status")
            params["status"] = status
        if progress is not None:
            updates.append("progress = :progress")
            params["progress"] = progress
        if result is not None:
            updates.append("result = :result")
            params["result"] = json.dumps(result)
        if error:
            updates.append("error = :error")
            params["error"] = error
        if status in ('running', 'completed', 'failed'):
            if status == 'running':
                updates.append("started_at = now()")
            else:
                updates.append("finished_at = now()")
        if not updates:
            return
        query += ", ".join(updates) + " WHERE id = :id"
        conn.execute(text(query), params)

# -------------------------------------------------------------------
# Tareas en segundo plano
# -------------------------------------------------------------------
def _run_detect_gis(job_id, extensions):
    update_job(job_id, status='running')
    try:
        count = detect_gis_folders(None, list(extensions))
        update_job(job_id, status='completed', result={"folders_marked": count})
    except Exception as e:
        print("❌ Error en detect_gis_folders:", e)
        update_job(job_id, status='failed', error=str(e))

def _run_download_gis(job_id, project_guid=None, folder_id=None):
    update_job(job_id, status='running')
    try:
        result = download_gis_folders(project_guid, folder_id)
        update_job(job_id, status='completed', result=result)
    except Exception as e:
        update_job(job_id, status='failed', error=str(e))

def _run_audit_pdfs(job_id, project_guid, folder_id, download_base, subtype_alteracion, subtype_falta_firma):
    update_job(job_id, status='running')
    try:
        from ..utils.acc_client import APSClient
        from ..utils.auditoria_pdfs import run_audit
        import os

        client_id = os.getenv("ACC_CLIENT_ID", "").strip()
        client_secret = os.getenv("ACC_CLIENT_SECRET", "").strip()
        region = os.getenv("APS_REGION", "US").strip()

        if not client_id or not client_secret:
            raise RuntimeError("Faltan ACC_CLIENT_ID o ACC_CLIENT_SECRET en .env")

        client = APSClient(client_id, client_secret, region)
        download_dir = os.path.join(download_base, 'audit')
        result = run_audit(
            project_guid=project_guid,
            folder_id=folder_id,
            download_dir=download_dir,
            client=client,
            subtype_alteracion=subtype_alteracion,
            subtype_falta_firma=subtype_falta_firma
        )
        # Si la auditoría se completó y generó un CSV, guardamos la ruta relativa para la descarga
        if result.get("csv_path"):
            # Calculamos la ruta relativa a download_base para poder construir la URL
            rel_path = os.path.relpath(result["csv_path"], download_base)
            result["csv_download_url"] = f"/admin/api/download/{rel_path.replace(os.sep, '/')}"
        update_job(job_id, status='completed', result=result)
    except Exception as e:
        update_job(job_id, status='failed', error=str(e))

# -------------------------------------------------------------------
# Rutas API
# -------------------------------------------------------------------
@api_bp.route('/detect-gis', methods=['POST'])
def start_detect_gis():
    extensions = current_app.config.get('GIS_EXTENSIONS', set())
    job_id = create_job('detect_gis')
    Thread(target=_run_detect_gis, args=(job_id, extensions)).start()
    return jsonify({"job_id": job_id})

@api_bp.route('/download-gis', methods=['POST'])
def start_download_gis():
    data = request.get_json(silent=True) or {}
    project_guid = data.get('project_guid')
    folder_id = data.get('folder_id')
    job_id = create_job('download_gis')
    Thread(target=_run_download_gis, args=(job_id, project_guid, folder_id)).start()
    return jsonify({"job_id": job_id})

@api_bp.route('/audit-pdfs', methods=['POST'])
def start_audit_pdfs():
    data = request.get_json(silent=True) or {}
    project_guid = data.get('project_guid')
    folder_id = data.get('folder_id')
    if not project_guid or not folder_id:
        return jsonify({"error": "Faltan project_guid o folder_id"}), 400

    download_base = current_app.config.get('DOWNLOAD_BASE', 'downloads')
    subtype_alteracion = current_app.config.get('SUBTYPE_ALTERACION', 'ALTERACIÓN')
    subtype_falta_firma = current_app.config.get('SUBTYPE_FALTA_FIRMA', 'FALTA FIRMA')

    job_id = create_job('audit_pdfs')
    Thread(target=_run_audit_pdfs, args=(
        job_id, project_guid, folder_id,
        download_base, subtype_alteracion, subtype_falta_firma
    )).start()
    return jsonify({"job_id": job_id})

@api_bp.route('/job/<int:job_id>', methods=['GET'])
def job_status(job_id):
    with models.engine.connect() as conn:
        job = conn.execute(
            text("SELECT * FROM acc_jobs WHERE id = :id"),
            {"id": job_id}
        ).mappings().first()
    if not job:
        return jsonify({"error": "Job no encontrado"}), 404
    return jsonify(dict(job))

# -------------------------------------------------------------------
# Ruta para descargar archivos generados (CSV, etc.)
# -------------------------------------------------------------------
@api_bp.route('/download/<path:filename>')
def download_file(filename):
    """Sirve un archivo para descarga, asegurando que esté dentro del directorio permitido."""
    download_base = current_app.config.get('DOWNLOAD_BASE', 'downloads')
    # Construir la ruta absoluta de forma segura
    safe_path = os.path.normpath(os.path.join(download_base, filename))
    # Verificar que el archivo esté dentro de download_base
    if not safe_path.startswith(os.path.abspath(download_base)):
        abort(404)
    if not os.path.exists(safe_path):
        abort(404)
    return send_file(safe_path, as_attachment=True)