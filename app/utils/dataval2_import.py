"""
Indicadores de proyectos: carga la hoja DATA-VAL2 (CSV exportado del Drive,
p. ej. proyectos4-11.csv) en public.seguimiento_dataval2, la tabla que muestra
la ficha del proyecto en el visor ANIN 360.

Es la misma regla que tools/dataval2/importar.ts del repositorio ANIN 360
(hoja.ts): mismas columnas, misma limpieza (montos es-PE, porcentajes, fechas
dd/mm/aaaa) y solo las filas con "SI" en "Considerar visor BIM". Se usa desde
el botón "Actualizar indicadores" hasta que la OTI entregue una API.

La carga reemplaza todo el contenido en una sola transacción y antes guarda
la versión anterior en public.seguimiento_dataval2_respaldo (una sola copia).
"""
import csv
import io
import json
import os
import re
import urllib.request

from sqlalchemy import text

from .. import models

# Columna de la hoja -> campo de la tabla (igual que hoja.ts).
COLUMNAS = {
    "C": "cui", "D": "nombre_corto", "E": "direccion", "F": "unidad_funcional",
    "I": "wbs", "P": "departamento", "AA": "beneficiarios", "AH": "subdireccion",
    "AQ": "contratista", "BG": "etapa", "BH": "condicion", "BW": "inicio_diseno",
    "CC": "avance_diseno", "CH": "avance_construccion", "CR": "fin_construccion",
    "CY": "costo_proyectado", "DA": "costo_actualizado", "DB": "devengado_acumulado",
    "DC": "avance_financiero", "DM": "pim_2026", "DN": "devengado_2026",
}
CAMPOS = list(COLUMNAS.values())
ENCABEZADO_FILTRO = "Considerar visor BIM"
CAMPOS_TEXTO = {"cui", "nombre_corto", "direccion", "unidad_funcional", "wbs", "departamento",
                "subdireccion", "contratista", "etapa", "condicion"}
CAMPOS_FECHA = {"inicio_diseno", "fin_construccion"}

DDL = """
CREATE TABLE IF NOT EXISTS public.seguimiento_dataval2 (
    id serial PRIMARY KEY, wbs text NOT NULL, cui text, nombre_corto text, direccion text,
    subdireccion text, unidad_funcional text, departamento text, etapa text, condicion text,
    contratista text, beneficiarios numeric, inicio_diseno date, fin_construccion date,
    avance_diseno numeric, avance_construccion numeric, avance_financiero numeric,
    costo_actualizado numeric, costo_proyectado numeric, devengado_acumulado numeric,
    pim_2026 numeric, devengado_2026 numeric, fuente text,
    cargado_en timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS seguimiento_dataval2_wbs_idx ON public.seguimiento_dataval2 (wbs);
ALTER TABLE public.seguimiento_dataval2 ADD COLUMN IF NOT EXISTS inicio_diseno date;
ALTER TABLE public.seguimiento_dataval2 ADD COLUMN IF NOT EXISTS fin_construccion date;
ALTER TABLE public.seguimiento_dataval2 ADD COLUMN IF NOT EXISTS costo_proyectado numeric;
"""


def _indice(letras):
    n = 0
    for ch in letras:
        n = n * 26 + ord(ch) - 64
    return n - 1


def _numero(valor):
    """'1.239.023.216' / '322.038.608,68' / ' -   ' -> float o None (formato es-PE)."""
    s = str(valor or "").replace("S/", "").replace("%", "").strip()
    if s in ("", "-") or not re.search(r"\d", s):
        return None
    try:
        return float(s.replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _texto(valor):
    s = re.sub(r"\s+", " ", str(valor or "")).strip()
    return s if s and s != "-" else None


def _fecha(valor):
    """'19/12/2024' -> '2024-12-19'; otro texto -> None."""
    s = str(valor or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        return s
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
    if not m:
        return None
    d, mes, a = int(m.group(1)), int(m.group(2)), m.group(3)
    if not (1 <= mes <= 12 and 1 <= d <= 31):
        return None
    return f"{a}-{mes:02d}-{d:02d}"


def _limpiar(crudo):
    fila = {}
    for campo in CAMPOS:
        v = crudo.get(campo)
        if campo in CAMPOS_FECHA:
            fila[campo] = _fecha(v)
        elif campo in CAMPOS_TEXTO:
            fila[campo] = _texto(v)
        else:
            fila[campo] = _numero(v)
    # La condición viene como ".Normal"; el punto solo sirve para ordenar en el Excel.
    if fila.get("condicion"):
        fila["condicion"] = fila["condicion"].lstrip(". ") or None
    return fila


def leer_csv(contenido: bytes):
    """Filas de la hoja con WBS de 6 dígitos y "SI" en "Considerar visor BIM"."""
    texto = contenido.decode("utf-8-sig", errors="replace")
    filas = list(csv.reader(io.StringIO(texto, newline=""), delimiter=";"))
    i_wbs = _indice("I")
    cab = next((i for i, r in enumerate(filas)
                if len(r) > i_wbs and r[i_wbs].strip().upper() == "WBS"), None)
    if cab is None:
        raise ValueError("No es la hoja DATA-VAL2: no se encontró la fila de encabezados (columna I = 'WBS').")
    i_filtro = next((i for i, h in enumerate(filas[cab])
                     if (_texto(h) or "").lower() == ENCABEZADO_FILTRO.lower()), None)
    if i_filtro is None:
        raise ValueError(f"No se encontró la columna '{ENCABEZADO_FILTRO}' en la hoja.")
    salida = []
    for r in filas[cab + 1:]:
        if len(r) <= i_wbs or not re.fullmatch(r"\d{6}", r[i_wbs].strip()):
            continue
        marca = (r[i_filtro] if i_filtro < len(r) else "").strip().upper()
        if marca not in ("SI", "SÍ"):
            continue
        crudo = {campo: (r[_indice(col)] if _indice(col) < len(r) else None)
                 for col, campo in COLUMNAS.items()}
        salida.append(_limpiar(crudo))
    return salida


def estado_actual():
    """Lo que hay hoy en la tabla: filas, WBS, archivo de origen y fecha de carga."""
    with models.engine.begin() as conn:
        if conn.execute(text("SELECT to_regclass('public.seguimiento_dataval2')")).scalar() is None:
            return None
        r = conn.execute(text("""
            SELECT COUNT(*) AS filas, COUNT(DISTINCT wbs) AS wbs,
                   max(fuente) AS fuente, max(cargado_en) AS cargado_en
            FROM public.seguimiento_dataval2
        """)).mappings().first()
        respaldo = conn.execute(text("SELECT to_regclass('public.seguimiento_dataval2_respaldo')")).scalar()
        r = dict(r)
        if respaldo:
            r["respaldo"] = dict(conn.execute(text("""
                SELECT COUNT(*) AS filas, max(fuente) AS fuente, max(cargado_en) AS cargado_en
                FROM public.seguimiento_dataval2_respaldo
            """)).mappings().first())
        return r


def cargar(filas, fuente):
    """Respalda la tabla actual y la reemplaza por las filas nuevas (una transacción)."""
    if not filas:
        raise ValueError("El archivo no tiene proyectos marcados 'SI' en 'Considerar visor BIM'; no se modificó nada.")
    columnas = CAMPOS + ["fuente"]
    sql = (f"INSERT INTO public.seguimiento_dataval2 ({', '.join(columnas)}) "
           f"VALUES ({', '.join(':' + c for c in columnas)})")
    with models.engine.begin() as conn:
        for stmt in [s for s in DDL.split(";") if s.strip()]:
            conn.execute(text(stmt))
        conn.execute(text("DROP TABLE IF EXISTS public.seguimiento_dataval2_respaldo"))
        conn.execute(text("CREATE TABLE public.seguimiento_dataval2_respaldo AS TABLE public.seguimiento_dataval2"))
        conn.execute(text("DELETE FROM public.seguimiento_dataval2"))
        conn.execute(text(sql), [dict(f, fuente=fuente) for f in filas])
        r = conn.execute(text(
            "SELECT COUNT(*) AS filas, COUNT(DISTINCT wbs) AS wbs FROM public.seguimiento_dataval2"
        )).mappings().first()
    return dict(r)


def restaurar_respaldo():
    """Vuelve a la carga anterior (la copia de seguimiento_dataval2_respaldo)."""
    with models.engine.begin() as conn:
        if conn.execute(text("SELECT to_regclass('public.seguimiento_dataval2_respaldo')")).scalar() is None:
            raise ValueError("No hay una carga anterior guardada.")
        n = conn.execute(text("SELECT COUNT(*) FROM public.seguimiento_dataval2_respaldo")).scalar()
        if not n:
            raise ValueError("La copia anterior está vacía; no se restauró.")
        conn.execute(text("DELETE FROM public.seguimiento_dataval2"))
        conn.execute(text("""
            INSERT INTO public.seguimiento_dataval2
            SELECT * FROM public.seguimiento_dataval2_respaldo
        """))
    return n


def limpiar_cache_visor():
    """Pide al visor ANIN 360 vaciar su caché para que muestre los datos de inmediato."""
    base = (os.getenv("ANIN360_API_URL") or "https://anin360-api.onrender.com").rstrip("/")
    try:
        req = urllib.request.Request(f"{base}/api/cache/clear", data=b"", method="POST")
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read() or b"{}").get("ok", False)
    except Exception:
        # Sin respuesta: el visor toma los datos al vencer su caché (GIS_CACHE_SECONDS).
        return False
