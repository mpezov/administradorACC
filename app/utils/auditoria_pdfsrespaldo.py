# app/utils/auditoria_pdfs.py
import os
import re
import csv
import hashlib
import json
from typing import List, Dict, Tuple, Optional
from urllib.parse import unquote, quote

import fitz  # PyMuPDF
import pikepdf

# Importa el cliente desde acc_client (debe estar en el mismo paquete)
from .acc_client import APSClient

# ------------------------------------------------------------
# Funciones auxiliares (copiadas de tu script)
# ------------------------------------------------------------
def es_pdf(nombre: str) -> bool:
    return nombre.lower().endswith(".pdf")

def es_r(nombre_pdf: str) -> bool:
    return os.path.splitext(nombre_pdf)[0].endswith("[R]")

def nombre_r_equivalente(nombre_pdf: str) -> str:
    if not es_pdf(nombre_pdf):
        return nombre_pdf
    base = nombre_pdf[:-4]
    if base.endswith("[R]"):
        return nombre_pdf
    return f"{base}[R].pdf"

def normalizar_key(nombre_pdf: str) -> str:
    base = os.path.splitext(nombre_pdf)[0]
    base = re.sub(r"\s+", " ", base).strip()
    return base

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()

def project_id_sin_b(project_id: str) -> str:
    return project_id[2:] if project_id.startswith("b.") else project_id

# ------------------------------------------------------------
# Funciones de extracción de datos de ACC (usando el cliente)
# ------------------------------------------------------------
def listar_contents_folder(client: APSClient, project_id: str, folder_id: str) -> List[dict]:
    return client.list_folder_contents(project_id, folder_id)

def recolectar_items_recursivo(client: APSClient, project_id: str, folder_id: str) -> List[dict]:
    items = []
    queue = [folder_id]
    visited = set()
    while queue:
        fid = queue.pop(0)
        if fid in visited:
            continue
        visited.add(fid)
        for it in listar_contents_folder(client, project_id, fid):
            if it.get("type") == "folders":
                queue.append(it["id"])
            elif it.get("type") == "items":
                items.append(it)
    return items

def obtener_version_actual(client: APSClient, project_id: str, item_id: str) -> Optional[dict]:
    versions = client.get_item_versions(project_id, item_id)
    return versions[0] if versions else None

def storage_path_de_version(version_data: dict) -> Optional[str]:
    try:
        return version_data["relationships"]["storage"]["data"]["id"].split(":")[-1]
    except Exception:
        return None

def descargar_desde_storage(client: APSClient, storage_path: str, ruta_destino: str) -> None:
    client.download_file(storage_path, ruta_destino)

# ------------------------------------------------------------
# Funciones de análisis de PDF
# ------------------------------------------------------------
def comparar_pdf_texto(path_a: str, path_b: str) -> Tuple[bool, List[int]]:
    doc_a = fitz.open(path_a)
    doc_b = fitz.open(path_b)
    max_pages = max(doc_a.page_count, doc_b.page_count)
    dif = []
    for i in range(max_pages):
        if i >= doc_a.page_count or i >= doc_b.page_count:
            dif.append(i + 1)
            continue
        ta = " ".join((doc_a.load_page(i).get_text("text") or "").split())
        tb = " ".join((doc_b.load_page(i).get_text("text") or "").split())
        if ta != tb:
            dif.append(i + 1)
    doc_a.close()
    doc_b.close()
    return (len(dif) == 0), dif

def tiene_firma_criptografica(path: str) -> bool:
    def deref(x):
        try:
            return x.get_object()
        except Exception:
            return x

    def walk_fields(field_obj):
        field = deref(field_obj)
        yield field
        for k in field.get("/Kids", []):
            yield from walk_fields(k)

    firmas = []
    with pikepdf.open(path) as pdf:
        acro = pdf.Root.get("/AcroForm", None)
        if not acro:
            return False
        acro = deref(acro)
        for f0 in acro.get("/Fields", []):
            for f in walk_fields(f0):
                if f.get("/FT", None) == "/Sig":
                    v = f.get("/V", None)
                    info = {"has_value": v is not None}
                    if v is not None:
                        vobj = deref(v)
                        info["has_contents"] = "/Contents" in vobj
                        info["has_byterange"] = "/ByteRange" in vobj
                    firmas.append(info)
    for f in firmas:
        if f.get("has_value") and f.get("has_contents") and f.get("has_byterange"):
            return True
    return False

# ------------------------------------------------------------
# Lógica de evaluación de incidencias
# ------------------------------------------------------------
def evaluar_incidencia(
    falta_clon: bool,
    base_aceptado: bool,
    texto_igual: Optional[bool],
    firma_base: Optional[bool],
    firma_r: Optional[bool],
    paginas_dif: List[int],
) -> Tuple[Optional[str], str]:
    if texto_igual is False:
        return "ALTERACIÓN", f"Texto diferente entre base y [R]. Páginas: {paginas_dif}"
    if falta_clon:
        return "FALTA FIRMA", "No existe el clon [R]."
    if not base_aceptado:
        return "FALTA FIRMA", "Documento base NO está Aceptado."
    if firma_base is False:
        return "FALTA FIRMA", "Documento base sin firma digital."
    if firma_r is False:
        return "FALTA FIRMA", "Documento [R] sin firma digital (GP no firmó)."
    return None, ""

def _check_approval(client: APSClient, project_no_b: str, version_id: str) -> Tuple[bool, str]:
    try:
        data = client.get_version_approval_status(project_no_b, version_id)
        blob = json.dumps(data, ensure_ascii=False).lower()
        if "aceptado" in blob:
            return True, "ACEPTADO"
        return False, "NO_ACEPTADO"
    except Exception as e:
        code = getattr(e, 'response', None) and e.response.status_code
        if code == 404:
            return False, "SIN_WORKFLOW"
        return False, f"ERROR_{code}"

# ------------------------------------------------------------
# Función principal (ahora con logs y manejo de errores)
# ------------------------------------------------------------
def run_audit(
    project_guid: str,
    folder_id: str,
    download_dir: str,
    client: APSClient,
    subtype_alteracion: str,
    subtype_falta_firma: str,
) -> dict:
    """
    Ejecuta la auditoría de PDFs.
    Si no se pueden obtener los subtipos, retorna un resultado con error
    pero la tarea continúa (no lanza excepción).
    """
    project_no_b = project_id_sin_b(project_guid)

    # Logs para depuración
    print(f"🔍 Proyecto (sin b): {project_no_b}")
    print(f"📂 Carpeta: {folder_id}")
    print(f"📂 run_audit: folder_id recibido = '{folder_id}' (len={len(folder_id)})")
    # --- Intentar obtener los subtipos, si falla, retornar error controlado ---
    try:
        issue_types_data = client.get_issue_types(project_no_b)
        print(f"📥 Respuesta de issue-types: {json.dumps(issue_types_data, indent=2)[:500]}")  # primeros 500 chars
        sid_alter = None
        sid_firma = None
        for t in issue_types_data.get("results") or issue_types_data.get("data") or []:
            for st in t.get("subtypes") or []:
                tit = (st.get("title") or "").strip().lower()
                if tit == subtype_alteracion.strip().lower():
                    sid_alter = st["id"]
                elif tit == subtype_falta_firma.strip().lower():
                    sid_firma = st["id"]
        if not sid_alter or not sid_firma:
            return {
                "error": f"No se encontraron los subtypes: '{subtype_alteracion}' o '{subtype_falta_firma}'.",
                "comparados": 0,
                "issues_creadas": 0,
                "saltados": 0,
                "csv_path": None
            }
    except Exception as e:
        return {
            "error": f"Error al obtener subtypes: {str(e)}",
            "comparados": 0,
            "issues_creadas": 0,
            "saltados": 0,
            "csv_path": None
        }

    # --- Obtener estado por defecto para issues ---
    try:
        user_settings = client.get_user_issue_settings(project_no_b)
        default_status = (user_settings.get("issues", {}).get("new", {}).get("permittedStatuses") or ["open"])[0]
    except Exception as e:
        default_status = "open"
        print(f"⚠️ No se pudo obtener default status, usando 'open': {e}")

    # --- Recolectar items ---
    items = recolectar_items_recursivo(client, project_guid, folder_id)
    print(f"📄 Items encontrados: {len(items)}")

    # --- Indexar PDFs ---
    index = {}
    for it in items:
        nombre = (it.get("attributes", {}) or {}).get("displayName", "") or ""
        if not nombre or not es_pdf(nombre):
            continue
        try:
            v = obtener_version_actual(client, project_guid, it["id"])
            if not v:
                continue
            storage = storage_path_de_version(v)
            if not storage:
                continue
            index[nombre] = {
                "storage": storage,
                "version_id": v.get("id", ""),
                "item_id": it["id"],
            }
        except Exception as e:
            print(f"❌ Error indexando {nombre}: {e}")

    print(f"📌 PDFs indexados: {len(index)}")

    os.makedirs(download_dir, exist_ok=True)
    csv_path = os.path.join(download_dir, f"auditoria_{folder_id.replace(':','_')}.csv")
    issues_creadas = 0
    comparados = 0
    saltados = 0

    with open(csv_path, "w", newline="", encoding="utf-8") as fcsv:
        w = csv.writer(fcsv)
        w.writerow([
            "BaseKey", "PDF_Base", "PDF_[R]", "Base_Aceptado", "Motivo_Base",
            "R_Aceptado", "Motivo_R", "Texto_Igual", "Paginas_Dif",
            "Firma_Base", "Firma_[R]", "SHA256_Base", "SHA256_[R]",
            "Incidencia", "Motivo_Incidencia", "IssueId", "IssueDuplicado",
            "Ruta_Local_Base", "Ruta_Local_[R]", "Error"
        ])

        bases = [n for n in index.keys() if not es_r(n)]
        if not bases:
            print("⚠️ No hay PDFs base (sin [R]) en la carpeta/árbol seleccionado.")

        for nombre_base in sorted(bases):
            base_key = normalizar_key(nombre_base)
            nombre_r = nombre_r_equivalente(nombre_base)

            base_aceptado = False
            r_aceptado = False
            mot_base = ""
            mot_r = ""
            texto_igual = None
            paginas_dif = []
            firma_base = None
            firma_r = None
            hb = ""
            hr = ""
            incidencia = None
            motivo_incid = ""
            issue_id = ""
            issue_dup = "NO"
            err = ""

            local_base = os.path.join(download_dir, nombre_base)
            local_r = os.path.join(download_dir, nombre_r)

            try:
                falta_clon = nombre_r not in index
                if falta_clon:
                    incidencia, motivo_incid = evaluar_incidencia(
                        falta_clon=True,
                        base_aceptado=False,
                        texto_igual=None,
                        firma_base=None,
                        firma_r=None,
                        paginas_dif=[]
                    )
                    # Crear issue si no existe
                    title = f"{incidencia}: {base_key}"
                    desc = f"Norma carpeta controlada.\n\nBase: {nombre_base}\nClon esperado: {nombre_r}\n\nMotivo: {motivo_incid}"
                    try:
                        existente = client.search_issues(project_no_b, title)
                        if existente and existente.get("results"):
                            issue_id = existente["results"][0].get("id")
                            issue_dup = "SI"
                        else:
                            subtype_id = sid_firma
                            issue_data = {
                                "title": title[:250],
                                "description": desc[:10000],
                                "issueSubtypeId": subtype_id,
                                "status": default_status,
                                "locationDetails": "Auditoría automática"
                            }
                            new_issue = client.create_issue(project_no_b, issue_data)
                            issue_id = new_issue.get("id", "")
                            issues_creadas += 1
                    except Exception as e:
                        err = f"Error al crear issue: {str(e)}"
                    saltados += 1
                    print(f"🚨 {nombre_base} -> {incidencia} | {motivo_incid} | issue={issue_id} dup={issue_dup}")
                else:
                    base_aceptado, mot_base = _check_approval(client, project_no_b, index[nombre_base]["version_id"])
                    r_aceptado, mot_r = _check_approval(client, project_no_b, index[nombre_r]["version_id"])

                    descargar_desde_storage(client, index[nombre_base]["storage"], local_base)
                    descargar_desde_storage(client, index[nombre_r]["storage"], local_r)

                    hb = sha256_file(local_base)
                    hr = sha256_file(local_r)

                    texto_igual, paginas_dif = comparar_pdf_texto(local_base, local_r)
                    firma_base = tiene_firma_criptografica(local_base)
                    firma_r = tiene_firma_criptografica(local_r)

                    comparados += 1

                    incidencia, motivo_incid = evaluar_incidencia(
                        falta_clon=False,
                        base_aceptado=base_aceptado,
                        texto_igual=texto_igual,
                        firma_base=firma_base,
                        firma_r=firma_r,
                        paginas_dif=paginas_dif,
                    )

                    if incidencia:
                        title = f"{incidencia}: {base_key}"
                        desc = (
                            f"Norma carpeta controlada.\n\n"
                            f"Base: {nombre_base}\n[R]: {nombre_r}\n\n"
                            f"Estado base: {mot_base}\nEstado [R]: {mot_r}\n"
                            f"Firma base: {firma_base}\nFirma [R]: {firma_r}\n"
                            f"Texto igual: {texto_igual}\nPáginas dif: {paginas_dif}\n"
                            f"SHA256 base: {hb}\nSHA256 [R]: {hr}\n\n"
                            f"Motivo incidencia: {motivo_incid}"
                        )
                        try:
                            existente = client.search_issues(project_no_b, title)
                            if existente and existente.get("results"):
                                issue_id = existente["results"][0].get("id")
                                issue_dup = "SI"
                            else:
                                subtype_id = sid_alter if incidencia == "ALTERACIÓN" else sid_firma
                                issue_data = {
                                    "title": title[:250],
                                    "description": desc[:10000],
                                    "issueSubtypeId": subtype_id,
                                    "status": default_status,
                                    "locationDetails": "Auditoría automática"
                                }
                                new_issue = client.create_issue(project_no_b, issue_data)
                                issue_id = new_issue.get("id", "")
                                issues_creadas += 1
                        except Exception as e:
                            err = f"Error al crear issue: {str(e)}"
                        print(f"🚨 {nombre_base} -> {incidencia} | {motivo_incid} | issue={issue_id} dup={issue_dup}")
                    else:
                        print(f"✅ {nombre_base} vs {nombre_r} | Base({mot_base}) R({mot_r}) | Texto={texto_igual} | Firma base={firma_base} R={firma_r}")

            except Exception as e:
                err = repr(e)
                print(f"❌ Error procesando {nombre_base}: {err}")

            w.writerow([
                base_key, nombre_base, nombre_r if nombre_r in index else "",
                "SI" if base_aceptado else "NO", mot_base,
                "SI" if r_aceptado else "NO", mot_r,
                "" if texto_igual is None else ("SI" if texto_igual else "NO"),
                ",".join(map(str, paginas_dif)) if paginas_dif else "",
                "" if firma_base is None else ("SI" if firma_base else "NO"),
                "" if firma_r is None else ("SI" if firma_r else "NO"),
                hb, hr, incidencia or "", motivo_incid, issue_id, issue_dup,
                local_base if os.path.exists(local_base) else "",
                local_r if os.path.exists(local_r) else "", err
            ])

    print("\n===== RESUMEN =====")
    print(f"Comparaciones realizadas: {comparados}")
    print(f"Issues creadas: {issues_creadas}")
    print(f"Saltados (p.ej. falta clon): {saltados}")
    print(f"CSV: {csv_path}")

    return {
        "comparados": comparados,
        "issues_creadas": issues_creadas,
        "saltados": saltados,
        "csv_path": csv_path
    }