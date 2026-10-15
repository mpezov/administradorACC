import os
from flask import Blueprint, render_template, request
from ..utils.viewer_db import (
    schema_ready as viewer_schema_ready,
    mirror_schema_ready,
    get_selected_model,
    get_local_mirror,
)
from ..utils.portal_db import (
    DEFAULT_PROJECT_EXCLUDE_REGEX,
    list_project_portfolio,
    get_project_overview,
    list_current_bim_items,
    list_federated_candidates,
    get_project_bim_extension_stats,
    get_project_bim_folder_stats,
    get_inventory_run_status,
    get_bim_global_stats,
    get_top_bim_projects,
    get_top_bim_versions,
)

portfolio_bp = Blueprint("portfolio", __name__)

BIM_VIEW_EXTENSIONS = [
    e.strip().lower().lstrip(".")
    for e in os.getenv("BIM_VIEW_EXTENSIONS", "nwd,nwc,rvt,ifc").split(",")
    if e.strip() and e.strip().lower().lstrip(".") not in {"dwg","dgn","dxf"}
]
FEDERATED_CANDIDATE_MIN_SCORE = int(os.getenv("FEDERATED_CANDIDATE_MIN_SCORE", "70"))
FEDERATED_CANDIDATE_LIMIT = int(os.getenv("FEDERATED_CANDIDATE_LIMIT", "8"))

PROJECT_EXCLUDE_REGEX = os.getenv(
    "PORTFOLIO_EXCLUDE_REGEX",
    DEFAULT_PROJECT_EXCLUDE_REGEX,
)


@portfolio_bp.route("/projects")
def projects():
    q = (request.args.get("q") or "").strip()
    bim_filter = (request.args.get("bim") or "all").strip().lower()
    only_bim = True if bim_filter == "yes" else (False if bim_filter == "no" else None)
    scope = (request.args.get("scope") or "analysis").strip().lower()
    include_excluded = scope == "all"
    return render_template(
        "projects.html",
        projects=list_project_portfolio(
            q, only_bim, BIM_VIEW_EXTENSIONS,
            include_excluded=include_excluded,
            exclude_regex=PROJECT_EXCLUDE_REGEX,
        ),
        q=q,
        bim_filter=bim_filter,
        scope=scope,
        run_status=get_inventory_run_status(),
        global_stats=get_bim_global_stats(BIM_VIEW_EXTENSIONS, PROJECT_EXCLUDE_REGEX),
        bim_view_exts=BIM_VIEW_EXTENSIONS,
    )


@portfolio_bp.route("/project/<project_guid>")
def project_detail(project_guid):
    project = get_project_overview(project_guid, BIM_VIEW_EXTENSIONS)
    if not project:
        return "Proyecto no encontrado", 404

    catalog_ready = viewer_schema_ready()
    selected = get_selected_model(project_guid) if catalog_ready else None
    mirror = None
    if mirror_schema_ready() and selected and selected.get("local_version_id"):
        mirror = get_local_mirror(selected["local_version_id"])

    return render_template(
        "project_detail.html",
        project=project,
        candidates=list_federated_candidates(project_guid, FEDERATED_CANDIDATE_MIN_SCORE, FEDERATED_CANDIDATE_LIMIT),
        candidate_min_score=FEDERATED_CANDIDATE_MIN_SCORE,
        bim_items=list_current_bim_items(project_guid, BIM_VIEW_EXTENSIONS),
        ext_stats=get_project_bim_extension_stats(project_guid, BIM_VIEW_EXTENSIONS),
        folder_stats=get_project_bim_folder_stats(project_guid, BIM_VIEW_EXTENSIONS, 20),
        run_status=get_inventory_run_status(),
        bim_view_exts=BIM_VIEW_EXTENSIONS,
        viewer_catalog_ready=catalog_ready,
        mirror_schema_ready=mirror_schema_ready(),
        selected_model=selected,
        mirror_state=mirror,
        mirror_notice=(request.args.get("mirror") or ""),
        viewer_notice=(request.args.get("viewer") or ""),
        viewer_error=(request.args.get("viewer_error") or ""),
    )


@portfolio_bp.route("/bim-stats")
def bim_stats():
    return render_template(
        "bim_stats.html",
        totals=get_bim_global_stats(BIM_VIEW_EXTENSIONS, PROJECT_EXCLUDE_REGEX),
        top_projects=get_top_bim_projects(BIM_VIEW_EXTENSIONS, 25, PROJECT_EXCLUDE_REGEX),
        top_versions=get_top_bim_versions(BIM_VIEW_EXTENSIONS, 25, PROJECT_EXCLUDE_REGEX),
        run_status=get_inventory_run_status(),
        bim_view_exts=BIM_VIEW_EXTENSIONS,
    )
