import streamlit as st
import pandas as pd
from datetime import datetime
import pytz
import os
import time
import json
import gspread
import requests # Necesario para las llamadas a la API de ORS
import folium # Necesario para la visualización exacta de la ruta
from streamlit_folium import folium_static # Necesario para renderizar Folium

# Importa la lógica y constantes del módulo vecino
from Routing_logic3 import COORDENADAS_LOTES, solve_route_optimization, VEHICLES, COORDENADAS_ORIGEN

# =============================================================================
# CONFIGURACIÓN INICIAL, ZONA HORARIA Y PERSISTENCIA DE DATOS (GOOGLE SHEETS)
# =============================================================================

st.set_page_config(page_title="Optimizador Bimodal de Rutas", layout="wide")

# --- ZONA HORARIA ARGENTINA (GMT-3) ---
ARG_TZ = pytz.timezone("America/Argentina/Buenos_Aires")

# --- CONFIGURACIÓN OPENROUTESERVICE (ORS) ---
ORS_TOKEN = st.secrets.get("OPENROUTESERVICE_API_KEY", "TU_CLAVE_ORS_AQUI")
ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-car/geojson" # Endpoint de ORS

# Ocultar menú de Streamlit y footer
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)

# Encabezados en el orden de Google Sheets
COLUMNS = ["Fecha", "Hora", "Lotes_ingresados", "Lotes_CamionA", "Lotes_CamionB", "KmRecorridos_CamionA", "KmRecorridos_CamionB"]


# --- Funciones Auxiliares para Navegación ---

def generate_gmaps_link(stops_order):
    """
    Genera un enlace de Google Maps para una ruta con múltiples paradas.
    Se usa como Deep Link de navegación para el móvil.
    """
    if not stops_order:
        return '#'

    # COORDENADAS_ORIGEN es (lon, lat). GMaps requiere lat,lon.
    lon_orig, lat_orig = COORDENADAS_ORIGEN
    route_parts = [f"{lat_orig},{lon_orig}"] # Origen
    
    # Añadir paradas intermedias
    for stop_lote in stops_order:
        if stop_lote in COORDENADAS_LOTES:
            lon, lat = COORDENADAS_LOTES[stop_lote]
            route_parts.append(f"{lat},{lon}") # lat,lon

    # Añadir destino final (regreso al origen)
    route_parts.append(f"{lat_orig},{lon_orig}")

    return "https://www.google.com/maps/dir/" + "/".join(route_parts)


# [FUNCIÓN CLAVE CORREGIDA] - Llamada a la API de OpenRouteService
def get_ors_route_data(stops_order):
    """
    Llama a la API de OpenRouteService para obtener la geometría exacta de la ruta.
    
    Retorna:
        distancia_km (float): Distancia total calculada.
        geojson_coords (list): Lista de coordenadas (lat, lon) de la ruta.
        gmaps_url (str): Enlace Deep Link para el móvil (usamos GMaps como fallback de navegación).
    """
    if not stops_order or not ORS_TOKEN or ORS_TOKEN == "TU_CLAVE_ORS_AQUI":
        return None, None, "#"

    # ORS espera las coordenadas en una lista de listas [[lon1, lat1], [lon2, lat2], ...]
    points = [COORDENADAS_ORIGEN] # Inicio
    for lote in stops_order:
        if lote in COORDENADAS_LOTES:
            points.append(COORDENADAS_LOTES[lote])
    points.append(COORDENADAS_ORIGEN) # Regreso al origen

    # Construir el cuerpo de la solicitud JSON
    # NOTA: En la documentación de ORS, el token va en el encabezado 'Authorization'
    # y el tipo de contenido debe ser JSON.
    headers = {
        'Accept': 'application/json, application/geo+json, application/gpx+xml, application/x-protobuf',
        'Authorization': ORS_TOKEN,
        'Content-Type': 'application/json; charset=utf-8' # Aseguramos el tipo de contenido
    }
    
    body = {
        "coordinates": points,
        "units": "km"
    }

    try:
        # Usamos requests.post ya que estamos enviando un cuerpo JSON
        response = requests.post(ORS_DIRECTIONS_URL, headers=headers, json=body)
        response.raise_for_status() # Lanza un error para códigos 4xx/5xx

        data = response.json()

        if not data.get('routes'):
            st.error("ORS no pudo calcular la ruta con los puntos proporcionados. Revise los lotes.")
            return None, None, "#"

        route = data['routes'][0]
        
        # Extraer la distancia y la geometría
        distancia_km = route['summary']['distance']
        
        # ORS devuelve la geometría en [lon, lat], la convertimos a [lat, lon] para Folium
        geojson_coords = [[lat, lon] for lon, lat in route['geometry']['coordinates']]
        
        # Generar URL de Navegación con Google Maps (usado como Deep Link)
        gmaps_url = generate_gmaps_link(stops_order) 
        
        return distancia_km, geojson_coords, gmaps_url

    except requests.exceptions.HTTPError as e:
        # Se detalla mejor el error para el usuario
        error_message = f"Error de API de OpenRouteService: {e.response.status_code}. Mensaje del servidor: {e.response.reason}"
        if e.response.status_code == 406:
            st.error(f"❌ Error 406: La solicitud no es aceptable. Verifique que el token sea correcto y que los headers son válidos.")
        elif e.response.status_code == 403:
             st.error(f"❌ Error 403: Token inválido o sin privilegios.")
        elif e.response.status_code == 404:
            st.error(f"❌ Error 404: Ruta no encontrada. Verifique las coordenadas.")
        else:
            st.error(f"❌ Error HTTP {e.response.status_code}: {e.response.reason}")
        
        return None, None, "#"
    except Exception as e:
        st.error(f"❌ Error general al conectar con ORS: {e}")
        return None, None, "#"
        route = data['routes'][0]
        
        # Extraer la distancia y la geometría
        distancia_km = route['summary']['distance']
        
        # ORS devuelve la geometría en [lon, lat], la convertimos a [lat, lon] para Folium
        geojson_coords = [[lat, lon] for lon, lat in route['geometry']['coordinates']]
        
        # Generar URL de Navegación con Google Maps (usado como Deep Link)
        gmaps_url = generate_gmaps_link(stops_order) 
        
        return distancia_km, geojson_coords, gmaps_url

    except requests.exceptions.HTTPError as e:
        st.error(f"Error de API de OpenRouteService: {e.response.status_code}. Verifique su clave o límite de uso. {e}")
        return None, None, "#"
    except Exception as e:
        st.error(f"Error al conectar con ORS: {e}")
        return None, None, "#"


# --- Funciones de Conexión y Persistencia (Google Sheets) ---
# Se mantiene el código de las funciones de Google Sheets sin cambios.

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
                    # 2. Obtener la geometría y distancia exacta de ORS
                    
                    # --- CAMIÓN A ---
                    orden_a = results['ruta_a']['orden_optimo']
                    if is_ors_configured:
                        km_a, geojson_a, nav_link_a = get_ors_route_data(orden_a)
                        results['ruta_a']['geojson'] = geojson_a 
                        results['ruta_a']['distancia_km'] = km_a if km_a else results['ruta_a']['distancia_km']
                        results['ruta_a']['nav_link'] = nav_link_a
                    else:
                        results['ruta_a']['nav_link'] = generate_gmaps_link(orden_a)

                    # --- CAMIÓN B ---
                    orden_b = results['ruta_b']['orden_optimo']
                    if is_ors_configured:
                        km_b, geojson_b, nav_link_b = get_ors_route_data(orden_b)
                        results['ruta_b']['geojson'] = geojson_b 
                        results['ruta_b']['distancia_km'] = km_b if km_b else results['ruta_b']['distancia_km'] 
                        results['ruta_b']['nav_link'] = nav_link_b
                    else:
                        results['ruta_b']['nav_link'] = generate_gmaps_link(orden_b)

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

        st.divider()
        st.header("Análisis de Rutas Generadas")
        st.metric("Distancia Interna de Agrupación (Minimización)", f"{results['agrupacion_distancia_km']} km")
        st.divider()

        res_a = results.get('ruta_a', {})
        res_b = results.get('ruta_b', {})
        
        # [NUEVO] - Mapa de Visualización de las Rutas con Folium
        col_mapa_viz, col_vacio = st.columns([1,1])
        with col_mapa_viz:
            st.subheader("Mapa Interactivo de Rutas Calculadas (Folium)")
            if not ORS_TOKEN or ORS_TOKEN == "TU_CLAVE_ORS_AQUI":
                st.info("Debe configurar la clave ORS API para visualizar la geometría exacta de la ruta aquí. Actualmente se usa el mapa básico de puntos.")
            elif res_a.get('geojson') and res_b.get('geojson'):
                
                lon_center, lat_center = COORDENADAS_ORIGEN
                
                m = folium.Map(
                    location=[lat_center, lon_center], 
                    zoom_start=11, 
                    tiles="CartoDB positron"
                )
                
                # Marcar Origen
                folium.Marker([lat_center, lon_center], tooltip="Ingenio (Origen)", icon=folium.Icon(color='green', icon='home')).add_to(m)

                # Dibuja Ruta A
                folium.PolyLine(res_a['geojson'], color="blue", weight=5, opacity=0.8, tooltip="Camión A").add_to(m)
                
                # Dibuja Ruta B
                folium.PolyLine(res_b['geojson'], color="red", weight=5, opacity=0.8, tooltip="Camión B").add_to(m)

                # Añadir marcadores de paradas
                all_stops = res_a.get('orden_optimo', []) + res_b.get('orden_optimo', [])
                for i, lote in enumerate(all_stops):
                    if lote in COORDENADAS_LOTES:
                        lon, lat = COORDENADAS_LOTES[lote]
                        color = 'blue' if lote in res_a.get('orden_optimo', []) else 'red'
                        folium.Marker([lat, lon], tooltip=f"{lote} ({i+1})", icon=folium.Icon(color=color, icon='truck')).add_to(m)
                
                folium_static(m, width=700, height=500)
            else:
                 st.info("No hay datos de geometría GeoJSON para mostrar (verifique la conexión con OpenRouteService).")

        st.divider()
        col_a, col_b = st.columns(2)

        with col_a:
            st.subheader(f"🚛 Camión 1: {res_a.get('patente', 'N/A')}")
            with st.container(border=True):
                st.markdown(f"**Total Lotes:** {len(res_a.get('lotes_asignados', []))}")
                st.markdown(f"**Distancia Total (TSP):** **{res_a.get('distancia_km', 'N/A')} km**")
                st.markdown(f"**Lotes Asignados:** `{' → '.join(res_a.get('lotes_asignados', []))}`")
                st.info(f"**Orden Óptimo:** Ingenio → {' → '.join(res_a.get('orden_optimo', []))} → Ingenio")
                
                # Botón principal con Deep Link a Google Maps
                st.link_button(
                    "🚀 INICIAR RECORRIDO A (Navegación)", 
                    res_a.get('nav_link', '#'),
                    type="primary", 
                    use_container_width=True
                )
                st.markdown("---")
                st.markdown(f"**Fuente de Ruta:** {'OpenRouteService' if ORS_TOKEN != 'TU_CLAVE_ORS_AQUI' else 'Google Maps (Fallback)'}")
                st.link_button("🗺️ Ver en Google Maps (Alternativa)", generate_gmaps_link(res_a.get('orden_optimo', [])))


        with col_b:
            st.subheader(f"🚚 Camión 2: {res_b.get('patente', 'N/A')}")
            with st.container(border=True):
                st.markdown(f"**Total Lotes:** {len(res_b.get('lotes_asignados', []))}")
                st.markdown(f"**Distancia Total (TSP):** **{res_b.get('distancia_km', 'N/A')} km**")
                st.markdown(f"**Lotes Asignados:** `{' → '.join(res_b.get('lotes_asignados', []))}`")
                st.info(f"**Orden Óptimo:** Ingenio → {' → '.join(res_b.get('orden_optimo', []))} → Ingenio")
                
                # Botón principal con Deep Link a Google Maps
                st.link_button(
                    "🚀 INICIAR RECORRIDO B (Navegación)", 
                    res_b.get('nav_link', '#'), 
                    type="primary", 
                    use_container_width=True
                )
                st.markdown("---")
                st.markdown(f"**Fuente de Ruta:** {'OpenRouteService' if ORS_TOKEN != 'TU_CLAVE_ORS_AQUI' else 'Google Maps (Fallback)'}")
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

