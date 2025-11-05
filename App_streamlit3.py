import streamlit as st
import pandas as pd
from datetime import date
import os
import time 
import json # Mantenido por si acaso, aunque ya no es estrictamente necesario para la lógica de Sheets

# Importa la lógica y constantes del módulo vecino (Asegúrate que se llama 'routing_logic.py')
from routing_logic import COORDENADAS_LOTES, solve_route_optimization, VEHICLES, COORDENADAS_ORIGEN 

# =============================================================================
# CONFIGURACIÓN INICIAL Y PERSISTENCIA DE DATOS (CSV)
# =============================================================================

st.set_page_config(page_title="Optimizador Bimodal de Rutas", layout="wide")

# Ocultar menú de Streamlit y footer
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)

# Define el nombre del archivo de historial para persistencia
HISTORY_FILE = 'historial.csv'
# Encabezados en el orden del CSV
COLUMNS = ["Fecha", "Lotes_ingresados", "Lotes_CamionA", "Lotes_CamionB", "KmRecorridos_CamionA", "KmRecorridos_CamionB"]


# --- Funciones de Persistencia CSV ---

@st.cache_data(ttl=3600)
def get_history_data():
    """Lee el historial del archivo CSV."""
    if os.path.exists(HISTORY_FILE):
        try:
            df = pd.read_csv(HISTORY_FILE)
            return df
        except Exception as e:
            # Si el archivo existe pero está corrupto o vacío, retorna un DataFrame vacío
            return pd.DataFrame(columns=COLUMNS)
    else:
        # Si el archivo no existe, devuelve un DataFrame vacío con las columnas esperadas
        return pd.DataFrame(columns=COLUMNS)

def save_new_route_to_csv(new_route_data):
    """Escribe la nueva ruta al final del archivo CSV."""
    
    # Carga el historial actual
    current_df = get_history_data()

    # Formatea los datos de la nueva ruta en un DataFrame de una fila
    new_row_df = pd.DataFrame([new_route_data])
    
    # Aseguramos que la nueva fila tenga las columnas en el orden correcto antes de concatenar
    new_row_df = new_row_df[list(new_row_df.columns)]
    
    # Concatena y guarda
    updated_df = pd.concat([current_df, new_row_df], ignore_index=True)
    
    # Sobrescribe el archivo CSV con los datos actualizados
    updated_df.to_csv(HISTORY_FILE, index=False)
    
    # Invalida la caché para que la próxima vez que se llame a get_history_data() lea el archivo actualizado
    st.cache_data.clear()


# -------------------------------------------------------------------------
# INICIALIZACIÓN DE LA SESIÓN Y CLIENTE
# -------------------------------------------------------------------------

# Inicializar el estado de la sesión para guardar el historial PERMANENTE
if 'historial_cargado' not in st.session_state:
    df_history = get_history_data()
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
    ["Calcular Nueva Ruta", "Historial", "Estadísticas"]
)
st.sidebar.divider()
st.sidebar.info(f"Rutas Guardadas: {len(st.session_state.historial_rutas)}")

# =============================================================================
# 1. PÁGINA: CALCULAR NUEVA RUTA (PÁGINA PRINCIPAL)
# =============================================================================

if page == "Calcular Nueva Ruta":
    st.title("🚚 Optimizator")
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

        with st.spinner('Realizando cálculo óptimo y agrupando rutas (¡75s de espera incluidos!)...'):
            try:
                results = solve_route_optimization(all_stops_to_visit) 
                
                if "error" in results:
                    st.error(f"❌ Error en la API de Ruteo: {results['error']}")
                else:
                    # ✅ CREA LA ESTRUCTURA DEL REGISTRO PARA GUARDADO EN CSV
                    new_route = {
                        "Fecha": date.today().strftime("%Y-%m-%d"),
                        "Lotes_ingresados": ", ".join(all_stops_to_visit),
                        "Lotes_CamionA": str(results['ruta_a']['lotes_asignados']), # Guardar como string
                        "Lotes_CamionB": str(results['ruta_b']['lotes_asignados']), # Guardar como string
                        "KmRecorridos_CamionA": results['ruta_a']['distancia_km'],
                        "KmRecorridos_CamionB": results['ruta_b']['distancia_km'],
                    }
                    
                    # 🚀 GUARDA PERMANENTEMENTE EN CSV
                    save_new_route_to_csv(new_route)
                    
                    # ACTUALIZA EL ESTADO DE LA SESIÓN
                    st.session_state.historial_rutas.append(new_route)
                    st.session_state.results = results
                    st.success("✅ Cálculo finalizado y rutas optimizadas.")
                    
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
                st.link_button("🌐 Ver Ruta A en GeoJSON.io", res_a.get('geojson_link', '#'))
            
        with col_b:
            st.subheader(f"🚚 Camión 2: {res_b.get('patente', 'N/A')}")
            with st.container(border=True):
                st.markdown(f"**Total Lotes:** {len(res_b.get('lotes_asignados', []))}")
                st.markdown(f"**Distancia Total (TSP):** **{res_b.get('distancia_km', 'N/A')} km**")
                st.markdown(f"**Lotes Asignados:** `{' → '.join(res_b.get('lotes_asignados', []))}`")
                st.info(f"**Orden Óptimo:** Ingenio → {' → '.join(res_b.get('orden_optimo', []))} → Ingenio")
                st.link_button("🌐 Ver Ruta B en GeoJSON.io", res_b.get('geojson_link', '#'))

    else:
        st.info("El reporte aparecerá aquí después de un cálculo exitoso.")


# =============================================================================
# 3. PÁGINA: HISTORIAL
# =============================================================================

elif page == "Historial":
    st.header("📋 Historial de Rutas Calculadas")
    
    if st.session_state.historial_rutas:
        df_historial = pd.DataFrame(st.session_state.historial_rutas)
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
                         "Lotes_ingresados": "Lotes Ingresados"
                     })
        
        st.divider()
        st.warning("El historial se guarda permanentemente en el archivo CSV.")
        
        if st.button("🗑️ Borrar Historial PERMANENTE"):
            # Vacia el estado de la sesión
            st.session_state.historial_rutas = []
            # Elimina el archivo CSV
            if os.path.exists(HISTORY_FILE):
                os.remove(HISTORY_FILE)
            st.rerun()

    else:
        st.info("No hay rutas guardadas. Realice un cálculo en la página principal.")

# =============================================================================
# 4. PÁGINA: ESTADÍSTICAS
# =============================================================================

elif page == "Estadísticas":
    st.header("📈 Estadísticas de Kilometraje")
    
    if st.session_state.historial_rutas:
        df = pd.DataFrame(st.session_state.historial_rutas)
        
        # CÁLCULOS
        df['Fecha'] = pd.to_datetime(df['Fecha'], errors='coerce')
        df['KmRecorridos_CamionA'] = pd.to_numeric(df['KmRecorridos_CamionA'], errors='coerce')
        df['KmRecorridos_CamionB'] = pd.to_numeric(df['KmRecorridos_CamionB'], errors='coerce')
        
        df_diario = df.groupby(df['Fecha'].dt.date)[['KmRecorridos_CamionA', 'KmRecorridos_CamionB']].sum().reset_index()
        df_diario.columns = ['Fecha', 'KM Camión A', 'KM Camión B']
        
        df['mes_año'] = df['Fecha'].dt.to_period('M')
        df_mensual = df.groupby('mes_año')[['KmRecorridos_CamionA', 'KmRecorridos_CamionB']].sum().reset_index()
        df_mensual['Mes'] = df_mensual['mes_año'].astype(str)
        
        df_mensual_final = df_mensual[['Mes', 'KmRecorridos_CamionA', 'KmRecorridos_CamionB']].rename(columns={'KmRecorridos_CamionA': 'KM Camión A', 'KmRecorridos_CamionB': 'KM Camión B'})


        st.subheader("Kilómetros Recorridos por Día")
        st.dataframe(df_diario, use_container_width=True)
        st.bar_chart(df_diario.set_index('Fecha'))

        st.subheader("Kilómetros Mensuales Acumulados")
        st.dataframe(df_mensual_final, use_container_width=True)
        st.bar_chart(df_mensual_final.set_index('Mes'))

    else:
        st.info("No hay datos en el historial para generar estadísticas.")
