# Administrador ACC · Portal BIM v0.3

Cambios principales:
- DWG/DGN/DXF fuera del Portal BIM (no se borran de ACC ni del Explorador).
- BIM/3D: NWD, RVT, IFC, NWC.
- Nueva lista reducida de candidatos federados: solo NWD, agrupados por nombre.
- Puntaje por FEDERADO/MASTER/INTEGRADO, PUB/COM, carpeta BIM/Federado y código WVS.
- Penalización por alcance parcial/disciplina y copias documentales.
- Exclusión de RECHAZADOS, obsoletos, backups y temporales.
- Estado visual: ACTIVO ACC, no “VIGENTE”.
- Tamaño desconocido se muestra como —.

Variables opcionales .env:
BIM_VIEW_EXTENSIONS=nwd,nwc,rvt,ifc
FEDERATED_CANDIDATE_MIN_SCORE=70
FEDERATED_CANDIDATE_LIMIT=8

No hay migración SQL.

Reemplazar:
app/utils/portal_db.py
app/routes/portfolio.py
app/templates/projects.html
app/templates/project_detail.html
app/templates/bim_stats.html
app/static/app.css

Luego reiniciar: python .\run.py
