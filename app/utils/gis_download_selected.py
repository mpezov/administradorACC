import os
import re
import zipfile
import shutil
import subprocess
from pathlib import Path
from sqlalchemy import text

from app import models
from app.utils.acc_client import APSClient

GIS_EXTS = {
    "shp", "dbf", "shx", "prj", "cpg",
    "geotif", "kml", "kmz", "geojson", "json",
    "dwg", "dxf", "tif", "tiff"
}


def safe_name(value: str) -> str:
    value = value or "sin_nombre"
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)


def get_folder_items(project_guid: str, folder_id: str):
    query = """
    SELECT
        i.item_id,
        i.item_name,
        i.ext,
        i.latest_version_id,
        v.storage_bucket,
        v.storage_key
    FROM public.acc_items i
    LEFT JOIN public.acc_item_versions v
      ON v.version_id = i.latest_version_id
    WHERE i.project_guid = :pg
      AND i.folder_id = :fid
      AND lower(regexp_replace(coalesce(i.ext, ''), '^\\.', '')) = ANY(:exts)
    ORDER BY i.item_name;
    """
    with models.engine.begin() as conn:
        rows = conn.execute(
            text(query),
            {
                "pg": project_guid,
                "fid": folder_id,
                "exts": list(GIS_EXTS),
            }
        ).mappings().all()
    return [dict(r) for r in rows]


def download_storage_object(client: APSClient, bucket: str, key: str, output_path: Path):
    """
    Descarga objeto OSS usando bucket/key.
    Ojo: esto asume que APSClient tiene método download_object o request.
    """
    url = f"https://developer.api.autodesk.com/oss/v2/buckets/{bucket}/objects/{key}"

    response = client.session.get(
        url,
        headers={
            "Authorization": f"Bearer {client.get_token()}",
            "Accept": "application/octet-stream",
        },
        stream=True,
        timeout=120,
    )
    response.raise_for_status()

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "wb") as f:
        for chunk in response.iter_content(chunk_size=1024 * 1024):
            if chunk:
                f.write(chunk)


def unzip_files(base_dir: Path):
    extracted = []

    for zip_path in base_dir.rglob("*.zip"):
        target_dir = zip_path.with_suffix("")
        target_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as z:
            z.extractall(target_dir)

        extracted.append(str(target_dir))

    return extracted


def convert_shp_to_geojson(base_dir: Path):
    """
    Conversión usando ogr2ogr si está instalado.
    Es lo más estable para shapefile.
    """
    converted = []

    ogr2ogr = shutil.which("ogr2ogr")
    if not ogr2ogr:
        return {
            "converted": converted,
            "warning": "No se encontró ogr2ogr en PATH. Se descargó/descomprimió, pero no se convirtió a GeoJSON."
        }

    for shp_path in base_dir.rglob("*.shp"):
        geojson_path = shp_path.with_suffix(".geojson")

        cmd = [
            ogr2ogr,
            "-f", "GeoJSON",
            str(geojson_path),
            str(shp_path)
        ]

        subprocess.run(cmd, check=True)
        converted.append(str(geojson_path))

    return {
        "converted": converted,
        "warning": None
    }


def download_selected_gis_folder(project_guid: str, folder_id: str):
    items = get_folder_items(project_guid, folder_id)

    base_dir = Path("downloads") / "gis" / safe_name(project_guid) / safe_name(folder_id)
    base_dir.mkdir(parents=True, exist_ok=True)

    client = APSClient()

    downloaded = []
    skipped = []

    for item in items:
        item_name = item["item_name"]
        bucket = item.get("storage_bucket")
        key = item.get("storage_key")

        if not bucket or not key:
            skipped.append({
                "item_name": item_name,
                "reason": "Sin storage_bucket/storage_key en acc_item_versions"
            })
            continue

        output_path = base_dir / safe_name(item_name)

        try:
            download_storage_object(client, bucket, key, output_path)
            downloaded.append(str(output_path))
        except Exception as e:
            skipped.append({
                "item_name": item_name,
                "reason": str(e)
            })

    extracted = unzip_files(base_dir)
    conversion = convert_shp_to_geojson(base_dir)

    return {
        "folder_id": folder_id,
        "items_found": len(items),
        "downloaded": len(downloaded),
        "downloaded_files": downloaded,
        "unzipped_dirs": extracted,
        "geojson_files": conversion["converted"],
        "warning": conversion["warning"],
        "skipped": skipped,
    }