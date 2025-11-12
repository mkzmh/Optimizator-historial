import streamlit as st
import pandas as pd
from datetime import datetime
import pytz
import os
import time
import json
import gspread
import requests
import folium 
from streamlit_folium import folium_static 

# Importa la lógica y constantes del módulo vecino
from Routing_logic3 import COORDENADAS_LOTES, solve_route_optimization, VEHICLES, COORDENADAS_ORIGEN

# =============================================================================
# CONFIGURACIÓN INICIAL, ZONA HORARIA Y PERSISTENCIA DE DATOS (API KEYS)
# =============================================================================

st.set_page_config(page_title="Optimizador Bimodal de Rutas", layout="wide")

# --- ZONA HORARIA ARGENTINA (GMT-3) ---
ARG_TZ = pytz.timezone("America/Argentina/Buenos_Aires")

# --- CONFIGURACIÓN ORS ---
ORS_TOKEN = st.secrets.get("OPENROUTESERVICE_API_KEY", "TU_CLAVE_ORS_AQUI")
ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson" 

# Ocultar menú de Streamlit y footer
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)

COLUMNS = ["Fecha", "Hora", "Lotes_ingresados", "Lotes_CamionA", "Lotes_CamionB", "KmRecorridos_CamionA", "KmRecorridos_CamionB"]


# --- Funciones Auxiliares ---

def generate_gmaps_link(stops_order, include_return=True):
    """
    Genera un enlace de Google Maps con múltiples paradas.
    Se usa como Deep Link de navegación para el móvil.
    """
    if not stops_order:
        return '#'

    # COORDENADAS_ORIGEN es (lon, lat). GMaps requiere lat,lon.
    lon_orig, lat_orig = COORDENADAS_ORIGEN
    route_parts = [f"{lat_orig},{lon_orig}"]
    
    for stop_lote in stops_order:
        if stop_lote in COORDENADAS_LOTES:
            lon, lat = COORDENADAS_LOTES[stop_lote]
            route_parts.append(f"{lat},{lon}")

    if include_return:
        route_parts.append(f"{lat_orig},{lon_orig}")

    return f"https://www.google.com/maps/dir/{'/'.join(route_parts)}"

def get_points_list(stops_order, include_return=False):
    """Prepara la lista de puntos [[lon, lat], ...] para la API."""
    points = [COORDENADAS_ORIGEN]
    for lote in stops_order:
        if lote in COORDENADAS_LOTES:
            points.append(COORDENADAS_LOTES[lote])
    if include_return:
        points.append(COORDENADAS_ORIGEN)
    return points

# --------------------------------------------------------------------------
# Motores de Ruteo
# --------------------------------------------------------------------------

def get_ors_route_data(stops_order):
    """
    Llama a OpenRouteService usando el token como parámetro de URL (solución robusta).
    """
    if not ORS_TOKEN or ORS_TOKEN == "TU_CLAVE_ORS_AQUI":
        return {"error": "ORS: Clave API no configurada."}
    
    if not stops_order: # Manejar el caso de una ruta vacía
         return {"error": "ORS: La lista de paradas está vacía."}

    # 1. Definir los puntos (NO incluimos el retorno al origen para evitar fallos de ruta cerrada)
    points = get_points_list(stops_order, include_return=False) 
    
    # --- LÍNEA DE DEPURACIÓN TEMPORAL (Muestra las coordenadas enviadas) ---
    st.info(f"Coordenadas enviadas a ORS (Lon, Lat): {points}") 
    # -----------------------------------------------------------------------
    
    # 2. Definir los encabezados y el cuerpo de la solicitud JSON
    headers = {
        'Accept': 'application/json',
        'Content-Type': 'application/json; charset=utf-8'
    }
    
    body = {
        "coordinates": points,
        "units": "km"
    }
    
    # 3. Construir la URL con el token como parámetro de consulta (SOLUCIÓN CLAVE)
    url_with_key = f"{ORS_DIRECTIONS_URL}?api_key={ORS_TOKEN}"

    try:
        # Añadir el timeout de 30 segundos
        response = requests.post(url_with_key, headers=headers, json=body, timeout=30) 
        response.raise_for_status()

        data = response.json()

        if not data.get('routes'):
            st.error("❌ ORS: No se encontró una ruta viable. Esto puede deberse a caminos inaccesibles o a restricciones de la red.")
            return {"error": "ORS: Ruta no encontrada/inaccesible"}

        route = data['routes'][0]
        distancia_km = route['summary']['distance']
        
        # Las coordenadas de la ruta de ORS (GeoJSON)
        # Se necesita añadir el retorno al origen manualmente para Folium
        geojson_coords = [[lat, lon] for lon, lat in route['geometry']['coordinates']]
        geojson_coords.append([COORDENADAS_ORIGEN[1], COORDENADAS_ORIGEN[0]]) # Agregar retorno para visualización
        
        return {"distance": distancia_km, "geojson": geojson_coords}

    except requests.exceptions.Timeout:
         # Capturar el error de timeout específicamente
         st.error("❌ Fallo ORS: Timeout. La ruta tardó demasiado en calcularse. Intente menos lotes o caminos menos complejos.")
         return {"error": "ORS: Timeout"}
    except requests.exceptions.HTTPError as e:
        st.error(f"❌ Fallo ORS: HTTP Error {e.response.status_code}: {e.response.reason}. Verifique su clave o límites de uso.")
        return {"error": f"ORS HTTP Error {e.response.status_code}"}
    except Exception as e:
        st.error(f"❌ Fallo ORS: Error general de conexión: {e}")
        return {"error": f"ORS General Error: {e}"}


def calculate_route_geometry(stops_order):
    """Intenta calcular la geometría solo con ORS y gestiona el fallback."""
    result = {"distance": None, "geojson": None, "source": "GeoJSON de Emergencia"}
    
    # 1. Intentar con OpenRouteService (ORS)
    ors_res = get_ors_route_data(stops_order)
    if "error" not in ors_res:
        st.success("✅ Ruta calculada con OpenRouteService.")
        result["distance"] = ors_res["distance"]
        result["geojson"] = ors_res["geojson"]
        result["source"] = "OpenRouteService"
        return result
    else:
        # Si ORS falla, el mensaje de error ya se mostró arriba.
        pass
    
    # 2. Fallback a GeoJSON de Emergencia (Línea Recta simple)
    st.error("🚨 El ruteo avanzado falló. Usando GeoJSON de Emergencia (líneas rectas).")
    
    # Generar la GeoJSON de emergencia (líneas rectas entre puntos)
    points = get_points_list(stops_order, include_return=True) 
    result["geojson"] = [[lat, lon] for lon, lat in points] # [lat, lon] para Folium
    result["distance"] = 0 
    
    return result


# --- Funciones de Conexión y Persistencia (Google Sheets) ---

@st.cache_resource(ttl=3600)
def get_gspread_client():
    try:
        credentials_dict = {
            "type": "service_account",
            "project_id": st.secrets["gsheets_project_id"],
            "private_key_id": st.secrets["gsheets_private_key_id"],
            "private_key": st.secrets["gsheets_private_key"],
            "client_email": st.secrets["gsheets_client_email"],
            "client_id": st.secrets["gsheets_client_id"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": f"https://www.googleapis.com/robot/v1/metadata/x509/{st.secrets['gsheets_client_email']}",
            "universe_domain": "googleapis.com"
        }
        gc = gspread.service_account_from_dict(credentials_dict)
        return gc
    except KeyError as e:
        st.error(f"⚠️ Error de Credenciales: Falta la clave '{e}' en Streamlit Secrets. El historial está desactivado.")
        return None
    except Exception as e:
        st.error(f"❌ Error fatal al inicializar la conexión con GSheets: {e}")
        return None

@st.cache_data(ttl=3600)
def get_history_data():
    client = get_gspread_client()
    if not client:
        return pd.DataFrame(columns=COLUMNS)
    try:
        sh = client.open_by_url(st.secrets["GOOGLE_SHEET_URL"])
        worksheet = sh.worksheet(st.secrets["SHEET_WORKSHEET"])
        data = worksheet.get_all_records()
        df = pd.DataFrame(data)
        if df.empty or len(df.columns) < len(COLUMNS):
            return pd.DataFrame(columns=COLUMNS)
        return df
    except Exception as e:
        st.error(f"❌ Error al cargar datos de Google Sheets. Asegure permisos para {st.secrets['gsheets_client_email']}: {e}")
        return pd.DataFrame(columns=COLUMNS)

def save_new_route_to_sheet(new_route_data):
    client = get_gspread_client()
    if not client:
        st.warning("No se pudo guardar la ruta por fallo de conexión a Google Sheets.")
        return
    try:
        sh = client.open_by_url(st.secrets["GOOGLE_SHEET_URL"])
        worksheet = sh.worksheet(st.secrets["SHEET_WORKSHEET"])
        values_to_save = [new_route_data[col] for col in COLUMNS]
        worksheet.append_row(values_to_save)
        st.cache_data.clear()
    except Exception as e:
        st.error(f"❌ Error al guardar datos en Google Sheets. Verifique que la Fila 1 tenga 7 columnas: {e}")


# -------------------------------------------------------------------------
# INICIALIZACIÓN DE LA SESIÓN
# -------------------------------------------------------------------------

if 'historial_cargado' not in st.session_state:
    df_history = get_history_data() 
    st.session_state.historial_rutas = df_history.to_dict('records')
    st.session_state.historial_cargado = True

if 'results' not in st.session_state:
    st.session_state.results = None

# Inicializar estados de GeoJSON Override
if 'geojson_override_a' not in st.session_state:
    st.session_state.geojson_override_a = ''
if 'geojson_override_b' not in st.session_state:
    st.session_state.geojson_override_b = ''
# Bandera para saber si el cálculo de la sesión falló
if 'ors_failed' not in st.session_state:
    st.session_state.ors_failed = False


# =============================================================================
# ESTRUCTURA DEL MENÚ LATERAL Y NAVEGACIÓN
# =============================================================================

st.sidebar.title("Menú Principal")
page = st.sidebar.radio(
    "Seleccione una opción:",
    ["Calcular Nueva Ruta", "Historial"]
)
st.sidebar.divider()
st.sidebar.info(f"Rutas Guardadas: {len(st.session_state.historial_rutas)}")

# =============================================================================
# 1. PÁGINA: CALCULAR NUEVA RUTA (PÁGINA PRINCIPAL)
# =============================================================================

if page == "Calcular Nueva Ruta":
    st.title("🚚 Optimizator📍")
    st.caption("Planificación y división óptima de lotes para vehículos de entrega.")

    st.header("Selección de Destinos")

    lotes_input = st.text_input(
        "Ingrese los lotes a visitar (separados por coma, ej: A05, B10, C95):",
        placeholder="A05, A10, B05, B10, C95, D01, K01"
    )

    col_map, col_details = st.columns([2, 1])

    all_stops_to_visit = [l.strip().upper() for l in lotes_input.split(',') if l.strip()]
    num_lotes = len(all_stops_to_visit)

    # Lógica de pre-visualización y mapa...
    map_data_list = []
    map_data_list.append({'name': 'INGENIO (Origen)', 'lat': COORDENADAS_ORIGEN[1], 'lon': COORDENADAS_ORIGEN[0]})

    valid_stops_count = 0
    invalid_stops = [l for l in all_stops_to_visit if l not in COORDENADAS_LOTES]

    for lote in all_stops_to_visit:
        if lote in COORDENADAS_LOTES:
            lon, lat = COORDENADAS_LOTES[lote]
            map_data_list.append({'name': lote, 'lat': lat, 'lon': lon})
            valid_stops_count += 1

    map_data = pd.DataFrame(map_data_list)

    with col_map:
        if valid_stops_count > 0:
            st.subheader(f"Mapa de {valid_stops_count} Destinos")
            st.map(map_data, latitude='lat', longitude='lon', color='#0044FF', size=10, zoom=10)
        else:
            st.info("Ingrese lotes válidos para ver la previsualización del mapa.")

    with col_details:
        st.subheader("Estado de la Selección")
        st.metric("Total Lotes Ingresados", num_lotes)

        if invalid_stops:
            st.error(f"❌ {len(invalid_stops)} Lotes Inválidos: {', '.join(invalid_stops)}.")

        MIN_LOTES = 3
        MAX_LOTES = 7

        if valid_stops_count < MIN_LOTES or valid_stops_count > MAX_LOTES:
            st.warning(f"⚠️ Debe ingresar entre {MIN_LOTES} y {MAX_LOTES} lotes válidos. Ingresó {valid_stops_count}.")
            calculate_disabled = True
        elif valid_stops_count > 0:
            calculate_disabled = False
        else:
            calculate_disabled = True

    # -------------------------------------------------------------------------
    # 🛑 BOTÓN DE CÁLCULO Y LÓGICA
    # -------------------------------------------------------------------------
    st.divider()

    if st.button("🚀 Calcular Rutas Óptimas", key="calc_btn_main", type="primary", disabled=calculate_disabled):

        st.session_state.results = None
        st.session_state.ors_failed = False # Resetear bandera de fallo
        current_time = datetime.now(ARG_TZ) 

        # Verificar si la clave de ORS está configurada
        is_ors_configured = ORS_TOKEN != "TU_CLAVE_ORS_AQUI"

        if not is_ors_configured:
             st.warning("⚠️ ¡Atención! OpenRouteService no está configurado. La distancia en el reporte es solo una estimación. Usando Google Maps para los enlaces de navegación.")

        with st.spinner('Realizando cálculo óptimo y agrupando rutas'):
            try:
                # 1. Resolver el problema TSP
                results = solve_route_optimization(all_stops_to_visit)

                if "error" in results:
                    st.error(f"❌ Error en la API de Ruteo: {results['error']}")
                else:
                    # 2. Obtener la geometría y distancia exacta con ORS/Fallback
                    
                    # --- CAMIÓN A ---
                    orden_a = results['ruta_a']['orden_optimo']
                    geo_a_res = calculate_route_geometry(orden_a)
                    
                    results['ruta_a']['geojson'] = geo_a_res["geojson"]
                    if geo_a_res["distance"]:
                         results['ruta_a']['distancia_km'] = geo_a_res["distance"] 
                    results['ruta_a']['nav_link'] = generate_gmaps_link(orden_a, include_return=True)
                    results['ruta_a']['source'] = geo_a_res["source"]

                    if geo_a_res["source"] != "OpenRouteService":
                        st.session_state.ors_failed = True
                    
                    # --- CAMIÓN B ---
                    orden_b = results['ruta_b']['orden_optimo']
                    geo_b_res = calculate_route_geometry(orden_b)
                    
                    results['ruta_b']['geojson'] = geo_b_res["geojson"] 
                    if geo_b_res["distance"]:
                         results['ruta_b']['distancia_km'] = geo_b_res["distance"] 
                    results['ruta_b']['nav_link'] = generate_gmaps_link(orden_b, include_return=True)
                    results['ruta_b']['source'] = geo_b_res["source"]

                    if geo_b_res["source"] != "OpenRouteService":
                        st.session_state.ors_failed = True
                    
                    # 3. Guardar en Sheets
                    new_route = {
                        "Fecha": current_time.strftime("%Y-%m-%d"),
                        "Hora": current_time.strftime("%H:%M:%S"),
                        "Lotes_ingresados": ", ".join(all_stops_to_visit),
                        "Lotes_CamionA": str(results['ruta_a']['lotes_asignados']),
                        "Lotes_CamionB": str(results['ruta_b']['lotes_asignados']),
                        "KmRecorridos_CamionA": results['ruta_a']['distancia_km'],
                        "KmRecorridos_CamionB": results['ruta_b']['distancia_km'],
                    }

                    save_new_route_to_sheet(new_route)

                    # 4. Actualizar Estado de la Sesión
                    st.session_state.historial_rutas.append(new_route)
                    st.session_state.results = results
                    st.success("✅ Cálculo finalizado y rutas optimizadas. Datos guardados permanentemente en Google Sheets.")

            except Exception as e:
                st.session_state.results = None
                st.error(f"❌ Ocurrió un error inesperado durante el ruteo: {e}")

    # -------------------------------------------------------------------------
    # 2. REPORTE DE RESULTADOS UNIFICADO
    # -------------------------------------------------------------------------

    if st.session_state.results:
        results = st.session_state.results

        # Si ORS falló, intentar aplicar GeoJSON de override
        if st.session_state.ors_failed and (st.session_state.geojson_override_a or st.session_state.geojson_override_b):
            st.warning("🔄 Aplicando GeoJSON de anulación. Recalculando visualización...")
            
            # Intento de cargar GeoJSON A
            if st.session_state.geojson_override_a:
                try:
                    # El usuario pega la geometría, necesitamos solo la lista de coordenadas [[lat, lon], ...]
                    geojson_data = json.loads(st.session_state.geojson_override_a)
                    if 'routes' in geojson_data and geojson_data['routes']:
                        coords = geojson_data['routes'][0]['geometry']['coordinates']
                        # Convertir [lon, lat] a [lat, lon] y añadir retorno
                        results['ruta_a']['geojson'] = [[lat, lon] for lon, lat in coords]
                        results['ruta_a']['geojson'].append([COORDENADAS_ORIGEN[1], COORDENADAS_ORIGEN[0]])
                        results['ruta_a']['source'] = "Manual Override A"
                    elif isinstance(geojson_data, list): # Si pegan solo la lista de coordenadas
                        results['ruta_a']['geojson'] = geojson_data
                        results['ruta_a']['source'] = "Manual Override A (Lista)"
                    else:
                        st.error("Error al parsear GeoJSON A. Asegúrese de que es GeoJSON de ruta o una lista de coordenadas.")
                        
                except json.JSONDecodeError:
                    st.error("Error al leer el JSON de anulación del Camión A. Verifique el formato.")
                except Exception as e:
                    st.error(f"Error al procesar el GeoJSON A: {e}")

            # Intento de cargar GeoJSON B
            if st.session_state.geojson_override_b:
                try:
                    geojson_data = json.loads(st.session_state.geojson_override_b)
                    if 'routes' in geojson_data and geojson_data['routes']:
                        coords = geojson_data['routes'][0]['geometry']['coordinates']
                        results['ruta_b']['geojson'] = [[lat, lon] for lon, lat in coords]
                        results['ruta_b']['geojson'].append([COORDENADAS_ORIGEN[1], COORDENADAS_ORIGEN[0]])
                        results['ruta_b']['source'] = "Manual Override B"
                    elif isinstance(geojson_data, list):
                        results['ruta_b']['geojson'] = geojson_data
                        results['ruta_b']['source'] = "Manual Override B (Lista)"
                    else:
                        st.error("Error al parsear GeoJSON B. Asegúrese de que es GeoJSON de ruta o una lista de coordenadas.")

                except json.JSONDecodeError:
                    st.error("Error al leer el JSON de anulación del Camión B. Verifique el formato.")
                except Exception as e:
                    st.error(f"Error al procesar el GeoJSON B: {e}")
            
            # La visualización se actualizará automáticamente en el siguiente bloque

        st.divider()
        st.header("Análisis de Rutas Generadas")
        st.metric("Distancia Interna de Agrupación (Minimización)", f"{results['agrupacion_distancia_km']} km")
        st.divider()

        res_a = results.get('ruta_a', {})
        res_b = results.get('ruta_b', {})
        
        # Mapa de Visualización de las Rutas con Folium
        col_mapa_viz, col_vacio = st.columns([1,1])
        with col_mapa_viz:
            st.subheader("Mapa Interactivo de Rutas Calculadas (Folium)")
            if not res_a.get('geojson') or not res_b.get('geojson') or res_a.get('source') == 'GeoJSON de Emergencia':
                # Mensaje de advertencia si falló la carga 
                st.warning("No hay datos de geometría de ruta para mostrar. **Verifique sus credenciales y la accesibilidad de los caminos en OpenStreetMap.**")

                # --- MÓDULO DE OVERRIDE (SOLO VISIBLE SI EL CÁLCULO FALLÓ) ---
                if st.session_state.ors_failed:
                    with st.expander("🛠️ PEGAR GEOJSON DE ANULACIÓN (Override)", expanded=True):
                        st.caption("Si la ruta funciona en la web de ORS, péguela aquí para visualizarla.")
                        
                        col_o1, col_o2 = st.columns(2)
                        with col_o1:
                            st.session_state.geojson_override_a = st.text_area(
                                "GeoJSON Camión A (Pegue el JSON o lista de coordenadas)",
                                value=st.session_state.geojson_override_a,
                                height=150,
                                key="override_a"
                            )
                        with col_o2:
                            st.session_state.geojson_override_b = st.text_area(
                                "GeoJSON Camión B (Pegue el JSON o lista de coordenadas)",
                                value=st.session_state.geojson_override_b,
                                height=150,
                                key="override_b"
                            )
                        st.button("Aplicar GeoJSON Manual", key="apply_override")
                # --- FIN MÓDULO DE OVERRIDE ---
            else:
                # Si la geometría existe, se dibuja el mapa
                lon_center, lat_center = COORDENADAS_ORIGEN
                
                m = folium.Map(
                    location=[lat_center, lon_center], 
                    zoom_start=11, 
                    tiles="CartoDB positron"
                )
                
                # Marcar Origen
                folium.Marker([lat_center, lon_center], tooltip="Ingenio (Origen)", icon=folium.Icon(color='green', icon='home')).add_to(m)

                # Dibuja Ruta A
                folium.PolyLine(res_a['geojson'], color="blue", weight=5, opacity=0.8, tooltip=f"Camión A ({res_a['source']})").add_to(m)
                
                # Dibuja Ruta B
                folium.PolyLine(res_b['geojson'], color="red", weight=5, opacity=0.8, tooltip=f"Camión B ({res_b['source']})").add_to(m)

                # Añadir marcadores de paradas
                all_stops = res_a.get('orden_optimo', []) + res_b.get('orden_optimo', [])
                for i, lote in enumerate(all_stops):
                    if lote in COORDENADAS_LOTES:
                        lon, lat = COORDENADAS_LOTES[lote]
                        color = 'blue' if lote in res_a.get('orden_optimo', []) else 'red'
                        folium.Marker([lat, lon], tooltip=f"{lote} ({i+1})", icon=folium.Icon(color=color, icon='truck')).add_to(m)
                
                folium_static(m, width=700, height=500)

        st.divider()
        col_a, col_b = st.columns(2)

        with col_a:
            st.subheader(f"🚛 Camión 1: {res_a.get('patente', 'N/A')}")
            with st.container(border=True):
                st.markdown(f"**Total Lotes:** {len(res_a.get('lotes_asignados', []))}")
                st.markdown(f"**Distancia Total (Vial):** **{res_a.get('distancia_km', 'N/A')} km**")
                st.markdown(f"**Lotes Asignados:** `{' → '.join(res_a.get('lotes_asignados', []))}`")
                st.info(f"**Orden Óptimo:** Ingenio → {' → '.join(res_a.get('orden_optimo', []))} → Ingenio")
                
                st.link_button(
                    "🚀 INICIAR RECORRIDO A (Navegación)", 
                    res_a.get('nav_link', '#'),
                    type="primary", 
                    use_container_width=True
                )
                st.markdown("---")
                st.markdown(f"**Fuente de Ruta (Visualización):** **{res_a.get('source', 'N/A')}**")
                st.link_button("🗺️ Ver en Google Maps (Alternativa)", generate_gmaps_link(res_a.get('orden_optimo', [])))


        with col_b:
            st.subheader(f"🚚 Camión 2: {res_b.get('patente', 'N/A')}")
            with st.container(border=True):
                st.markdown(f"**Total Lotes:** {len(res_b.get('lotes_asignados', []))}")
                st.markdown(f"**Distancia Total (Vial):** **{res_b.get('distancia_km', 'N/A')} km**")
                st.markdown(f"**Lotes Asignados:** `{' → '.join(res_b.get('lotes_asignados', []))}`")
                st.info(f"**Orden Óptimo:** Ingenio → {' → '.join(res_b.get('orden_optimo', []))} → Ingenio")
                
                st.link_button(
                    "🚀 INICIAR RECORRIDO B (Navegación)", 
                    res_b.get('nav_link', '#'), 
                    type="primary", 
                    use_container_width=True
                )
                st.markdown("---")
                st.markdown(f"**Fuente de Ruta (Visualización):** **{res_b.get('source', 'N/A')}**")
                st.link_button("🗺️ Ver en Google Maps (Alternativa)", generate_gmaps_link(res_b.get('orden_optimo', [])))

    else:
        st.info("El reporte aparecerá aquí después de un cálculo exitoso.")


# =============================================================================
# 3. PÁGINA: HISTORIAL
# =============================================================================

elif page == "Historial":
    st.header("📋 Historial de Rutas Calculadas")

    df_historial = get_history_data()
    st.session_state.historial_rutas = df_historial.to_dict('records')

    if not df_historial.empty:
        st.subheader(f"Total de {len(df_historial)} Rutas Guardadas")

        st.dataframe(df_historial,
                      use_container_width=True,
                      column_config={
                          "KmRecorridos_CamionA": st.column_config.NumberColumn("KM Camión A", format="%.2f km"),
                          "KmRecorridos_CamionB": st.column_config.NumberColumn("KM Camión B", format="%.2f km"),
                          "Lotes_CamionA": "Lotes Camión A",
                          "Lotes_CamionB": "Lotes Camión B",
                          "Fecha": "Fecha",
                          "Hora": "Hora de Carga",
                          "Lotes_ingresados": "Lotes Ingresados"
                      })

    else:
        st.info("No hay rutas guardadas. Realice un cálculo en la página principal.")
