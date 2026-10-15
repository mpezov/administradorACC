from flask import Blueprint, render_template
from ..utils.db import (
    get_stats_summary,
    get_stats_by_extension,
    get_top_items_by_versions,
    get_top_projects_by_items,
    get_top_folders_by_items
)

stats_bp = Blueprint('stats', __name__, url_prefix='/stats')

@stats_bp.route('/')
def index():
    summary = get_stats_summary()
    extensions = get_stats_by_extension(limit=15)
    top_items = get_top_items_by_versions(limit=20)
    top_projects = get_top_projects_by_items(limit=10)
    top_folders = get_top_folders_by_items(limit=20)
    return render_template(
        'stats.html',
        summary=summary,
        extensions=extensions,
        top_items=top_items,
        top_projects=top_projects,
        top_folders=top_folders
    )