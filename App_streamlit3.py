import streamlit as st
import pandas as pd
from datetime import datetime
import pytz
import os
import time
import json
import gspread

# Importa la lógica y constantes del módulo vecino (Asegúrate que se llama 'routing_logic.py')
from Routing_logic3 import COORDENADAS_LOTES, solve_route_optimization, VEHICLES, COORDENADAS_ORIGEN

# =============================================================================
# CONFIGURACIÓN INICIAL, ZONA HORARIA Y PERSISTENCIA DE DATOS (GOOGLE SHEETS)
# =============================================================================

st.set_page_config(page_title="Optimizador Bimodal de Rutas", layout="wide")

# --- ZONA HORARIA ARGENTINA (GMT-3) ---
ARG_TZ = pytz.timezone("America/Argentina/Buenos_Aires") # Define la zona horaria de Buenos Aires

# Ocultar menú de Streamlit y footer
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)

# Encabezados en el orden de Google Sheets
# ¡ATENCIÓN! Se agregó "Hora" después de "Fecha"
COLUMNS = ["Fecha", "Hora", "Lotes_ingresados", "Lotes_CamionA", "Lotes_CamionB", "KmRecorridos_CamionA", "KmRecorridos_CamionB"]


# --- Funciones Auxiliares para Navegación (Solo las solicitadas) ---

def generate_gmaps_link(stops_order):
    """
    Genera un enlace de Google Maps para una ruta con múltiples paradas.
    La ruta comienza en el origen (Ingenio y regresa a él.
    """
    if not stops_order:
        return '#'

    # COORDENADAS_ORIGEN es (lon, lat). GMaps requiere lat,lon.
    lon_orig, lat_orig = COORDENADAS_ORIGEN
    
    # 1. Punto de partida (Ingenio)
    # 2. Puntos intermedios (Paradas optimizadas)
    # 3. Punto de destino final (Volver al Ingenio)
    
    route_parts = [f"{lat_orig},{lon_orig}"] # Origen
    
    # Añadir paradas intermedias
    for stop_lote in stops_order:
        if stop_lote in COORDENADAS_LOTES:
            lon, lat = COORDENADAS_LOTES[stop_lote]
            route_parts.append(f"{lat},{lon}") # lat,lon

    # Añadir destino final (regreso al origen)
    route_parts.append(f"{lat_orig},{lon_orig}")

    # Une las partes con '/' para la URL de Google Maps directions (dir/Start/Waypoint1/Waypoint2/End)
    return "https://www.google.com/maps/dir/" + "/".join(route_parts)

def generate_mapycz_link(stops_order):
    """
    Genera un enlace web de Mapy.cz centrado en la última parada.
    Mapy.cz es un servicio excelente para rutas y tracks definidos.
    """
    if not stops_order:
        return '#'
    
    last_stop_lote = stops_order[-1]
    if last_stop_lote in COORDENADAS_LOTES:
        lon, lat = COORDENADAS_LOTES[last_stop_lote]
        # Formato de URL de Mapy.cz para abrir una ubicación
        return f"https://mapy.cz/turisticka?x={lon}&y={lat}&z=10"
    
    return "https://mapy.cz/turisticka"

def generate_waze_link(stops_order):
    """
    Genera un enlace de Waze para una ruta con múltiples paradas. 
    Waze no soporta paradas intermedias en el formato de link simple. 
    Solo usaremos el punto de origen y el último destino.
    """
    if not stops_order:
        return '#'
    
    lon_orig, lat_orig = COORDENADAS_ORIGEN
    last_stop_lote = stops_order[-1]
    
    if last_stop_lote in COORDENADAS_LOTES:
        lon_dest, lat_dest = COORDENADAS_LOTES[last_stop_lote]
        # URL de Waze para iniciar navegación (start_lat, start_lon, end_lat, end_lon)
        return f"https://waze.com/ul?ll={lat_dest},{lon_dest}&navigate=yes&zoom=10"
        
    return "https://waze.com/ul" # Enlace base

def generate_bing_link(stops_order):
    """
    Genera un enlace de Bing Maps para ruteo con múltiples paradas.
    """
    if not stops_order:
        return '#'
    
    lon_orig, lat_orig = COORDENADAS_ORIGEN
    
    # Puntos de partida, intermedios y final (lat,lon)
    waypoints = [f"{lat_orig},{lon_orig}"] 
    
    for stop_lote in stops_order:
        if stop_lote in COORDENADAS_LOTES:
            lon, lat = COORDENADAS_LOTES[stop_lote]
            waypoints.append(f"{lat},{lon}")

    waypoints.append(f"{lat_orig},{lon_orig}")
    
    # Formato Bing: waypoints.0=lat,lon&waypoints.1=lat,lon...
    waypoints_param = "&".join([f"waypoints.{i}={wp}" for i, wp in enumerate(waypoints)])
    
    # URL de Bing Maps para obtener direcciones
    return f"https://www.bing.com/maps/default.aspx?rtp={waypoints_param}"


# --- Funciones de Conexión y Persistencia (Google Sheets) ---

@st.cache_resource(ttl=3600)
def get_gspread_client():
    """Establece la conexión con Google Sheets usando variables de secrets separadas."""
    try:
        # Crea el diccionario de credenciales a partir de los secrets individuales
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

        # Usa service_account_from_dict para autenticar
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
    """Lee el historial de Google Sheets."""
    client = get_gspread_client()
    if not client:
        return pd.DataFrame(columns=COLUMNS)

    try:
        sh = client.open_by_url(st.secrets["GOOGLE_SHEET_URL"])
        worksheet = sh.worksheet(st.secrets["SHEET_WORKSHEET"])

        data = worksheet.get_all_records()
        df = pd.DataFrame(data)

        # Validación: si el DF está vacío o las columnas no coinciden con las 7 esperadas, se usa el DF vacío.
        if df.empty or len(df.columns) < len(COLUMNS):
            return pd.DataFrame(columns=COLUMNS)
        return df

    except Exception as e:
        # Puede fallar si la hoja no está compartida
        st.error(f"❌ Error al cargar datos de Google Sheets. Asegure permisos para {st.secrets['gsheets_client_email']}: {e}")
        return pd.DataFrame(columns=COLUMNS)

def save_new_route_to_sheet(new_route_data):
    """Escribe una nueva ruta a Google Sheets."""
    client = get_gspread_client()
    if not client:
        st.warning("No se pudo guardar la ruta por fallo de conexión a Google Sheets.")
        return

    try:
        sh = client.open_by_url(st.secrets["GOOGLE_SHEET_URL"])
        worksheet = sh.worksheet(st.secrets["SHEET_WORKSHEET"])

        # gspread necesita una lista de valores en el orden de las COLUMNS
        # El orden es crucial: [Fecha, Hora, Lotes_ingresados, ...]
        values_to_save = [new_route_data[col] for col in COLUMNS]

        # Añade la fila al final de la hoja
        worksheet.append_row(values_to_save)

        # Invalida la caché para que la próxima lectura traiga el dato nuevo
        st.cache_data.clear()

    except Exception as e:
        st.error(f"❌ Error al guardar datos en Google Sheets. Verifique que la Fila 1 tenga 7 columnas: {e}")


# -------------------------------------------------------------------------
# INICIALIZACIÓN DE LA SESIÓN
# -------------------------------------------------------------------------

# Inicializar el estado de la sesión para guardar el historial PERMANENTE
if 'historial_cargado' not in st.session_state:
    df_history = get_history_data() # Ahora carga de Google Sheets
    # Convertimos el DataFrame a lista de diccionarios para la sesión
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
        # 👇 Captura la fecha y hora con la zona horaria argentina
        current_time = datetime.now(ARG_TZ) 

        with st.spinner('Realizando cálculo óptimo y agrupando rutas'):
            try:
                results = solve_route_optimization(all_stops_to_visit)

                if "error" in results:
                    st.error(f"❌ Error en la API de Ruteo: {results['error']}")
                else:
                    # ✅ GENERACIÓN DE ENLACES DE NAVEGACIÓN
                    
                    # Generamos los enlaces para todas las 5 opciones: GMaps, Mapy.cz, Waze, Bing
                    if results['ruta_a'].get('orden_optimo'):
                        results['ruta_a']['gmaps_link'] = generate_gmaps_link(results['ruta_a']['orden_optimo'])
                        results['ruta_a']['mapycz_link'] = generate_mapycz_link(results['ruta_a']['orden_optimo']) 
                        results['ruta_a']['waze_link'] = generate_waze_link(results['ruta_a']['orden_optimo']) 
                        results['ruta_a']['bing_link'] = generate_bing_link(results['ruta_a']['orden_optimo']) 
                    else:
                        st.warning("Advertencia: No se pudo optimizar la Ruta A. Puede haber insuficientes lotes válidos o un error en la lógica de ruteo TSP.")
                        results['ruta_a']['gmaps_link'] = '#'
                        results['ruta_a']['mapycz_link'] = '#'
                        results['ruta_a']['waze_link'] = '#'
                        results['ruta_a']['bing_link'] = '#'

                    if results['ruta_b'].get('orden_optimo'):
                        results['ruta_b']['gmaps_link'] = generate_gmaps_link(results['ruta_b']['orden_optimo'])
                        results['ruta_b']['mapycz_link'] = generate_mapycz_link(results['ruta_b']['orden_optimo'])
                        results['ruta_b']['waze_link'] = generate_waze_link(results['ruta_b']['orden_optimo']) 
                        results['ruta_b']['bing_link'] = generate_bing_link(results['ruta_b']['orden_optimo']) 
                    else:
                        st.warning("Advertencia: No se pudo optimizar la Ruta B. Puede haber insuficientes lotes válidos o un error en la lógica de ruteo TSP.")
                        results['ruta_b']['gmaps_link'] = '#'
                        results['ruta_b']['mapycz_link'] = '#'
                        results['ruta_b']['waze_link'] = '#'
                        results['ruta_b']['bing_link'] = '#'
                    
                    # Para GeoJSON, asumimos que 'geojson_link' se establece en solve_route_optimization
                    # Si no está, el botón usará el fallback '#'.

                    # ✅ CREA LA ESTRUCTURA DEL REGISTRO PARA GUARDADO EN SHEETS
                    new_route = {
                        "Fecha": current_time.strftime("%Y-%m-%d"),
                        "Hora": current_time.strftime("%H:%M:%S"), # << Usa la hora ya en la zona horaria correcta
                        "Lotes_ingresados": ", ".join(all_stops_to_visit),
                        "Lotes_CamionA": str(results['ruta_a']['lotes_asignados']), # Guardar como string
                        "Lotes_CamionB": str(results['ruta_b']['lotes_asignados']), # Guardar como string
                        "KmRecorridos_CamionA": results['ruta_a']['distancia_km'],
                        "KmRecorridos_CamionB": results['ruta_b']['distancia_km'],
                    }

                    # 🚀 GUARDA PERMANENTEMENTE EN GOOGLE SHEETS
                    save_new_route_to_sheet(new_route)

                    # ACTUALIZA EL ESTADO DE LA SESIÓN
                    st.session_state.historial_rutas.append(new_route)
                    st.session_state.results = results
                    st.success("✅ Cálculo finalizado y rutas optimizadas. Datos guardados permanentemente en Google Sheets.")

            except Exception as e:
                st.session_state.results = None
                # st.error(f"❌ Ocurrió un error inesperado durante el ruteo: {e}") # Descomentar para debugging
                st.error("❌ Ocurrió un error inesperado durante el ruteo. Verifique la entrada de lotes y el módulo de lógica de ruteo.")


    # -------------------------------------------------------------------------
    # 2. REPORTE DE RESULTADOS UNIFICADO
    # -------------------------------------------------------------------------

    # ESTA CONDICIÓN ES CLAVE: SOLO SE MUESTRA SI HAY RESULTADOS
    if st.session_state.results:
        results = st.session_state.results

        # Definimos res_a y res_b aquí por si la estructura de results es parcial
        res_a = results.get('ruta_a', {})
        res_b = results.get('ruta_b', {})
        
        # GUARDIA ADICIONAL: Solo intentamos renderizar si tenemos rutas completas
        if not (res_a and res_b):
             st.error("Error: La estructura de resultados está incompleta.")
             st.stop() # <-- Uso correcto de st.stop()
        
        st.divider()
        st.header("Análisis de Rutas Generadas")
        st.metric("Distancia Interna de Agrupación (Minimización)", f"{results['agrupacion_distancia_km']} km")
        st.divider()

        col_a, col_b = st.columns(2)

        with col_a:
            st.subheader(f"🚛 Camión 1: {res_a.get('patente', 'N/A')}")
            with st.container(border=True):
                st.markdown(f"**Total Lotes:** {len(res_a.get('lotes_asignados', []))}")
                st.markdown(f"**Distancia Total (TSP):** **{res_a.get('distancia_km', 'N/A')} km**")
                st.markdown(f"**Lotes Asignados:** `{' → '.join(res_a.get('lotes_asignados', []))}`")
                st.info(f"**Orden Óptimo:** Ingenio → {' → '.join(res_a.get('orden_optimo', []))} → Ingenio")
                
            # 👇 ENLACES DE NAVEGACIÓN 
            st.markdown("---")
            
            # Fila para los botones de navegación (5 columnas: 4 navegadores + 1 GeoJSON)
            col_btn_a_1, col_btn_a_2, col_btn_a_3, col_btn_a_4, col_btn_a_5 = st.columns(5)

            with col_btn_a_1:
                st.link_button("🗺️ Google Maps", res_a.get('gmaps_link', '#'), key="gmaps_a")
            
            with col_btn_a_2:
                st.link_button("🌲 Mapy.cz", res_a.get('mapycz_link', '#'), key="mapycz_a") 
            
            with col_btn_a_3:
                st.link_button("🚗 Waze", res_a.get('waze_link', '#'), key="waze_a") # NUEVO
            
            with col_btn_a_4:
                st.link_button("📍 Bing Maps", res_a.get('bing_link', '#'), key="bing_a") # NUEVO
            
            with col_btn_a_5:
                # Asumimos que 'geojson_link' está en los resultados de la lógica de ruteo
                st.link_button("🌐 GeoJSON (Track)", res_a.get('geojson_link', '#'), key="geojson_a")


        with col_b:
            st.subheader(f"🚚 Camión 2: {res_b.get('patente', 'N/A')}")
            with st.container(border=True):
                st.markdown(f"**Total Lotes:** {len(res_b.get('lotes_asignados', []))}")
                st.markdown(f"**Distancia Total (TSP):** **{res_b.get('distancia_km', 'N/A')} km**")
                st.markdown(f"**Lotes Asignados:** `{' → '.join(res_b.get('lotes_asignados', []))}`")
                st.info(f"**Orden Óptimo:** Ingenio → {' → '.join(res_b.get('orden_optimo', []))} → Ingenio")
                
            # 👇 ENLACES DE NAVEGACIÓN 
            st.markdown("---")
            
            # Fila para los botones de navegación (5 columnas: 4 navegadores + 1 GeoJSON)
            col_btn_b_1, col_btn_b_2, col_btn_b_3, col_btn_b_4, col_btn_b_5 = st.columns(5)
            
            with col_btn_b_1:
                st.link_button("🗺️ Google Maps", res_b.get('gmaps_link', '#'), key="gmaps_b")

            with col_btn_b_2:
                st.link_button("🌲 Mapy.cz", res_b.get('mapycz_link', '#'), key="mapycz_b")
            
            with col_btn_b_3:
                st.link_button("🚗 Waze", res_b.get('waze_link', '#'), key="waze_b") # NUEVO
            
            with col_btn_b_4:
                st.link_button("📍 Bing Maps", res_b.get('bing_link', '#'), key="bing_b") # NUEVO
            
            with col_btn_b_5:
                # Asumimos que 'geojson_link' está en los resultados de la lógica de ruteo
                st.link_button("🌐 GeoJSON (Track)", res_b.get('geojson_link', '#'), key="geojson_b")

    else:
        st.info("El reporte aparecerá aquí después de un cálculo exitoso.")


# =============================================================================
# 3. PÁGINA: HISTORIAL
# =============================================================================

elif page == "Historial":
    st.header("📋 Historial de Rutas Calculadas")

    # Se recarga el historial de Google Sheets para garantizar que está actualizado
    df_historial = get_history_data()
    st.session_state.historial_rutas = df_historial.to_dict('records') # Sincroniza la sesión

    if not df_historial.empty:
        st.subheader(f"Total de {len(df_historial)} Rutas Guardadas")

        # Muestra el DF, usando los nombres amigables
        st.dataframe(df_historial,
                     use_container_width=True,
                     column_config={
                         "KmRecorridos_CamionA": st.column_config.NumberColumn("KM Camión A", format="%.2f km"),
                         "KmRecorridos_CamionB": st.column_config.NumberColumn("KM Camión B", format="%.2f km"),
                         "Lotes_CamionA": "Lotes Camión A",
                         "Lotes_CamionB": "Lotes Camión B",
                         "Fecha": "Fecha",
                         "Hora": "Hora de Carga", # Nombre visible en Streamlit
                         "Lotes_ingresados": "Lotes Ingresados"
                      })

    else:
        st.info("No hay rutas guardadas. Realice un cálculo en la página principal.")
