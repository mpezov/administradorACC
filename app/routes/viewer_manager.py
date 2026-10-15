from flask import (
    Blueprint,
    Response,
    current_app,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from ..utils.aps_viewer import public_viewer_token, resolve_acc_derivative
from ..utils.local_mirror import prepare_local_mirror, get_cached_or_fetch
from ..utils.viewer_db import (
    schema_ready,
    mirror_schema_ready,
    select_managerial_model,
    get_selected_model,
    get_local_mirror,
    refresh_selected_remote,
    get_viewer_model,
)

viewer_bp = Blueprint("viewer_manager", __name__)

MIRROR_DISABLED_MSG = (
    "Espejos desactivados en este servidor (MIRROR_ENABLED=false). "
    "Use el visor ACC remoto."
)


def _mirror_enabled():
    return current_app.config.get("MIRROR_ENABLED", True)


@viewer_bp.post("/project/<project_guid>/viewer/select")
def select_viewer_model(project_guid):
    if not schema_ready():
        return "Catálogo BIM no instalado. Ejecute migrar_viewer_manager.py.", 503

    item_id = (request.form.get("item_id") or "").strip()
    if not item_id:
        return "Falta item_id.", 400

    try:
        select_managerial_model(project_guid, item_id)
        try:
            refresh_selected_remote(project_guid, resolve_acc_derivative)
        except Exception:
            # La selección queda persistida aunque APS no pueda verificarse ahora.
            pass
    except ValueError as exc:
        return str(exc), 400
    except Exception as exc:
        return f"No se pudo seleccionar el modelo: {exc}", 500

    return redirect(
        url_for("portfolio.project_detail", project_guid=project_guid, viewer="selected")
    )


@viewer_bp.post("/project/<project_guid>/viewer/check")
def check_viewer_model(project_guid):
    if not schema_ready():
        return "Catálogo BIM no instalado.", 503

    try:
        refresh_selected_remote(project_guid, resolve_acc_derivative)
    except Exception as exc:
        return redirect(
            url_for(
                "portfolio.project_detail",
                project_guid=project_guid,
                viewer_error=str(exc),
            )
        )

    return redirect(
        url_for("portfolio.project_detail", project_guid=project_guid, viewer="checked")
    )


@viewer_bp.get("/project/<project_guid>/viewer")
def remote_viewer(project_guid):
    try:
        model = get_viewer_model(project_guid)
    except Exception as exc:
        return redirect(
            url_for(
                "portfolio.project_detail",
                project_guid=project_guid,
                viewer_error=str(exc),
            )
        )

    return render_template(
        "viewer_remote.html",
        model=model,
        project_guid=project_guid,
        viewer_version="7.108.0",
    )


@viewer_bp.post("/project/<project_guid>/mirror/prepare")
def prepare_mirror(project_guid):
    if not _mirror_enabled():
        return redirect(
            url_for(
                "portfolio.project_detail",
                project_guid=project_guid,
                viewer_error=MIRROR_DISABLED_MSG,
            )
        )
    if not schema_ready():
        return "Catálogo BIM no instalado.", 503
    if not mirror_schema_ready():
        return redirect(
            url_for(
                "portfolio.project_detail",
                project_guid=project_guid,
                viewer_error=(
                    "Mirror Cache no instalado. Ejecute "
                    "python .\\migrar_mirror_v0_5.py"
                ),
            )
        )

    try:
        model = get_viewer_model(project_guid)
        prepare_local_mirror(model, project_guid)
    except Exception as exc:
        return redirect(
            url_for(
                "portfolio.project_detail",
                project_guid=project_guid,
                viewer_error=str(exc),
            )
        )

    return redirect(
        url_for(
            "portfolio.project_detail",
            project_guid=project_guid,
            mirror="prepared",
        )
    )


@viewer_bp.get("/project/<project_guid>/viewer-mirror")
def mirror_viewer(project_guid):
    if not _mirror_enabled():
        return redirect(
            url_for(
                "portfolio.project_detail",
                project_guid=project_guid,
                viewer_error=MIRROR_DISABLED_MSG,
            )
        )
    if not mirror_schema_ready():
        return redirect(
            url_for(
                "portfolio.project_detail",
                project_guid=project_guid,
                viewer_error="Mirror Cache v0.5 no instalado.",
            )
        )

    selected = get_selected_model(project_guid)
    if not selected or not selected.get("local_version_id"):
        return redirect(
            url_for(
                "portfolio.project_detail",
                project_guid=project_guid,
                viewer_error="No existe espejo para la versión vigente.",
            )
        )

    mirror = get_local_mirror(selected["local_version_id"])
    if not mirror:
        return redirect(
            url_for(
                "portfolio.project_detail",
                project_guid=project_guid,
                viewer_error="No se pudo recuperar el espejo local.",
            )
        )

    fmt = str(
        mirror.get("remote_derivative_format")
        or mirror.get("derivative_format")
        or ""
    ).upper()
    if fmt != "SVF":
        return redirect(
            url_for(
                "portfolio.project_detail",
                project_guid=project_guid,
                viewer_error=(
                    f"El derivative es {fmt or 'desconocido'}. "
                    "Mirror Cache v0.5 admite SVF clásico."
                ),
            )
        )

    if mirror.get("remote_status") != "READY" or not mirror.get("derivative_urn"):
        return redirect(
            url_for(
                "portfolio.project_detail",
                project_guid=project_guid,
                viewer_error="El derivative remoto no está READY.",
            )
        )

    return render_template(
        "viewer_mirror.html",
        model=mirror,
        project_guid=project_guid,
        viewer_version="7.108.0",
        proxy_endpoint=f"/admin/viewer-cache/{mirror['id']}",
    )


@viewer_bp.route(
    "/viewer-cache/<int:local_version_id>/<path:proxy_path>",
    methods=["GET", "HEAD"],
)
def mirror_proxy(local_version_id, proxy_path):
    if not _mirror_enabled():
        return Response(MIRROR_DISABLED_MSG, status=404, mimetype="text/plain")
    if request.headers.get("Upgrade", "").lower() == "websocket":
        return Response(
            "Mirror Cache v0.5 no implementa proxy WebSocket.",
            status=501,
            mimetype="text/plain",
        )

    raw_uri = (
        request.environ.get("RAW_URI")
        or request.environ.get("REQUEST_URI")
        or ""
    )
    if raw_uri:
        raw_path = raw_uri.split("?", 1)[0]
        prefix = f"/admin/viewer-cache/{local_version_id}/"
        if raw_path.startswith(prefix):
            proxy_path = raw_path[len(prefix):]

    try:
        result = get_cached_or_fetch(
            local_version_id=local_version_id,
            proxy_path=proxy_path,
            query_string=request.query_string.decode("latin-1"),
            range_header=request.headers.get("Range"),
            method=request.method,
        )
    except ValueError as exc:
        return Response(str(exc), status=400, mimetype="text/plain")
    except Exception as exc:
        return Response(
            f"Error del espejo: {exc}",
            status=502,
            mimetype="text/plain",
        )

    if request.method == "HEAD":
        response = Response(status=result["status_code"])
    elif result.get("path"):
        response = send_file(
            result["path"],
            conditional=False,
            max_age=0,
        )
        response.status_code = result["status_code"]
    else:
        response = Response(
            result.get("body", b""),
            status=result["status_code"],
        )

    for key, value in (result.get("headers") or {}).items():
        response.headers[key] = value

    response.headers["X-ANIN-Mirror"] = result.get("source", "UNKNOWN")
    return response


@viewer_bp.get("/api/viewer/token")
def viewer_token():
    try:
        return jsonify(public_viewer_token())
    except Exception as exc:
        return jsonify({
            "error": "VIEWER_TOKEN_UNAVAILABLE",
            "detail": str(exc),
        }), 503


@viewer_bp.get("/api/project/<project_guid>/viewer-state")
def viewer_state(project_guid):
    if not schema_ready():
        return jsonify({"catalog_ready": False}), 503

    selected = get_selected_model(project_guid)
    mirror = None
    if (
        mirror_schema_ready()
        and selected
        and selected.get("local_version_id")
    ):
        mirror = get_local_mirror(selected["local_version_id"])

    return jsonify({
        "catalog_ready": True,
        "mirror_ready": mirror_schema_ready(),
        "model": selected,
        "mirror": mirror,
    })
