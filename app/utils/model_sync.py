# app/utils/model_sync.py
"""
"Buscar nuevos modelos": ejecuta scripts/sync_bim_models_fast.py (discovery NWD)
para un proyecto y deja el resultado en la base:

1. Discovery recursivo en ACC (--discover --apply): guarda candidatos en
   bim_nwd_candidates y refresca la versión de los modelos ya registrados.
2. Inserta/actualiza los NWD encontrados en acc_folders/acc_items, para que
   aparezcan en la lista de federados de la ficha del proyecto.
3. Re-verifica el derivative del modelo gerencial seleccionado.

Corre en un hilo; el avance queda en acc_jobs (job_type='sync_models').
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from threading import Thread

from sqlalchemy import text

from .. import models

ROOT = Path(__file__).resolve().parents[2]
JOB_TYPE = "sync_models"
MAX_LOG_LINES = 400


def _project_code(project_guid):
    with models.engine.connect() as conn:
        row = conn.execute(
            text("SELECT cod_wvs FROM public.acc_projects WHERE id_guid=:g"),
            {"g": project_guid},
        ).first()
    code = (row[0] or "").strip() if row else ""
    if not code:
        raise ValueError("El proyecto no tiene código WVS en acc_projects.")
    return code


def running_job(project_guid):
    """Job en curso para el proyecto (evita dos búsquedas simultáneas)."""
    with models.engine.connect() as conn:
        row = conn.execute(
            text(
                """
                SELECT id FROM acc_jobs
                WHERE job_type=:t AND status='running'
                  AND result->>'project_guid'=:g
                  AND created_at > now() - interval '2 hours'
                ORDER BY id DESC LIMIT 1
                """
            ),
            {"t": JOB_TYPE, "g": project_guid},
        ).first()
    return row[0] if row else None


def get_job(job_id):
    with models.engine.connect() as conn:
        job = conn.execute(
            text("SELECT * FROM acc_jobs WHERE id=:id AND job_type=:t"),
            {"id": job_id, "t": JOB_TYPE},
        ).mappings().first()
    return dict(job) if job else None


def _save(job_id, status=None, result=None, error=None, finished=False):
    sets, params = [], {"id": job_id}
    if status:
        sets.append("status=:status")
        params["status"] = status
    if result is not None:
        sets.append("result=CAST(:result AS jsonb)")
        params["result"] = json.dumps(result, ensure_ascii=False, default=str)
    if error is not None:
        sets.append("error=:error")
        params["error"] = error
    if finished:
        sets.append("finished_at=now()")
    with models.engine.begin() as conn:
        conn.execute(text(f"UPDATE acc_jobs SET {', '.join(sets)} WHERE id=:id"), params)


def start(project_guid):
    """Crea el job y lanza el hilo. Devuelve el id del job."""
    existing = running_job(project_guid)
    if existing:
        return existing
    code = _project_code(project_guid)
    result = {"project_guid": project_guid, "project_code": code, "log": []}
    with models.engine.begin() as conn:
        job_id = conn.execute(
            text(
                """
                INSERT INTO acc_jobs (job_type, status, started_at, result)
                VALUES (:t, 'running', now(), CAST(:r AS jsonb)) RETURNING id
                """
            ),
            {"t": JOB_TYPE, "r": json.dumps(result)},
        ).scalar()
    Thread(target=_run, args=(job_id, result), daemon=True).start()
    return job_id


def _run(job_id, result):
    log = result["log"]

    def add(line):
        log.append(line.rstrip())
        del log[:-MAX_LOG_LINES]

    try:
        report = Path(tempfile.gettempdir()) / f"sync_bim_discovery_{result['project_code']}_{job_id}.json"
        cmd = [
            sys.executable, "-u", str(ROOT / "run_sync_bim_fast.py"),
            "--discover", "--project-code", result["project_code"],
            "--apply", "--json", str(report),
        ]
        add("Buscando archivos NWD en ACC (puede tardar varios minutos)...")
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        proc = subprocess.Popen(
            cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
        )
        def ticker():
            # Guarda el avance cada 3 s aunque no lleguen líneas nuevas.
            while proc.poll() is None:
                time.sleep(3)
                try:
                    _save(job_id, result=result)
                except Exception:
                    pass

        Thread(target=ticker, daemon=True).start()
        for line in proc.stdout:
            add(line)
        code = proc.wait()

        if not report.exists():
            raise RuntimeError(f"La búsqueda terminó con código {code} sin generar reporte.")
        data = json.loads(report.read_text(encoding="utf-8"))
        report.unlink(missing_ok=True)
        stats = data.get("stats") or {}
        result["stats"] = stats

        upserted = _upsert_inventory(result["project_guid"], data.get("candidates") or [])
        add(f"Inventario actualizado: {upserted} NWD en la lista de federados.")

        try:
            from .aps_viewer import resolve_acc_derivative
            from .viewer_db import refresh_selected_remote
            refresh_selected_remote(result["project_guid"], resolve_acc_derivative)
            add("Modelo gerencial re-verificado con su última versión.")
        except Exception as exc:
            add(f"Aviso: no se pudo re-verificar el modelo gerencial ({exc}).")

        if code not in (0, 1):
            raise RuntimeError(f"La búsqueda terminó con código {code}.")
        if code == 1:
            add("Aviso: algunas carpetas no se pudieron leer (ver arriba).")
        _save(job_id, status="completed", result=result, finished=True)
    except Exception as exc:
        add(f"ERROR: {exc}")
        _save(job_id, status="failed", result=result, error=str(exc), finished=True)


def _upsert_inventory(project_guid, candidates):
    """Registra los NWD encontrados en acc_folders/acc_items (fuente de la lista de federados)."""
    n = 0
    with models.engine.begin() as conn:
        for c in candidates:
            item_id, folder_id = c.get("item_id"), c.get("folder_id")
            if not item_id or not folder_id:
                continue
            remote = c.get("remote") or {}
            conn.execute(
                text(
                    """
                    INSERT INTO acc_folders (folder_id, project_guid, folder_path, updated_at, is_active)
                    VALUES (:f, :g, :p, now(), true)
                    ON CONFLICT (folder_id) DO NOTHING
                    """
                ),
                {"f": folder_id, "g": project_guid, "p": c.get("folder_path") or "/"},
            )
            conn.execute(
                text(
                    """
                    INSERT INTO acc_items (item_id, project_guid, folder_id, item_name, ext,
                                           version_count, latest_version_id, last_modified,
                                           folder_path_cached, updated_at, is_active)
                    VALUES (:i, :g, :f, :n, 'nwd', :vc, :lv, CAST(:lm AS timestamptz), :fp, now(), true)
                    ON CONFLICT (item_id) DO UPDATE SET
                        folder_id=EXCLUDED.folder_id,
                        item_name=EXCLUDED.item_name,
                        version_count=COALESCE(EXCLUDED.version_count, acc_items.version_count),
                        latest_version_id=COALESCE(EXCLUDED.latest_version_id, acc_items.latest_version_id),
                        last_modified=COALESCE(EXCLUDED.last_modified, acc_items.last_modified),
                        folder_path_cached=EXCLUDED.folder_path_cached,
                        updated_at=now(),
                        is_active=true
                    """
                ),
                {
                    "i": item_id, "g": project_guid, "f": folder_id, "n": c.get("name") or "",
                    "vc": _int(remote.get("version_number")), "lv": remote.get("version_id"),
                    "lm": remote.get("last_modified"), "fp": c.get("folder_path"),
                },
            )
            n += 1
    return n


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
