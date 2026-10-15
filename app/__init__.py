# app/__init__.py
from pathlib import Path
import hmac
import os

from dotenv import load_dotenv
from flask import Flask, Response, redirect, request

# Cargar .env ANTES de importar rutas/utilidades que leen variables a nivel módulo.
ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(dotenv_path=ENV_FILE)

from .routes.main import main_bp
from .routes.api import api_bp
from .routes.portfolio import portfolio_bp
from .routes.viewer_manager import viewer_bp


def create_app():
    app = Flask(
        __name__,
        template_folder="templates",
        static_folder="static",
        static_url_path="/admin/static",
    )

    app.config.from_prefixed_env()

    gis_exts = os.getenv("GIS_EXTENSIONS", "shp,kml,kmz,zip,gdb,geojson")
    app.config["GIS_EXTENSIONS"] = set(gis_exts.lower().split(","))
    app.config["DOWNLOAD_BASE"] = os.getenv("DOWNLOAD_BASE", "downloads")
    app.config["SUBTYPE_ALTERACION"] = os.getenv("SUBTYPE_ALTERACION", "ALTERACIÓN")
    app.config["SUBTYPE_FALTA_FIRMA"] = os.getenv("SUBTYPE_FALTA_FIRMA", "FALTA FIRMA")
    # Espejos SVF locales: ocupan mucho disco. En la nube se apagan con MIRROR_ENABLED=false.
    app.config["MIRROR_ENABLED"] = os.getenv("MIRROR_ENABLED", "true").lower() == "true"

    # Acceso con usuario y clave (Basic Auth) solo si ADMIN_PASSWORD está definido.
    admin_user = os.getenv("ADMIN_USER", "admin")
    admin_password = os.getenv("ADMIN_PASSWORD", "")

    @app.before_request
    def require_login():
        if not admin_password or request.path == "/admin/health":
            return None
        auth = request.authorization
        if (
            auth
            and hmac.compare_digest(auth.username or "", admin_user)
            and hmac.compare_digest(auth.password or "", admin_password)
        ):
            return None
        return Response(
            "Acceso restringido.",
            401,
            {"WWW-Authenticate": 'Basic realm="Administrador ACC"'},
        )

    @app.get("/admin/health")
    def health():
        return {"ok": True}

    from . import models
    models.init_app(app)

    @app.template_filter("filesize")
    def filesize_filter(value):
        try:
            n = float(value or 0)
        except Exception:
            return "—"
        units = ["B", "KB", "MB", "GB", "TB"]
        idx = 0
        while n >= 1024 and idx < len(units) - 1:
            n /= 1024.0
            idx += 1
        return f"{n:.1f} {units[idx]}" if idx else f"{int(n)} B"

    @app.template_filter("pct")
    def pct_filter(value):
        if value is None:
            return "—"
        try:
            n = float(value)
        except Exception:
            return str(value)
        if -1 <= n <= 1:
            n *= 100
        return f"{n:.1f}%"

    app.register_blueprint(main_bp, url_prefix="/admin")
    app.register_blueprint(api_bp, url_prefix="/admin/api")
    app.register_blueprint(portfolio_bp, url_prefix="/admin")
    app.register_blueprint(viewer_bp, url_prefix="/admin")

    if os.getenv("ENABLE_SCHEDULER", "false").lower() == "true":
        from apscheduler.schedulers.background import BackgroundScheduler
        from .utils.gis_processor import detect_gis_folders
        scheduler = BackgroundScheduler()
        scheduler.add_job(
            func=detect_gis_folders,
            trigger="cron",
            hour=3,
            minute=0,
            args=[app.config["GIS_EXTENSIONS"]],
        )
        scheduler.start()
        app.config["SCHEDULER"] = scheduler

    @app.route("/")
    def root_redirect():
        return redirect("/admin/projects")

    return app
