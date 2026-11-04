"""
Página "Indicadores de proyectos": sube el CSV de la hoja DATA-VAL2 (p. ej.
proyectos4-11.csv), muestra una vista previa y, al confirmar, actualiza
public.seguimiento_dataval2 (ficha del proyecto en el visor ANIN 360).
Es una solución provisional hasta que la OTI entregue una API.
"""
import re
import tempfile
import uuid
from pathlib import Path

from flask import Blueprint, redirect, render_template, request, url_for

from ..utils.dataval2_import import (
    cargar,
    estado_actual,
    leer_csv,
    limpiar_cache_visor,
    restaurar_respaldo,
)

indicadores_bp = Blueprint("indicadores", __name__)

# Archivos subidos esperando confirmación (se borran al confirmar).
PENDIENTES = Path(tempfile.gettempdir()) / "anin_indicadores"


def _pendiente(token):
    if not re.fullmatch(r"[0-9a-f]{32}", token or ""):
        return None
    p = PENDIENTES / f"{token}.csv"
    return p if p.exists() else None


@indicadores_bp.get("/indicadores")
def indicadores():
    return render_template(
        "indicadores.html",
        actual=estado_actual(),
        ok=request.args.get("ok"),
        error=request.args.get("error"),
        cache=request.args.get("cache"),
    )


@indicadores_bp.post("/indicadores/previa")
def indicadores_previa():
    """Lee el CSV subido y muestra cuántos proyectos se cargarían, sin tocar la base."""
    archivo = request.files.get("archivo")
    if not archivo or not archivo.filename:
        return redirect(url_for("indicadores.indicadores", error="Elija el archivo CSV de la hoja DATA-VAL2."))
    contenido = archivo.read()
    try:
        filas = leer_csv(contenido)
    except ValueError as exc:
        return redirect(url_for("indicadores.indicadores", error=str(exc)))
    if not filas:
        return redirect(url_for("indicadores.indicadores",
                                error="El archivo no tiene proyectos marcados 'SI' en 'Considerar visor BIM'."))
    PENDIENTES.mkdir(parents=True, exist_ok=True)
    token = uuid.uuid4().hex
    (PENDIENTES / f"{token}.csv").write_bytes(contenido)
    actual = estado_actual()
    wbs = sorted({f["wbs"] for f in filas})
    return render_template(
        "indicadores.html",
        actual=actual,
        previa={
            "token": token,
            "nombre": Path(archivo.filename).name,
            "filas": len(filas),
            "wbs": len(wbs),
            # Si llegan muchos menos proyectos que hoy, se pide confirmar a propósito.
            "caida": bool(actual and actual.get("filas") and len(filas) < 0.5 * actual["filas"]),
            "muestra": filas[:10],
        },
    )


@indicadores_bp.post("/indicadores/cargar")
def indicadores_cargar():
    """Confirma la carga: respalda la tabla, la reemplaza y limpia la caché del visor."""
    ruta = _pendiente(request.form.get("token"))
    if not ruta:
        return redirect(url_for("indicadores.indicadores", error="La vista previa venció; vuelva a subir el archivo."))
    nombre = Path(request.form.get("nombre") or ruta.name).name
    try:
        r = cargar(leer_csv(ruta.read_bytes()), nombre)
    except ValueError as exc:
        return redirect(url_for("indicadores.indicadores", error=str(exc)))
    finally:
        ruta.unlink(missing_ok=True)
    cache = "1" if limpiar_cache_visor() else "0"
    return redirect(url_for("indicadores.indicadores",
                            ok=f"Cargados {r['filas']} registros ({r['wbs']} WBS) desde {nombre}.", cache=cache))


@indicadores_bp.post("/indicadores/restaurar")
def indicadores_restaurar():
    """Deshace la última carga con la copia guardada antes de ella."""
    try:
        n = restaurar_respaldo()
    except ValueError as exc:
        return redirect(url_for("indicadores.indicadores", error=str(exc)))
    cache = "1" if limpiar_cache_visor() else "0"
    return redirect(url_for("indicadores.indicadores", ok=f"Se restauró la carga anterior ({n} registros).", cache=cache))
