-- ============================================================================
-- 03_seed_perfiles_gerenciales.sql
-- Perfiles iniciales. Los selectors quedan PENDING_CONFIGURATION a propósito:
-- deben ajustarse a propiedades reales del modelo antes de generar layer_map.json.
-- ============================================================================

INSERT INTO public.bim_project_types(type_code,display_name,description) VALUES
('BUILDING','Edificio','Hospitales, centros de salud, educación y edificaciones'),
('ROAD','Carretera / movilidad','Carreteras, vías urbanas y proyectos lineales viales'),
('DRAINAGE','Drenaje','Drenaje pluvial, canales y obras hidráulicas de drenaje'),
('BRIDGE','Puente','Puentes y estructuras especiales'),
('RIVER_DEFENSE','Defensa ribereña','Ríos, quebradas, defensas y protección'),
('SANITATION','Agua y saneamiento','Redes, reservorios y estaciones'),
('ELECTRICAL','Eléctrico','Subestaciones y redes principales'),
('GENERIC','Genérico','Proyecto sin perfil especializado todavía')
ON CONFLICT(type_code) DO NOTHING;

INSERT INTO public.bim_layer_profiles(profile_code,project_type,display_name,description) VALUES
('BUILDING_MANAGER','BUILDING','Edificio gerencial','Arquitectura y contexto principal'),
('ROAD_MANAGER','ROAD','Carretera gerencial','Pavimento, bermas, drenaje y estructuras principales'),
('DRAINAGE_MANAGER','DRAINAGE','Drenaje gerencial','Canales y estructuras principales de drenaje'),
('BRIDGE_MANAGER','BRIDGE','Puente gerencial','Componentes estructurales principales'),
('RIVER_DEFENSE_MANAGER','RIVER_DEFENSE','Defensa ribereña gerencial','Protecciones, muros y terraplenes'),
('SANITATION_MANAGER','SANITATION','Saneamiento gerencial','Red troncal e infraestructura principal'),
('ELECTRICAL_MANAGER','ELECTRICAL','Eléctrico gerencial','Infraestructura eléctrica principal'),
('GENERIC_MANAGER','GENERIC','Perfil gerencial genérico','Geometría principal configurada por proyecto')
ON CONFLICT(profile_code) DO NOTHING;

INSERT INTO public.bim_layer_profile_items
(profile_code,layer_code,display_name,default_visible,required,sort_order)
VALUES
('BUILDING_MANAGER','ARCHITECTURE','Arquitectura',true,true,10),
('BUILDING_MANAGER','SITE','Exteriores / emplazamiento',true,false,20),

('ROAD_MANAGER','PAVEMENT','Asfalto / pavimento',true,true,10),
('ROAD_MANAGER','BERMS','Bermas',true,false,20),
('ROAD_MANAGER','DRAINAGE','Drenaje principal',true,false,30),
('ROAD_MANAGER','STRUCTURES','Estructuras principales',true,false,40),

('DRAINAGE_MANAGER','CHANNELS','Canales',true,true,10),
('DRAINAGE_MANAGER','CULVERTS','Alcantarillas',true,false,20),
('DRAINAGE_MANAGER','CHAMBERS','Buzones / cámaras',true,false,30),
('DRAINAGE_MANAGER','OUTFALLS','Descargas',true,false,40),

('BRIDGE_MANAGER','DECK','Tablero',true,true,10),
('BRIDGE_MANAGER','ABUTMENTS','Estribos',true,true,20),
('BRIDGE_MANAGER','PIERS','Pilares',true,false,30),
('BRIDGE_MANAGER','FOUNDATIONS','Cimentaciones',true,false,40),
('BRIDGE_MANAGER','BARRIERS','Barreras',true,false,50),

('RIVER_DEFENSE_MANAGER','PROTECTION','Protección',true,true,10),
('RIVER_DEFENSE_MANAGER','WALLS_GABIONS','Muros / gaviones',true,false,20),
('RIVER_DEFENSE_MANAGER','EMBANKMENT','Terraplén',true,false,30),
('RIVER_DEFENSE_MANAGER','DRAINAGE','Drenaje',true,false,40),

('SANITATION_MANAGER','TRUNK_NETWORK','Red troncal',true,true,10),
('SANITATION_MANAGER','RESERVOIRS','Reservorios',true,false,20),
('SANITATION_MANAGER','PUMP_STATIONS','Estaciones de bombeo',true,false,30),
('SANITATION_MANAGER','CHAMBERS_VALVES','Cámaras / válvulas principales',true,false,40),

('ELECTRICAL_MANAGER','SUBSTATIONS','Subestaciones',true,true,10),
('ELECTRICAL_MANAGER','PRIMARY_FEEDERS','Alimentadores principales',true,false,20),
('ELECTRICAL_MANAGER','POLES_TOWERS','Postes / torres principales',true,false,30),

('GENERIC_MANAGER','MAIN_GEOMETRY','Geometría principal',true,true,10)
ON CONFLICT(profile_code,layer_code) DO NOTHING;

INSERT INTO public.bim_extension_catalog(extension_code,display_name,description) VALUES
('GIS','GIS','Mapa y capas geográficas del proyecto'),
('ROAD_CONTROL','Control vial C1-C5','Módulo técnico de carretera C1-C5'),
('BUILDING_4D','Control 4D edificio','Planificación y avance BIM de edificaciones'),
('DOCUMENTS','Documentos','Acceso a documentos relacionados'),
('ISSUES','Incidencias','Incidencias y observaciones'),
('TECHNICAL_VIEWER','Visor técnico','Herramientas técnicas especializadas')
ON CONFLICT(extension_code) DO NOTHING;

INSERT INTO public.project_kpi_catalog
(kpi_code,display_name,unit,value_type,source_default,sort_order)
VALUES
('COST','Costo','PEN','NUMERIC','proyectos_avance',10),
('ACCRUED','Devengado acumulado','PEN','NUMERIC','proyectos_avance',20),
('PHYSICAL_PROGRESS','Avance físico','%','NUMERIC','proyectos_avance',30),
('FINANCIAL_PROGRESS','Avance financiero','%','NUMERIC','proyectos_avance',40),
('DESIGN_PROGRESS','Avance diseño','%','NUMERIC','proyectos_avance',50),
('BIM_MODEL_VERSION','Versión BIM',NULL,'TEXT','bim_local_versions',60),
('BIM_SYNC_STATUS','Estado sincronización BIM',NULL,'TEXT','v_bim_models_current',70)
ON CONFLICT(kpi_code) DO NOTHING;
