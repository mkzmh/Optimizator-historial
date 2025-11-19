import streamlit as st
import pandas as pd
from datetime import datetime
import pytz
import os
import time
import json
import gspread

# IMPORTACIONES DEL CEREBRO
from Routing_logic3 import (
    COORDENADAS_LOTES, 
    solve_route_optimization, 
    VEHICLES, 
    COORDENADAS_ORIGEN
)

# CONFIGURACIÓN INICIAL
st.set_page_config(page_title="Optimizador Logístico", layout="wide", page_icon="🚛")
ARG_TZ = pytz.timezone("America/Argentina/Buenos_Aires")

st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    .stMetric {background-color: #f8f9fa; padding: 15px; border-radius: 10px; border: 1px solid #e9ecef;}
    div[data-testid="stExpander"] {background-color: #ffffff; border: 1px solid #ddd; border-radius: 5px;}
    </style>
    """, unsafe_allow_html=True)

COLUMNS = ["Fecha", "Hora", "LotesIngresados", "Lotes_CamionA", "Lotes_CamionB", "Km_CamionA", "Km_CamionB", "Km Totales"]

# FUNCIONES AUXILIARES
def generate_gmaps_link(stops_order_names):
    if not stops_order_names: return '#'
    lon_orig, lat_orig = COORDENADAS_ORIGEN
    route_parts = [f"{lat_orig},{lon_orig}"] 
    for lote_nombre in stops_order_names:
        if lote_nombre in COORDENADAS_LOTES:
            lon, lat = COORDENADAS_LOTES[lote_nombre]
            route_parts.append(f"{lat},{lon}")
    route_parts.append(f"{lat_orig},{lon_orig}")
    return f"https://www.google.com/maps/dir/" + "/".join(route_parts)

# CONEXIÓN GOOGLE SHEETS
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
    except Exception as e:
        st.error(f"⚠️ Error credenciales GSheets: {e}")
        return None

def save_new_route_to_sheet(new_route_data):
    client = get_gspread_client()
    if not client: return
    try:
        sh = client.open_by_url(st.secrets["GOOGLE_SHEET_URL"])
        worksheet = sh.worksheet(st.secrets["SHEET_WORKSHEET"])
        row_values = [new_route_data.get(col, "") for col in COLUMNS]
        worksheet.append_row(row_values)
        st.toast("✅ Guardado en Historial", icon="💾")
        st.cache_data.clear()
    except Exception as e:
        st.error(f"❌ Error guardando hoja: {e}")

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

# ESTADÍSTICAS
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

    daily = df.groupby('Fecha').agg({'Fecha':'count', 'Total_Asignados':'sum', 'Km Totales':'sum'}).rename(columns={'Fecha':'Rutas'}).reset_index()
    daily['Fecha_str'] = daily['Fecha'].dt.strftime('%Y-%m-%d')
    monthly = df.groupby('Mes').agg({'Fecha':'count', 'Total_Asignados':'sum', 'Km Totales':'sum'}).rename(columns={'Fecha':'Rutas'}).reset_index()
    monthly['Mes_str'] = monthly['Mes'].astype(str)
    return daily, monthly

# SESIÓN
if 'historial_cargado' not in st.session_state:
    st.cache_data.clear()
    df_hist = get_history_data()
    st.session_state.historial_rutas = df_hist.to_dict('records')
    st.session_state.historial_cargado = True

if 'results' not in st.session_state:
    st.session_state.results = None

st.sidebar.image("https://raw.githubusercontent.com/mkzmh/Optimizator-historial/main/LOGO%20CN%20GRUPO%20COLOR%20(1).png", use_container_width=True)
st.sidebar.title("Menú")
page = st.sidebar.radio("Ir a:", ["Calcular Nueva Ruta", "Historial", "Estadísticas"])
st.sidebar.divider()
st.sidebar.info(f"📂 Registros: {len(st.session_state.historial_rutas)}")

# PÁGINA CALCULAR
if page == "Calcular Nueva Ruta":
    st.title("🚜 Optimizador Logístico (Híbrido)")
    st.markdown("**Modo:** KML (Tierra) + API (Asfalto). Descarga GPX para OsmAnd.")
    st.divider()

    lotes_input = st.text_input("📍 Ingrese Lotes (separados por coma):", placeholder="Ej: A05, B10, C95")
    all_stops = [l.strip().upper() for l in lotes_input.split(',') if l.strip()]
    valid_stops = [l for l in all_stops if l in COORDENADAS_LOTES]
    invalid_stops = [l for l in all_stops if l not in COORDENADAS_LOTES]

    c1, c2 = st.columns([3, 1])
    with c1:
        if valid_stops:
            map_data = [{'lat': COORDENADAS_ORIGEN[1], 'lon': COORDENADAS_ORIGEN[0], 'name': 'INGENIO', 'color':'#00ff00'}]
            for l in valid_stops:
                coords = COORDENADAS_LOTES[l]
                map_data.append({'lat': coords[1], 'lon': coords[0], 'name': l, 'color':'#0000ff'})
            st.map(pd.DataFrame(map_data), size=20, color='color')
        else: st.info("Ingrese lotes.")
    with c2:
        st.metric("Lotes Válidos", len(valid_stops))
        if invalid_stops: st.warning(f"Desconocidos: {', '.join(invalid_stops)}")

    if st.button("🚀 Calcular Distribución", type="primary", disabled=len(valid_stops)==0):
        with st.spinner("🔄 Calculando Rutas Híbridas..."):
            try:
                results = solve_route_optimization(valid_stops)
                st.session_state.results = results

                if "error" not in results:
                    now = datetime.now(ARG_TZ)
                    ra = results.get('ruta_a', {})
                    rb = results.get('ruta_b', {})
                    new_entry = {
                        "Fecha": now.strftime("%Y-%m-%d"), "Hora": now.strftime("%H:%M:%S"),
                        "LotesIngresados": ", ".join(valid_stops),
                        "Lotes_CamionA": str(ra.get('lotes_asignados', [])),
                        "Lotes_CamionB": str(rb.get('lotes_asignados', [])),
                        "Km_CamionA": ra.get('distancia_km', 0), "Km_CamionB": rb.get('distancia_km', 0),
                    }
                    new_entry["Km Totales"] = new_entry["Km_CamionA"] + new_entry["Km_CamionB"]
                    save_new_route_to_sheet(new_entry)
                    st.session_state.historial_rutas.append(new_entry)
                    st.success("¡Cálculo Completado!")
            except Exception as e: st.error(f"❌ Error: {e}")

    if st.session_state.results:
        res = st.session_state.results
        with st.expander("🔍 Ver Detalles Técnicos (Debug)"): st.json(res)

        if "error" in res: st.error(res['error'])
        else:
            st.divider()
            col_a, col_b = st.columns(2)
            
            # CAMIÓN A
            with col_a:
                ra = res.get('ruta_a', {})
                st.subheader(f"🚛 {ra.get('nombre', 'Camión A')}")
                if 'error' in ra: st.error(ra['error'])
                elif ra.get('mensaje'): st.info(ra['mensaje'])
                else:
                    st.metric("Distancia", f"{ra.get('distancia_km',0)} km")
                    st.write("**Orden de Paradas:**")
                    st.code(" ↓ \n".join(["🏭 Ingenio"] + ra.get('orden_optimo', []) + ["🏁 Ingenio"]))
                    
                    # LINKS
                    link_geo = ra.get('geojson_link', '#')
                    link_maps = generate_gmaps_link(ra.get('orden_optimo', []))
                    gpx_data = ra.get('gpx_data', "")

                    c1, c2 = st.columns(2)
                    c1.link_button("🗺️ Ver en Web", link_geo, use_container_width=True)
                    # BOTÓN DE DESCARGA GPX (Correcto para OsmAnd)
                    c2.download_button("⬇️ Descargar GPX (OsmAnd)", data=gpx_data, file_name="Ruta_A.gpx", mime="application/gpx+xml", use_container_width=True)
                    st.link_button("📱 Puntos en GMaps", link_maps, use_container_width=True)

            # CAMIÓN B
            with col_b:
                rb = res.get('ruta_b', {})
                st.subheader(f"🚛 {rb.get('nombre', 'Camión B')}")
                if 'error' in rb: st.error(rb['error'])
                elif rb.get('mensaje'): st.info(rb['mensaje'])
                else:
                    st.metric("Distancia", f"{rb.get('distancia_km',0)} km")
                    st.write("**Orden de Paradas:**")
                    st.code(" ↓ \n".join(["🏭 Ingenio"] + rb.get('orden_optimo', []) + ["🏁 Ingenio"]))
                    
                    # LINKS
                    link_geo = rb.get('geojson_link', '#')
                    link_maps = generate_gmaps_link(rb.get('orden_optimo', []))
                    gpx_data = rb.get('gpx_data', "")
                    
                    c1, c2 = st.columns(2)
                    c1.link_button("🗺️ Ver en Web", link_geo, use_container_width=True)
                    # BOTÓN DE DESCARGA GPX
                    c2.download_button("⬇️ Descargar GPX (OsmAnd)", data=gpx_data, file_name="Ruta_B.gpx", mime="application/gpx+xml", use_container_width=True)
                    st.link_button("📱 Puntos en GMaps", link_maps, use_container_width=True)

# PÁGINAS SECUNDARIAS
elif page == "Historial":
    st.title("📋 Historial")
    df = pd.DataFrame(st.session_state.historial_rutas)
    if not df.empty: st.dataframe(df, use_container_width=True, hide_index=True)
    else: st.info("Historial vacío.")

elif page == "Estadísticas":
    st.title("📊 Estadísticas")
    df = pd.DataFrame(st.session_state.historial_rutas)
    if not df.empty:
        d, m = calculate_statistics(df)
        st.subheader("Diario"); st.bar_chart(d, x='Fecha_str', y='Km_Dia')
        st.subheader("Mensual"); st.dataframe(m, use_container_width=True)
    else: st.info("Sin datos.")
