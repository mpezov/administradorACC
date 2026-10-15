# app/utils/gis_processor.py
from .. import models
from sqlalchemy import text
from flask import current_app
import logging

logger = logging.getLogger(__name__)

# Extensiones GIS a detectar
GIS_EXTENSIONS = {
    # Shapefile
    '.shp', '.shx', '.dbf', '.prj', '.sbn', '.sbx', '.fbn', '.fbx', '.ain', '.aih', '.atx', '.ixs', '.mxs',
    # CAD
    '.dwg', '.dxf', '.dgn',
    # GeoJSON y formatos vectoriales
    '.geojson', '.json', '.topojson',
    # KML/KMZ
    '.kml', '.kmz',
    # Geodatabase
    '.gdb', '.gdbtable', '.gdbtablx', '.gdbindexes', '.spatialite', '.sqlite',
    # GIS raster
    '.tif', '.tiff', '.geotiff', '.img', '.ecw', '.jp2', '.sid',
    # Otros GIS
    '.gpx', '.osm', '.mif', '.mid', '.tab', '.qgs', '.qgz', '.qlr', '.qml', '.sld', '.gml', '.shp.xml'
}

def detect_gis_folders(project_guid=None, extensions=None):
    """
    Marca como tipo_gis = true las carpetas que contienen archivos con extensiones GIS.
    
    Args:
        project_guid: GUID del proyecto (opcional, si no se proporciona busca en todos)
        extensions: Lista de extensiones (opcional, usa GIS_EXTENSIONS por defecto)
    
    Returns:
        dict: Estadísticas de carpetas marcadas
    """
    try:
        # Usar extensiones por defecto si no se proporcionan
        if not extensions:
            extensions = list(GIS_EXTENSIONS)
        
        logger.info(f"🔍 Buscando carpetas GIS con extensiones: {len(extensions)} tipos")
        
        # Construir query base
        with models.engine.begin() as conn:
            # Verificar si la tabla existe
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS acc_utils_folder (
                    project_guid VARCHAR(255),
                    folder_id VARCHAR(255),
                    tipo_gis BOOLEAN DEFAULT FALSE,
                    incidencias BOOLEAN DEFAULT FALSE,
                    updated_at TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (project_guid, folder_id)
                )
            """))
            
            # Buscar carpetas con archivos GIS
            if project_guid:
                # Buscar solo en el proyecto seleccionado
                rows = conn.execute(
                    text("""
                        SELECT DISTINCT project_guid, folder_id
                        FROM acc_items
                        WHERE project_guid = :project_guid
                        AND ext = ANY(:exts)
                    """),
                    {"project_guid": project_guid, "exts": list(extensions)}
                ).fetchall()
                logger.info(f"📁 Proyecto filtrado: {project_guid}")
            else:
                # Buscar en todos los proyectos
                rows = conn.execute(
                    text("""
                        SELECT DISTINCT project_guid, folder_id
                        FROM acc_items
                        WHERE ext = ANY(:exts)
                    """),
                    {"exts": list(extensions)}
                ).fetchall()
            
            logger.info(f"📊 Encontradas {len(rows)} carpetas con archivos GIS")
            
            # Actualizar o insertar registros
            count = 0
            for proj, fid in rows:
                result = conn.execute(
                    text("""
                        INSERT INTO acc_utils_folder (project_guid, folder_id, tipo_gis, incidencias, updated_at)
                        VALUES (:proj, :fid, true, false, NOW())
                        ON CONFLICT (project_guid, folder_id) DO UPDATE
                        SET tipo_gis = true, updated_at = NOW()
                        RETURNING folder_id
                    """),
                    {"proj": proj, "fid": fid}
                )
                if result.rowcount > 0:
                    count += 1
                    logger.debug(f"✅ Carpeta marcada: {fid}")
            
            logger.info(f"✨ {count} carpetas marcadas como tipo_gis")
            
            return {
                "total_encontradas": len(rows),
                "actualizadas": count,
                "project_guid": project_guid or "todos"
            }
            
    except Exception as e:
        logger.error(f"❌ Error en detect_gis_folders: {str(e)}")
        raise

def get_gis_folders(project_guid=None):
    """
    Obtiene las carpetas marcadas como GIS.
    
    Args:
        project_guid: GUID del proyecto (opcional)
    
    Returns:
        list: Lista de carpetas GIS
    """
    try:
        with models.engine.begin() as conn:
            if project_guid:
                rows = conn.execute(
                    text("""
                        SELECT project_guid, folder_id, tipo_gis, incidencias, updated_at
                        FROM acc_utils_folder
                        WHERE project_guid = :project_guid AND tipo_gis = true
                        ORDER BY updated_at DESC
                    """),
                    {"project_guid": project_guid}
                ).fetchall()
            else:
                rows = conn.execute(
                    text("""
                        SELECT project_guid, folder_id, tipo_gis, incidencias, updated_at
                        FROM acc_utils_folder
                        WHERE tipo_gis = true
                        ORDER BY project_guid, updated_at DESC
                    """)
                ).fetchall()
            
            return [dict(row._mapping) for row in rows]
    except Exception as e:
        logger.error(f"❌ Error en get_gis_folders: {str(e)}")
        return []

def download_gis_folders(project_guid=None, folder_id=None):
    """
    Placeholder para la descarga GIS.
    Más adelante se implementará con la lógica del script acc_docs_downloader_view3.py.
    
    Args:
        project_guid: GUID del proyecto (opcional)
        folder_id: ID de carpeta específica (opcional)
    
    Returns:
        dict: Resultado de la operación
    """
    logger.info(f"📥 Descarga GIS - Proyecto: {project_guid}, Carpeta: {folder_id}")
    
    # TODO: Implementar lógica real de descarga
    return {
        "downloaded": 0,
        "message": "Funcionalidad en desarrollo - Próximamente disponible",
        "project_guid": project_guid,
        "folder_id": folder_id
    }