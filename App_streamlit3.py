import streamlit as st
import pandas as pd
from datetime import datetime
import pytz
import os
import time
import json
import gspread
from urllib.parse import quote

# =============================================================================
# 1. IMPORTACIONES
# =============================================================================
from Routing_logic3 import (
    COORDENADAS_LOTES, solve_route_optimization, VEHICLES, COORDENADAS_ORIGEN,
    generate_geojson_io_link, generate_geojson, COORDENADAS_LOTES_REVERSO
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

# CSS PROFESIONAL (Estilo Dashboard/Enterprise)
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
    
    /* ESTILO PARA BOTONES PRIMARIOS (stButton y stLinkButton) - AZUL CORPORATIVO */
    div.stButton > button:first-child, a[kind="primary"] {
        background-color: #003366 !important;
        color: white !important;
        border: none !important;
        border-radius: 4px !important;
        padding: 0.6rem 1.2rem !important;
        font-weight: 600 !important;
        font-size: 14px !important;
        text-decoration: none !important;
    }
    
    div.stButton > button:first-child:hover, a[kind="primary"]:hover {
        background-color: #002244 !important;
        color: #e0e0e0 !important;
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
# 3. FUNCIONES AUXILIARES
# =============================================================================

def generate_gmaps_link(stops_order_names):
    """Genera el link oficial de navegación de Google Maps"""
    if not stops_order_names: return '#'
    lat_orig, lon_orig = COORDENADAS_ORIGEN[1], COORDENADAS_ORIGEN[0]
    origin_str = f"{lat_orig},{lon_orig}"
    
    waypoints = []
    for lote_nombre in stops_order_names:
        if lote_nombre in COORDENADAS_LOTES:
            lon, lat = COORDENADAS_LOTES[lote_nombre]
            waypoints.append(f"{lat},{lon}")
            
    base_url = "https://www.google.com/maps/dir/"
    route_path = "/".join([origin_str] + waypoints + [origin_str])
    return base_url + route_path

# =============================================================================
# 4. CONEXIÓN BASE DE DATOS
# =============================================================================

@st.cache_resource(ttl=3600)
def get_gspread_client():
    try:
        credentials_dict = {
            "type": "service_account",
            "project_id": st.secrets["gsheets_project_id"],
            "private_key_id": st.secrets["gsheets_private_key_id"],
            "private_key": st.secrets["gsheets_private_key"].replace('\\n', '\n'), 
            "client_email": st.secrets["gsheets_client_email"],
            "client_id": st.secrets["gsheets_client_id"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "auth_provider_x509_cert_url": "https://www.googleapis.com/oauth2/v1/certs",
            "client_x509_cert_url": f"https://www.googleapis.com/robot/v1/metadata/x509/{st.secrets['gsheets_client_email']}",
            "universe_domain": "googleapis.com"
        }
        return gspread.service_account_from_dict(credentials_dict)
    except Exception: return None

def save_new_route_to_sheet(new_route_data):
    client = get_gspread_client()
    if not client: return
    try:
        sh = client.open_by_url(st.secrets["GOOGLE_SHEET_URL"])
        worksheet = sh.worksheet(st.secrets["SHEET_WORKSHEET"])
        row_values = [new_route_data.get(col, "") for col in COLUMNS]
        worksheet.append_row(row_values)
        st.cache_data.clear()
    except Exception as e:
        st.error(f"Error registrando operación: {e}")

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
    df['Total_Asignados'] = df['Lotes_CamionA'].apply(safe_count) + df['Lotes_CamionB'].apply(safe_count)
    for col in ['Km_CamionA', 'Km_CamionB', 'Km Totales']:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    daily = df.groupby('Fecha').agg({'Fecha':'count', 'Total_Asignados':'sum', 'Km Totales':'sum'}).rename(columns={'Fecha':'Operaciones'}).reset_index()
    daily['Fecha_str'] = daily['Fecha'].dt.strftime('%Y-%m-%d')
    monthly = df.groupby('Mes').agg({'Fecha':'count', 'Total_Asignados':'sum', 'Km Totales':'sum'}).rename(columns={'Fecha':'Operaciones'}).reset_index()
    monthly['Mes_str'] = monthly['Mes'].astype(str)
    return daily, monthly

# =============================================================================
# 6. NAVEGACIÓN
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
    page = st.radio("Módulos", ["Planificación Operativa", "Registro Histórico", "Indicadores de Gestión"])
    st.markdown("---")
    st.caption(f"Registros Totales: **{len(st.session_state.historial_rutas)}**")

# =============================================================================
# PÁGINA 1: PLANIFICACIÓN
# =============================================================================

if page == "Planificación Operativa":
    st.title("Sistema de Optimización Logística")
    st.markdown("##### Planificación y división óptima de lotes para vehículos de entrega")
    
    st.markdown("---")
    
    # Input
    lotes_input = st.text_input("Ingreso de Lotes", placeholder="Ingrese códigos separados por coma (Ej: A05, B10, C95)")
    
    all_stops = [l.strip().upper() for l in lotes_input.split(',') if l.strip()]
    valid_stops = [l for l in all_stops if l in COORDENADAS_LOTES]
    invalid_stops = [l for l in all_stops if l not in COORDENADAS_LOTES]

    # Estado de Lotes
    c1, c2 = st.columns([1, 3])
    c1.metric("Lotes Identificados", len(valid_stops))
    
    if invalid_stops:
        c2.error(f"⚠️ **Atención:** No se reconocen: **{', '.join(invalid_stops)}**")
    elif valid_stops:
        c2.success("Todos los lotes son válidos.")

    # Mapa Desplegable
    if valid_stops:
        with st.expander("🗺️ Ver Mapa de Lotes (Desplegar)", expanded=False):
            map_data = [{'lat': COORDENADAS_ORIGEN[1], 'lon': COORDENADAS_ORIGEN[0], 'name': 'INGENIO', 'color':'#000000'}]
            for l in valid_stops:
                coords = COORDENADAS_LOTES[l]
                map_data.append({'lat': coords[1], 'lon': coords[0], 'name': l, 'color':'#0044ff'})
            st.map(pd.DataFrame(map_data), size=20, color='color')

    st.markdown("---")
    
    # BOTÓN DE CÁLCULO
    col_btn, _ = st.columns([1, 3])
    with col_btn:
        # Este botón es type="primary" para que sea azul
        calculate = st.button("Ejecutar Algoritmo", type="primary", disabled=len(valid_stops)==0, use_container_width=True)

    if calculate:
        with st.spinner("Calculando distribución óptima de carga..."):
            try:
                results = solve_route_optimization(valid_stops)
                st.session_state.results = results

                if "error" not in results:
                    now = datetime.now(ARG_TZ)
                    ra = results.get('ruta_a', {})
                    rb = results.get('ruta_b', {})
                    
                    new_entry = {
                        "Fecha": now.strftime("%Y-%m-%d"),
                        "Hora": now.strftime("%H:%M:%S"),
                        "LotesIngresados": ", ".join(valid_stops),
                        "Lotes_CamionA": str(ra.get('lotes_asignados', [])),
                        "Lotes_CamionB": str(rb.get('lotes_asignados', [])),
                        "Km_CamionA": ra.get('distancia_km', 0),
                        "Km_CamionB": rb.get('distancia_km', 0),
                    }
                    new_entry["Km Totales"] = new_entry["Km_CamionA"] + new_entry["Km_CamionB"]
                    save_new_route_to_sheet(new_entry)
                    st.session_state.historial_rutas.append(new_entry)
                    st.success("Planificación completada y guardada.")
            except Exception as e:
                st.error(f"Error crítico: {e}")

    # RESULTADOS
    if st.session_state.results:
        res = st.session_state.results
        if "error" in res:
            st.error(res['error'])
        else:
            st.markdown("### Resultados de la Planificación")
            col_a, col_b = st.columns(2)

            # UNIDAD A
            with col_a:
                ra = res.get('ruta_a', {})
                with st.container(border=True):
                    # TITULO CON PATENTE DIRECTAMENTE
                    patente_a = ra.get('patente', 'N/A')
                    st.markdown(f"#### 🚛 Camión 1: {patente_a}")
                    
                    if ra.get('mensaje'):
                        st.info("Sin asignación de lotes.")
                    else:
                        kpi1, kpi2 = st.columns(2)
                        kpi1.metric("Distancia", f"{ra.get('distancia_km',0)} km")
                        kpi2.metric("Lotes", len(ra.get('lotes_asignados', [])))
                        
                        st.markdown("**Secuencia de Entrega:**")
                        seq = " ➤ ".join(["Ingenio"] + ra.get('orden_optimo', []) + ["Ingenio"])
                        st.code(seq, language="text")
                        
                        # Datos para botones
                        link_geo = ra.get('geojson_link', '#')
                        link_maps = generate_gmaps_link(ra.get('orden_optimo', []))
                        json_data = json.dumps(ra.get('geojson_data', {}))
                        
                        # BOTONES
                        st.markdown("---")
                        # 1. BOTÓN AZUL PRINCIPAL (Iniciar Ruta)
                        st.link_button("📍 Iniciar Ruta (Google Maps)", link_maps, type="primary", use_container_width=True)
                        
                        # 2. BOTÓN SECUNDARIO (Ver Mapa)
                        st.link_button("🌐 Ver Mapa Web (GeoJSON)", link_geo, use_container_width=True)

            # UNIDAD B
            with col_b:
                rb = res.get('ruta_b', {})
                with st.container(border=True):
                    # TITULO CON PATENTE DIRECTAMENTE
                    patente_b = rb.get('patente', 'N/A')
                    st.markdown(f"#### 🚛 Camión 2: {patente_b}")
                    
                    if rb.get('mensaje'):
                        st.info("Sin asignación de lotes.")
                    else:
                        kpi1, kpi2 = st.columns(2)
                        kpi1.metric("Distancia", f"{rb.get('distancia_km',0)} km")
                        kpi2.metric("Lotes", len(rb.get('lotes_asignados', [])))
                        
                        st.markdown("**Secuencia de Entrega:**")
                        seq = " ➤ ".join(["Ingenio"] + rb.get('orden_optimo', []) + ["Ingenio"])
                        st.code(seq, language="text")
                        
                        # Datos para botones
                        link_geo = rb.get('geojson_link', '#')
                        link_maps = generate_gmaps_link(rb.get('orden_optimo', []))
                        json_data = json.dumps(rb.get('geojson_data', {}))
                        
                        # BOTONES
                        st.markdown("---")
                        # 1. BOTÓN AZUL PRINCIPAL (Iniciar Ruta)
                        st.link_button("📍 Iniciar Ruta (Google Maps)", link_maps, type="primary", use_container_width=True)
                        
                        # 2. BOTÓN SECUNDARIO (Ver Mapa)
                        st.link_button("🌐 Ver Mapa Web (GeoJSON)", link_geo, use_container_width=True)

# =============================================================================
# PÁGINA 2: HISTORIAL
# =============================================================================
elif page == "Registro Histórico":
    st.title("Registro Histórico de Operaciones")
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
# PÁGINA 3: ESTADÍSTICAS (TU VERSIÓN EXACTA)
# =============================================================================

elif page == "Estadísticas":
    
    st.cache_data.clear() # Asegurar datos frescos
    
    st.header("📊 Estadísticas de Ruteo")
    st.caption("Análisis diario y mensual de la actividad de optimización.")

    df_historial = get_history_data()

    if df_historial.empty:
        st.info("No hay datos en el historial para generar estadísticas.")
    else:
        daily_stats, monthly_stats = calculate_statistics(df_historial)

        # -----------------------------------------------------
        # Estadísticas Diarias
        # -----------------------------------------------------
        st.subheader("Resumen Diario")
        if not daily_stats.empty:
            
            columns_to_show = {
                'Fecha_str': 'Fecha',
                'Rutas_Total': 'Rutas Calculadas',
                'Lotes_Asignados_Total': 'Lotes Asignados',
                'Km_CamionA_Total': 'KM Camión A',
                'Km_CamionB_Total': 'KM Camión B',
                'Km_Total': 'KM Totales',
                'Km_Promedio_Ruta': 'KM Promedio por Ruta'
            }

            st.dataframe(
                daily_stats[list(columns_to_show.keys())].rename(columns=columns_to_show),
                use_container_width=True,
                hide_index=True,
                column_config={
                    'KM Camión A': st.column_config.NumberColumn("KM Camión A", format="%.2f km"),
                    'KM Camión B': st.column_config.NumberColumn("KM Camión B", format="%.2f km"),
                    'KM Totales': st.column_config.NumberColumn("KM Totales", format="%.2f km"),
                    'KM Promedio por Ruta': st.column_config.NumberColumn("KM Promedio/Ruta", format="%.2f km"),
                }
            )
            
            st.markdown("##### Kilómetros Totales Recorridos por Día")
            st.bar_chart(
                daily_stats,
                x='Fecha_str',
                y=['Km_CamionA_Total', 'Km_CamionB_Total'],
                color=['#0044FF', '#FF4B4B']
            )

        # -----------------------------------------------------
        # Estadísticas Mensuales
        # -----------------------------------------------------
        st.subheader("Resumen Mensual")
        if not monthly_stats.empty:
            
            columns_to_show = {
                'Mes_str': 'Mes',
                'Rutas_Total': 'Rutas Calculadas',
                'Lotes_Asignados_Total': 'Lotes Asignados',
                'Km_CamionA_Total': 'KM Camión A',
                'Km_CamionB_Total': 'KM Camión B',
                'Km_Total': 'KM Totales',
                'Km_Promedio_Ruta': 'KM Promedio por Ruta'
            }

            st.dataframe(
                monthly_stats[list(columns_to_show.keys())].rename(columns=columns_to_show),
                use_container_width=True,
                hide_index=True,
                column_config={
                    'KM Camión A': st.column_config.NumberColumn("KM Camión A", format="%.2f km"),
                    'KM Camión B': st.column_config.NumberColumn("KM Camión B", format="%.2f km"),
                    'KM Totales': st.column_config.NumberColumn("KM Totales", format="%.2f km"),
                    'KM Promedio por Ruta': st.column_config.NumberColumn("KM Promedio/Ruta", format="%.2f km"),
                }
            )
        st.divider()
        st.caption("Nota: Los KM Totales/Promedio se calculan usando la suma de las distancias optimizadas de cada camión.")

