import streamlit as st
import pandas as pd
from datetime import datetime
import pytz
import json
import gspread
from oauth2client.service_account import ServiceAccountCredentials # A veces necesario según la versión de gspread

# =============================================================================
# 1. IMPORTACIONES DE LA NUEVA LÓGICA
# =============================================================================
# Asegúrate de que el archivo se llame routing_logic.py
from routing_logic import (
    COORDENADAS_LOTES, 
    solve_route_optimization, 
    VEHICLES, 
    COORDENADAS_ORIGEN
)

# =============================================================================
# 2. CONFIGURACIÓN E INTERFAZ CORPORATIVA
# =============================================================================

st.set_page_config(
    page_title="Sistema de Gestión Logística", 
    layout="wide", 
    page_icon="🏭",
    initial_sidebar_state="expanded"
)

ARG_TZ = pytz.timezone("America/Argentina/Buenos_Aires")

# CSS PROFESIONAL
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    
    /* Tarjetas de Métricas */
    div[data-testid="stMetric"] {
        background-color: #ffffff;
        border: 1px solid #e0e0e0;
        padding: 15px;
        border-radius: 8px;
        box-shadow: 0 2px 4px rgba(0,0,0,0.05);
    }
    
    /* --- BOTONES PRIMARIOS (AZUL) --- */
    div.stButton > button[kind="primary"], a[kind="primary"] {
        background-color: #003366 !important;
        border: 1px solid #003366 !important;
        color: #ffffff !important;
        font-weight: 600 !important;
        border-radius: 6px !important;
        text-align: center !important;
        text-decoration: none !important;
        width: 100% !important;
    }
    div.stButton > button[kind="primary"]:hover, a[kind="primary"]:hover {
        background-color: #002244 !important;
        border-color: #002244 !important;
        color: #ffffff !important;
    }

    /* --- BOTONES SECUNDARIOS (GRIS/NEUTRO) --- */
    div.stButton > button[kind="secondary"], a[kind="secondary"] {
        background-color: #ffffff !important;
        color: #003366 !important;
        border: 1px solid #dce1e6 !important;
        width: 100% !important;
        text-align: center !important;
        text-decoration: none !important;
    }
    div.stButton > button[kind="secondary"]:hover, a[kind="secondary"]:hover {
        border-color: #003366 !important;
        color: #003366 !important;
        background-color: #f0f2f6 !important;
    }

    /* Sidebar */
    [data-testid="stSidebar"] {
        background-color: #f8f9fa;
        border-right: 1px solid #e0e0e0;
    }
    </style>
    """, unsafe_allow_html=True)

COLUMNS = ["Fecha", "Hora", "LotesIngresados", "Lotes_CamionA", "Lotes_CamionB", "Km_CamionA", "Km_CamionB", "Km Totales"]

# =============================================================================
# 3. CONEXIÓN BASE DE DATOS (GOOGLE SHEETS)
# =============================================================================

@st.cache_resource(ttl=3600)
def get_gspread_client():
    try:
        # Opción A: Usando st.secrets (Recomendado para Streamlit Cloud)
        if "gsheets" in st.secrets:
             credentials_dict = dict(st.secrets["gsheets"])
             # Parche para saltos de línea en claves privadas
             if "private_key" in credentials_dict:
                 credentials_dict["private_key"] = credentials_dict["private_key"].replace('\\n', '\n')
             return gspread.service_account_from_dict(credentials_dict)
        
        # Opción B: Archivo local (si corres en tu PC)
        else:
             return gspread.service_account(filename='credentials.json')
    except Exception as e: 
        st.error(f"Error conectando a Google Sheets: {e}")
        return None

def save_new_route_to_sheet(new_route_data):
    client = get_gspread_client()
    if not client: return
    try:
        sh = client.open_by_url(st.secrets["GOOGLE_SHEET_URL"])
        worksheet = sh.worksheet(st.secrets["SHEET_WORKSHEET"])
        row_values = [new_route_data.get(col, "") for col in COLUMNS]
        worksheet.append_row(row_values)
        st.cache_data.clear() # Limpiar caché para recargar historial
    except Exception as e:
        st.error(f"Error registrando operación en Sheets: {e}")

@st.cache_data(ttl=3600)
def get_history_data():
    client = get_gspread_client()
    if not client: return pd.DataFrame(columns=COLUMNS)
    try:
        sh = client.open_by_url(st.secrets["GOOGLE_SHEET_URL"])
        worksheet = sh.worksheet(st.secrets["SHEET_WORKSHEET"])
        data = worksheet.get_all_records()
        return pd.DataFrame(data)
    except: return pd.DataFrame(columns=COLUMNS)

# =============================================================================
# 4. ESTADÍSTICAS
# =============================================================================

def calculate_statistics(df):
    if df.empty: return pd.DataFrame(), pd.DataFrame()
    df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')
    df = df.dropna(subset=['Fecha'])
    df['Mes'] = df['Fecha'].dt.to_period('M')
    
    def safe_count(x):
        try:
            s = str(x).replace('[','').replace(']','').replace("'", "")
            return len([i for i in s.split(',') if i.strip()])
        except: return 0

    if 'Lotes_CamionA' not in df.columns: df['Lotes_CamionA'] = ""
    if 'Lotes_CamionB' not in df.columns: df['Lotes_CamionB'] = ""
    
    df['Total_Asignados'] = df['Lotes_CamionA'].apply(safe_count) + df['Lotes_CamionB'].apply(safe_count)
    
    for col in ['Km_CamionA', 'Km_CamionB']:
        if col not in df.columns: df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0.0)
    
    df['Km_Total'] = df['Km_CamionA'] + df['Km_CamionB']

    daily = df.groupby('Fecha').agg({'Fecha':'count', 'Total_Asignados':'sum', 'Km_CamionA':'sum', 'Km_CamionB':'sum', 'Km_Total':'sum'}).rename(columns={'Fecha':'Rutas_Total', 'Total_Asignados':'Lotes_Asignados_Total', 'Km_CamionA':'Km_CamionA_Total', 'Km_CamionB':'Km_CamionB_Total', 'Km_Total':'Km_Total'}).reset_index()
    daily['Fecha_str'] = daily['Fecha'].dt.strftime('%Y-%m-%d')
    
    monthly = df.groupby('Mes').agg({'Fecha':'count', 'Total_Asignados':'sum', 'Km_CamionA':'sum', 'Km_CamionB':'sum', 'Km_Total':'sum'}).rename(columns={'Fecha':'Rutas_Total', 'Total_Asignados':'Lotes_Asignados_Total', 'Km_CamionA':'Km_CamionA_Total', 'Km_CamionB':'Km_CamionB_Total', 'Km_Total':'Km_Total'}).reset_index()
    monthly['Mes_str'] = monthly['Mes'].astype(str)
    return daily, monthly

# =============================================================================
# 5. NAVEGACIÓN Y ESTADO
# =============================================================================

if 'historial_cargado' not in st.session_state:
    st.cache_data.clear()
    df_hist = get_history_data()
    st.session_state.historial_rutas = df_hist.to_dict('records')
    st.session_state.historial_cargado = True

if 'results' not in st.session_state:
    st.session_state.results = None

with st.sidebar:
    st.image("https://raw.githubusercontent.com/mkzmh/Optimizator-historial/main/LOGO%20CN%20GRUPO%20COLOR%20(1).png", use_container_width=True)
    st.markdown("### Panel de Control")
    page = st.radio("Módulos", ["Planificación Operativa", "Historial", "Estadísticas"])
    st.markdown("---")
    st.caption(f"Registros Totales: **{len(st.session_state.historial_rutas)}**")

# =============================================================================
# PÁGINA 1: PLANIFICACIÓN
# =============================================================================

if page == "Planificación Operativa":
    st.title("Optimizador de Rutas")
    st.markdown("##### Planificación y división óptima de lotes para vehículos de entrega")
    
    st.markdown("---")
    
    lotes_input = st.text_input("Ingreso de Lotes", placeholder="Ingrese códigos separados por coma (Ej: A05, B10, C95)")
    
    all_stops = [l.strip().upper() for l in lotes_input.split(',') if l.strip()]
    valid_stops = [l for l in all_stops if l in COORDENADAS_LOTES]
    invalid_stops = [l for l in all_stops if l not in COORDENADAS_LOTES]

    c1, c2 = st.columns(2)
    c1.metric("Lotes Identificados", len(valid_stops))
    c2.metric("Lotes No Encontrados", len(invalid_stops), delta_color="inverse") 
    
    if invalid_stops:
        st.warning(f"⚠️ **Atención:** El sistema no reconoce estos códigos: {', '.join(invalid_stops)}")

    if valid_stops:
        with st.expander("🗺️ Ver Mapa de Lotes (Vista Previa)", expanded=False):
            map_data = [{'lat': COORDENADAS_ORIGEN[1], 'lon': COORDENADAS_ORIGEN[0], 'name': 'INGENIO', 'color':'#000000'}]
            for l in valid_stops:
                coords = COORDENADAS_LOTES[l]
                map_data.append({'lat': coords[1], 'lon': coords[0], 'name': l, 'color':'#0044ff'})
            st.map(pd.DataFrame(map_data), size=20, color='color')

    st.markdown("---")
    
    col_btn, _ = st.columns([1, 3])
    with col_btn:
        calculate = st.button("Calcular optimización", type="primary", disabled=len(valid_stops)==0, use_container_width=True)

    if calculate:
        with st.spinner("Optimizando rutas y generando archivos KML/GeoJSON..."):
            try:
                # LLAMADA A LA NUEVA LÓGICA
                results = solve_route_optimization(valid_stops)
                st.session_state.results = results

                # Guardado en Sheets
                now = datetime.now(ARG_TZ)
                
                # Extraer datos para Sheets (Nota: keys ahora son Ruta_A y Ruta_B)
                ra = results.get('Ruta_A') or {}
                rb = results.get('Ruta_B') or {}
                
                km_a = ra.get('distancia', 0)
                km_b = rb.get('distancia', 0)
                
                # Extraer lista de lotes del orden (excluyendo Ingenio)
                lotes_a = [x for x in ra.get('orden', []) if x != "Ingenio"]
                lotes_b = [x for x in rb.get('orden', []) if x != "Ingenio"]
                
                new_entry = {
                    "Fecha": now.strftime("%Y-%m-%d"),
                    "Hora": now.strftime("%H:%M:%S"),
                    "LotesIngresados": ", ".join(valid_stops),
                    "Lotes_CamionA": ", ".join(lotes_a),
                    "Lotes_CamionB": ", ".join(lotes_b),
                    "Km_CamionA": km_a,
                    "Km_CamionB": km_b,
                    "Km Totales": km_a + km_b
                }
                save_new_route_to_sheet(new_entry)
                st.session_state.historial_rutas.append(new_entry)
                st.success("Planificación completada y guardada.")
                
            except Exception as e:
                st.error(f"Error durante el cálculo: {e}")

    # --- MOSTRAR RESULTADOS ---
    if st.session_state.results:
        res = st.session_state.results
        
        st.markdown("### Resultados de la Planificación")
        col_a, col_b = st.columns(2)

        # --- RUTA A ---
        with col_a:
            data_a = res.get('Ruta_A')
            with st.container(border=True):
                st.markdown("#### 🚛 Camión 1 (Ruta A)")
                if not data_a:
                    st.info("Sin asignación.")
                elif data_a.get('status') == 'Error API':
                    st.error("Error de conexión con GraphHopper.")
                else:
                    kpi1, kpi2 = st.columns(2)
                    kpi1.metric("Distancia", f"{data_a['distancia']} km")
                    # Contamos paradas menos inicio/fin
                    lotes_count = len([x for x in data_a['orden'] if x != "Ingenio"])
                    kpi2.metric("Lotes", lotes_count)
                    
                    st.markdown("**Secuencia:**")
                    seq = " ➤ ".join(data_a['orden'])
                    st.code(seq, language="text")
                    
                    # LINKS Y DESCARGAS
                    st.markdown("---")
                    st.link_button("📍 Abrir en Google Maps", data_a['links']['gmaps'], type="primary", use_container_width=True)
                    
                    # Botón KML (Organic Maps)
                    st.download_button(
                        label="📂 Descargar KML (Organic Maps)",
                        data=data_a['archivos']['kml'],
                        file_name=f"Ruta_A_{datetime.now().strftime('%Y%m%d')}.kml",
                        mime="application/vnd.google-earth.kml+xml",
                        use_container_width=True
                    )
                    
                    # Botón GeoJSON
                    st.download_button(
                        label="🌍 Descargar GeoJSON",
                        data=json.dumps(data_a['archivos']['geojson'], indent=2),
                        file_name=f"Ruta_A_{datetime.now().strftime('%Y%m%d')}.geojson",
                        mime="application/json",
                        use_container_width=True
                    )
                    
                    st.link_button("🌐 Ver Online (Geojson.io)", data_a['links']['geojson_io'], use_container_width=True)

        # --- RUTA B ---
        with col_b:
            data_b = res.get('Ruta_B')
            with st.container(border=True):
                st.markdown("#### 🚚 Camión 2 (Ruta B)")
                if not data_b:
                    st.info("Sin asignación.")
                elif data_b.get('status') == 'Error API':
                    st.error("Error de conexión con GraphHopper.")
                else:
                    kpi1, kpi2 = st.columns(2)
                    kpi1.metric("Distancia", f"{data_b['distancia']} km")
                    lotes_count = len([x for x in data_b['orden'] if x != "Ingenio"])
                    kpi2.metric("Lotes", lotes_count)
                    
                    st.markdown("**Secuencia:**")
                    seq = " ➤ ".join(data_b['orden'])
                    st.code(seq, language="text")
                    
                    st.markdown("---")
                    st.link_button("📍 Abrir en Google Maps", data_b['links']['gmaps'], type="primary", use_container_width=True)
                    
                    st.download_button(
                        label="📂 Descargar KML (Organic Maps)",
                        data=data_b['archivos']['kml'],
                        file_name=f"Ruta_B_{datetime.now().strftime('%Y%m%d')}.kml",
                        mime="application/vnd.google-earth.kml+xml",
                        use_container_width=True
                    )
                    
                    st.download_button(
                        label="🌍 Descargar GeoJSON",
                        data=json.dumps(data_b['archivos']['geojson'], indent=2),
                        file_name=f"Ruta_B_{datetime.now().strftime('%Y%m%d')}.geojson",
                        mime="application/json",
                        use_container_width=True
                    )

                    st.link_button("🌐 Ver Online (Geojson.io)", data_b['links']['geojson_io'], use_container_width=True)

# =============================================================================
# PÁGINA 2: HISTORIAL
# =============================================================================
elif page == "Historial":
    st.title("Historial de Operaciones")
    df = pd.DataFrame(st.session_state.historial_rutas)
    if not df.empty:
        st.dataframe(
            df, 
            use_container_width=True, 
            hide_index=True,
            column_config={
                "Km_CamionA": st.column_config.NumberColumn("Km Unidad A", format="%.2f"),
                "Km_CamionB": st.column_config.NumberColumn("Km Unidad B", format="%.2f"),
                "Km Totales": st.column_config.NumberColumn("Km Totales", format="%.2f"),
            }
        )
    else:
        st.info("No se encontraron registros previos.")

# =============================================================================
# PÁGINA 3: ESTADÍSTICAS
# =============================================================================
elif page == "Estadísticas":
    st.title("Indicadores de Desempeño")
    df = pd.DataFrame(st.session_state.historial_rutas)
    
    if not df.empty:
        day, month = calculate_statistics(df)
        
        st.subheader("Desempeño Diario")
        if not day.empty:
            cols_show = {
                'Fecha_str': 'Fecha', 'Rutas_Total': 'Rutas', 'Lotes_Asignados_Total': 'Lotes Entregados',
                'Km_CamionA_Total': 'Km Unidad A', 'Km_CamionB_Total': 'Km Unidad B', 'Km_Total': 'Km Totales'
            }
            st.dataframe(day[list(cols_show.keys())].rename(columns=cols_show), use_container_width=True, hide_index=True)
            
            st.markdown("##### Kilómetros Totales Recorridos por Día")
            st.bar_chart(day, x='Fecha_str', y=['Km_CamionA_Total', 'Km_CamionB_Total'], color=['#003366', '#00A8E8'])
        
        st.subheader("Consolidado Mensual")
        if not month.empty:
            st.dataframe(
                month, 
                use_container_width=True,
                column_config={
                    "Km_Total": st.column_config.NumberColumn("Km Totales", format="%.2f"),
                    "Mes_str": "Período"
                }
            )
    else:
        st.info("Se requieren datos operativos para generar los indicadores.")
