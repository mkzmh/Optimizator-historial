import streamlit as st
import pandas as pd
from datetime import datetime # Importación actualizada para usar la hora
import pytz # ¡NUEVO! Importamos pytz para manejo de zonas horarias
import os
import time
import json
import gspread # Necesario para la conexión a Google Sheets
import requests # ¡NUEVO! Para la conexión con API de rastreo (Praxys)
import folium # ¡NUEVO! Para generar mapas interactivos
from streamlit_folium import folium_static # ¡NUEVO! Para mostrar mapas de Folium

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


# --- Funciones Auxiliares para Navegación ---

def generate_gmaps_link(stops_order):
    """
    Genera un enlace de Google Maps para una ruta con múltiples paradas.
    La ruta comienza en el origen (Ingenio) y regresa a él.
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
# FUNCIONES ESPECÍFICAS DE RASTREO Y MAPAS (Folium)
# -------------------------------------------------------------------------

@st.cache_data(ttl=3) # Cache por 3 segundos para simular tiempo de API
def fetch_praxys_location(camion_id, use_simulation=True):
    """
    [PUNTO CLAVE DE MODIFICACIÓN]
    Obtiene la ubicación GPS (latitud, longitud) del vehículo real o simulado.
    El usuario debe reemplazar la SIMULACIÓN (sección B) con la conexión a la API REAL (sección A).
    """
    
    # ---------------------------------------------------------------------
    # A. LÓGICA DE CONEXIÓN REAL A PRAXYS (Requiere edición del usuario)
    # ---------------------------------------------------------------------
    if not use_simulation:
        try:
            # 1. Obtener Token y configurar IDs
            api_token = st.secrets.get("PRAXYS_API_TOKEN", "TOKEN_NO_CONFIGURADO")
            if api_token == "TOKEN_NO_CONFIGURADO" or api_token == "PEGA_AQUÍ_TU_TOKEN_REAL_DE_PRAXYS":
                # Retorna el origen como ubicación estática si la API no está configurada
                return COORDENADAS_ORIGEN[1], COORDENADAS_ORIGEN[0] 
            
            # 2. Configurar la URL con el ID del vehículo real
            # --- REEMPLAZAR ID_REAL_CAMION_X con el ID de rastreo que Praxys usa ---
            VEHICLE_TRACKING_ID = "ID_REAL_CAMION_A" if camion_id == 'A' else "ID_REAL_CAMION_B" 
            API_URL = f"https://api.praxys.com/v1/vehicles/{VEHICLE_TRACKING_ID}/lastlocation" # <<< REEMPLAZAR CON LA URL DE TU SERVICIO
            
            HEADERS = {
                "Authorization": f"Bearer {api_token}",
                "Content-Type": "application/json"
            }
            
            # 3. Llamada a la API
            response = requests.get(API_URL, headers=HEADERS, timeout=5)
            response.raise_for_status() 
            data = response.json()
            
            # 4. PARSEAR LA RESPUESTA REAL DE PRAXYS
            # --- ATENCIÓN: AJUSTAR ESTAS CLAVES CON LA RESPUESTA JSON REAL DE PRAXYS ---
            # Ejemplo: Si Praxys devuelve {"latitude": -24.8, "longitude": -65.4}
            lat = data.get('lat') # Cambiar 'lat' a la clave real de latitud
            lon = data.get('lon') # Cambiar 'lon' a la clave real de longitud
            # --------------------------------------------------------------------
            
            if lat is not None and lon is not None:
                return float(lat), float(lon)
            
            st.warning(f"Praxys devolvió datos, pero no se pudo parsear lat/lon para Camión {camion_id}.")
            return None, None
            
        except requests.exceptions.RequestException as e:
            st.warning(f"❌ Error de conexión con Praxys para Camión {camion_id}: {e}")
            return None, None
        except Exception as e:
            st.warning(f"❌ Error inesperado al procesar la respuesta de Praxys: {e}")
            return None, None
            
    # ---------------------------------------------------------------------
    # B. LÓGICA DE SIMULACIÓN (PARA PRUEBAS)
    # ---------------------------------------------------------------------
    else:
        # Usa el estado de sesión para simular movimiento
        if f'sim_step_{camion_id}' not in st.session_state:
            st.session_state[f'sim_step_{camion_id}'] = 0
            st.session_state[f'sim_start_lat_{camion_id}'] = COORDENADAS_ORIGEN[1] # lat
            st.session_state[f'sim_start_lon_{camion_id}'] = COORDENADAS_ORIGEN[0] # lon
            
        step = st.session_state[f'sim_step_{camion_id}']
        
        # Simulación de movimiento diagonal desde el origen (Ingenio)
        # El movimiento es exagerado para que sea visible en el mapa.
        lat_movement = 0.00005 * step
        lon_movement = 0.00008 * step
        
        # Mueve el punto de inicio para la próxima iteración
        st.session_state[f'sim_step_{camion_id}'] += 1
        
        # El siguiente punto es la latitud/longitud de inicio + el movimiento simulado
        return st.session_state[f'sim_start_lat_{camion_id}'] + lat_movement, \
               st.session_state[f'sim_start_lon_{camion_id}'] + lon_movement


def create_route_map(route_data_a, route_data_b, camion_location_a=None, camion_location_b=None):
    """
    Genera un mapa Folium con la visualización de las dos rutas y los marcadores
    de ubicación de los camiones (si están disponibles).
    """
    # 1. Inicializar el mapa
    center_lat = COORDENADAS_ORIGEN[1]
    center_lon = COORDENADAS_ORIGEN[0]
    m = folium.Map(location=[center_lat, center_lon], zoom_start=11, tiles='OpenStreetMap')

    # 2. Función auxiliar para dibujar rutas y marcadores
    def draw_route(map_obj, route_data, color, camion_id):
        if not route_data or not route_data.get('orden_optimo'):
            return

        # Coordenadas de la ruta optimizada (Ingenio -> Lotes -> Ingenio)
        route_coords = [[COORDENADAS_ORIGEN[1], COORDENADAS_ORIGEN[0]]] # Inicio en Ingenio (lat, lon)
        for lote in route_data['orden_optimo']:
            lon, lat = COORDENADAS_LOTES.get(lote, (None, None))
            if lat is not None:
                route_coords.append([lat, lon]) # lat, lon
                
                # Marcar lotes
                folium.Marker([lat, lon], 
                              popup=f"Lote: {lote} ({camion_id})",
                              tooltip=lote,
                              icon=folium.Icon(color=color, icon='cube', prefix='fa')).add_to(map_obj)
                
        # Regreso al origen (opcional, pero ayuda a cerrar el circuito visual)
        route_coords.append([COORDENADAS_ORIGEN[1], COORDENADAS_ORIGEN[0]])

        # Dibujar la línea de la ruta
        folium.PolyLine(route_coords, color=color, weight=4, opacity=0.7).add_to(map_obj)

    # 3. Dibujar Rutas A y B
    draw_route(m, route_data_a, 'blue', 'Camión 1 (A)')
    draw_route(m, route_data_b, 'red', 'Camión 2 (B)')

    # 4. Marcar Origen (Ingenio)
    folium.Marker([COORDENADAS_ORIGEN[1], COORDENADAS_ORIGEN[0]], 
                  popup='INGENIO (Origen)', 
                  icon=folium.Icon(color='green', icon='home', prefix='fa')).add_to(m)

    # 5. Marcar Ubicación de Camiones (Rastreo en vivo/simulado)
    # Camión A
    if camion_location_a and camion_location_a[0] is not None:
        lat, lon = camion_location_a
        folium.Marker([lat, lon],
                      popup=f"Camión 1 (A) - GPS: {lat:.5f}, {lon:.5f}",
                      tooltip="Camión 1 (Rastreo)",
                      icon=folium.Icon(color='darkblue', icon='truck', prefix='fa')).add_to(m)

    # Camión B
    if camion_location_b and camion_location_b[0] is not None:
        lat, lon = camion_location_b
        folium.Marker([lat, lon],
                      popup=f"Camión 2 (B) - GPS: {lat:.5f}, {lon:.5f}",
                      tooltip="Camión 2 (Rastreo)",
                      icon=folium.Icon(color='darkred', icon='truck', prefix='fa')).add_to(m)

    return m

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
    
# Inicializar el modo de rastreo (Simulación por defecto)
if 'tracking_mode' not in st.session_state:
    st.session_state.tracking_mode = 'Simulación'
    
# Inicializar el estado de la simulación
if 'is_simulating' not in st.session_state:
    st.session_state.is_simulating = False
    
# Inicializar los pasos de simulación para cada camión
for camion_id in ['A', 'B']:
    if f'sim_step_{camion_id}' not in st.session_state:
        st.session_state[f'sim_step_{camion_id}'] = 0
        
# =============================================================================
# ESTRUCTURA DEL MENÚ LATERAL Y NAVEGACIÓN
# =============================================================================

st.sidebar.title("Menú Principal")
# MODIFICACIÓN: Agregamos la nueva página de Rastreo
page = st.sidebar.radio(
    "Seleccione una opción:",
    ["Calcular Nueva Ruta", "Historial", "🗺️ Vista de Despacho (Seguimiento)"]
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
            # Usamos st.map simple para la pre-visualización
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
                # Se asume que solve_route_optimization funciona y devuelve 'results'
                results = solve_route_optimization(all_stops_to_visit) 

                if "error" in results:
                    st.error(f"❌ Error en la API de Ruteo: {results['error']}")
                else:
                    # ✅ GENERACIÓN DE ENLACES DE NAVEGACIÓN
                    # Ruta A
                    results['ruta_a']['gmaps_link'] = generate_gmaps_link(results['ruta_a']['orden_optimo'])
                    
                    # Ruta B
                    results['ruta_b']['gmaps_link'] = generate_gmaps_link(results['ruta_b']['orden_optimo'])

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

        col_a, col_b = st.columns(2)

        with col_a:
            st.subheader(f"🚛 Camión 1: {res_a.get('patente', 'N/A')}")
            with st.container(border=True):
                st.markdown(f"**Total Lotes:** {len(res_a.get('lotes_asignados', []))}")
                st.markdown(f"**Distancia Total (TSP):** **{res_a.get('distancia_km', 'N/A')} km**")
                st.markdown(f"**Lotes Asignados:** `{' → '.join(res_a.get('lotes_asignados', []))}`")
                st.info(f"**Orden Óptimo:** Ingenio → {' → '.join(res_a.get('orden_optimo', []))} → Ingenio")
                
                # 👇 ENLACES DE NAVEGACIÓN (Solo Google Maps)
                st.markdown("---")
                st.link_button("🗺️ Ruta en Google Maps Camión A", res_a.get('gmaps_link', '#'))
                st.link_button("🌐 GeoJSON de Ruta A", res_a.get('geojson_link', '#'))


        with col_b:
            st.subheader(f"🚚 Camión 2: {res_b.get('patente', 'N/A')}")
            with st.container(border=True):
                st.markdown(f"**Total Lotes:** {len(res_b.get('lotes_asignados', []))}")
                st.markdown(f"**Distancia Total (TSP):** **{res_b.get('distancia_km', 'N/A')} km**")
                st.markdown(f"**Lotes Asignados:** `{' → '.join(res_b.get('lotes_asignados', []))}`")
                st.info(f"**Orden Óptimo:** Ingenio → {' → '.join(res_b.get('orden_optimo', []))} → Ingenio")
                
                # 👇 ENLACES DE NAVEGACIÓN (Solo Google Maps)
                st.markdown("---")
                st.link_button("🗺️ Ruta en Google Maps Camión B", res_b.get('gmaps_link', '#'))
                st.link_button("🌐 GeoJSON de Ruta B", res_b.get('geojson_link', '#'))

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
        

# =============================================================================
# 4. PÁGINA: VISTA DE DESPACHO (SEGUIMIENTO)
# =============================================================================

elif page == "🗺️ Vista de Despacho (Seguimiento)":
    st.header("🗺️ Rastreo en Vivo sobre Ruta Optimizada")
    
    # 1. Verificar si hay rutas calculadas
    if not st.session_state.results:
        st.warning("⚠️ Debe calcular una ruta óptima primero en la página 'Calcular Nueva Ruta' para activar el rastreo.")
        # Muestra un mapa base simple
        m = create_route_map(None, None)
        folium_static(m, width=1000, height=600)
        st.stop()
    
    results = st.session_state.results
    
    st.info("Presione el botón 'Actualizar' para ver el movimiento en el modo Simulación.")
    
    col_mode, col_update = st.columns([1, 1])

    with col_mode:
        # Toggle para cambiar entre simulación y API real
        tracking_mode = st.radio(
            "Modo de Rastreo:",
            ['Simulación', 'API Real (Praxys)'],
            key='tracking_mode',
            horizontal=True
        )
    
    use_simulation = (tracking_mode == 'Simulación')
    
    # 2. Obtener ubicaciones y Recargar la página automáticamente/manualmente
    
    # Botón para forzar la actualización en modo Simulación
    if use_simulation:
        if col_update.button("Actualizar Posición (Simulación)", type='primary'):
            # El hecho de que se presione el botón ya fuerza un re-run.
            # Solo incrementamos el contador para simular movimiento.
            st.session_state.is_simulating = True
        else:
            st.session_state.is_simulating = False
            
        if st.session_state.is_simulating:
            # Incrementa los pasos de simulación para que la función fetch mueva el camión
            st.session_state[f'sim_step_A'] += 1
            st.session_state[f'sim_step_B'] += 1
            st.info("Simulación activa. Presione 'Actualizar' para mover los camiones.")
    else:
        # En modo API Real, el cache ttl=3 ya maneja la actualización (cada 3 segundos)
        # Y solo mostramos un mensaje
        col_update.empty()
        st.info("Modo API Real activo. La posición se actualizará automáticamente cada 3 segundos.")
    
    
    # 2.1 Obtener ubicación de Camión A
    lat_a, lon_a = fetch_praxys_location('A', use_simulation=use_simulation)
    
    # 2.2 Obtener ubicación de Camión B
    lat_b, lon_b = fetch_praxys_location('B', use_simulation=use_simulation)
    
    # 2.3 Generar y mostrar el mapa
    st.subheader("Ubicación Actual vs. Ruta")
    
    # Crea el mapa con las ubicaciones actuales
    map_to_display = create_route_map(
        results.get('ruta_a'), 
        results.get('ruta_b'), 
        camion_location_a=(lat_a, lon_a),
        camion_location_b=(lat_b, lon_b)
    )
    
    # Muestra el mapa Folium en Streamlit
    folium_static(map_to_display, width=1000, height=600)
    
    # 2.4 Mostrar detalles de ubicación
    col_loc_a, col_loc_b = st.columns(2)
    with col_loc_a:
        if lat_a is not None:
            st.metric("Camión 1 (A) - GPS", f"Lat: {lat_a:.5f} / Lon: {lon_a:.5f}")
        else:
            st.warning("Ubicación de Camión A no disponible.")
    with col_loc_b:
        if lat_b is not None:
            st.metric("Camión 2 (B) - GPS", f"Lat: {lat_b:.5f} / Lon: {lon_b:.5f}")
        else:
            st.warning("Ubicación de Camión B no disponible.")
