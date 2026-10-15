# app/routes/main.py
import json
from flask import Blueprint, render_template, request, redirect, url_for
from sqlalchemy import text
from .. import models
from ..utils.db import (
    list_projects, list_folders, build_tree, get_flags, upsert_flags,
    compute_name_path, list_items_for_folder
)
import os
# Obtener lista de extensiones de interés
interest_exts = os.getenv("INTEREST_EXTENSIONS", "dwg,dxf,tif,ifc,rvt,xml,zip,rar,kml,kmz,gdb,geojson,json,shp,dbf,aprx,idx,xml").split(',')
interest_exts = [e.strip().lower() for e in interest_exts if e.strip()]
main_bp = Blueprint('main', __name__)

@main_bp.route('/')
def index():
    start_project = os.getenv("ACC_START_PROJECT", "").strip() or None
    q = (request.args.get("q") or "").strip().lower()

    projects = list_projects()
    if q:
        projects = [p for p in projects if q in (p.name or "").lower() or q in (p.hub_name or "").lower()]

    selected_project = request.args.get("project") or start_project
    if selected_project and projects:
        guids = {p.project_guid for p in projects}
        if selected_project not in guids:
            selected_project = projects[0].project_guid if projects else None
    elif projects:
        selected_project = projects[0].project_guid

    folders = []
    children = {}
    by_id = {}
    roots = []
    if selected_project:
        folders = list_folders(selected_project)
        children, by_id = build_tree(folders)
        roots = children.get(None, [])
        
        # Buscar la carpeta "Project Files" entre las raíces
        project_files_root = None
        for rid in roots:
            label = by_id[rid].label.strip().lower()
            if label == 'project files':
                project_files_root = rid
                break
        
        if project_files_root:
            roots = [project_files_root]  # Solo mostramos Project Files
        else:
            roots = []  # No hay Project Files, árbol vacío

    open_ids = request.args.getlist("open")
    seen = set()
    open_ids = [x for x in open_ids if x and not (x in seen or seen.add(x))]
    open_set = set(open_ids)

    selected_folder = request.args.get("folder")
    folder_info = None
    if selected_folder and selected_folder in by_id:
        tg, inc = get_flags(selected_project, selected_folder)
        folder_info = {
            "folder_id": selected_folder,
            "label": by_id[selected_folder].label,
            "folder_path": by_id[selected_folder].folder_path,
            "name_path": compute_name_path(selected_folder, by_id),
            "tipo_gis": tg,
            "incidencias": inc,
        }
    show_items = request.args.get("show_items") == "1"
    items = []
    if show_items and selected_project and selected_folder and selected_folder in by_id:
        items = list_items_for_folder(selected_project, selected_folder)

    return render_template(
        "index.html",
        projects=projects,
        selected_project=selected_project,
        roots=roots,
        children=children,
        by_id=by_id,
        folder_info=folder_info,
        open_ids=open_ids,
        open_set=open_set,
        q=q,
        show_items=show_items,
        items=items,
    )

@main_bp.route('/save_flags', methods=['POST'])
def save_flags():
    project_guid = request.form.get("project_guid", "").strip()
    folder_id = request.form.get("folder_id", "").strip()
    tipo_gis = request.form.get("tipo_gis") == "on"
    incidencias = request.form.get("incidencias") == "on"

    if not project_guid or not folder_id:
        return redirect(url_for("main.index", project=project_guid))

    upsert_flags(project_guid, folder_id, tipo_gis, incidencias)
    return redirect(url_for("main.index", project=project_guid, folder=folder_id))

# -------------------------------------------------------------------
# ÚNICA RUTA PARA ESTADÍSTICAS (sin duplicados)
# -------------------------------------------------------------------
@main_bp.route('/stats')
def stats():
    with models.engine.connect() as conn:
        totals = conn.execute(text("""
            SELECT 
                (SELECT COUNT(*) FROM acc_projects) as total_projects,
                (SELECT COUNT(*) FROM acc_folders) as total_folders,
                (SELECT COUNT(*) FROM acc_items) as total_items,
                (SELECT COUNT(*) FROM acc_item_versions) as total_versions
        """)).mappings().first()

        ext_rows = conn.execute(text("""
        SELECT ext, COUNT(*) as count
        FROM acc_items
        WHERE ext IS NOT NULL AND ext != '' AND ext = ANY(:exts)
        GROUP BY ext
        ORDER BY count DESC
    """), {"exts": interest_exts}).mappings().all()

        extensions = {row['ext']: row['count'] for row in ext_rows} if ext_rows else {}

        top_versions = conn.execute(text("""
            SELECT 
                i.project_guid,
                p.name as project_name,
                i.folder_path_cached,
                i.item_name,
                i.version_count
            FROM acc_items i
            LEFT JOIN acc_projects p ON i.project_guid = p.id_guid
            WHERE i.version_count > 1
            ORDER BY i.version_count DESC
            LIMIT 20
        """)).mappings().all()

        top_projects = conn.execute(text("""
            SELECT 
                p.id_guid,
                p.name as project_name,
                p.hub_name,
                COUNT(i.item_id) as total_files
            FROM acc_projects p
            LEFT JOIN acc_items i ON p.id_guid = i.project_guid
            GROUP BY p.id_guid, p.name, p.hub_name
            ORDER BY total_files DESC
            LIMIT 20
        """)).mappings().all()

    return render_template(
        'stats.html',
        totals=totals,
        extensions=extensions,  # Se pasa el diccionario, no el JSON string
        top_versions=top_versions,
        top_projects=top_projects
    )