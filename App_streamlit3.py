import streamlit as st
import time 
import pandas as pd
from datetime import date
# Importa la lógica y constantes del módulo vecino
from Routing_logic3 import COORDENADAS_LOTES, solve_route_optimization, VEHICLES, COORDENADAS_ORIGEN 

# =============================================================================
# FUNCIONES AUXILIARES DE ENLACES (Agregadas)
# =============================================================================

def generate_gmaps_link(stops_order):
    """
    Genera un enlace de Google Maps para una ruta con múltiples paradas.
    La ruta comienza en el origen (Ingenio) y regresa a él.
    """
    if not stops_order:
        return '#'

    # COORDENADAS_ORIGEN es (lon, lat). GMaps requiere lat,lon.
    lon_orig, lat_orig = COORDENADAS_ORIGEN
    
    # Construcción de la ruta: Origen / Puntos Intermedios / Destino Final
    route_parts = [f"{lat_orig},{lon_orig}"] # Origen
    
    # Añadir paradas intermedias
    for stop_lote in stops_order:
        if stop_lote in COORDENADAS_LOTES:
            lon, lat = COORDENADAS_LOTES[stop_lote]
            route_parts.append(f"{lat},{lon}") # lat,lon

    # Añadir destino final (regreso al origen)
    route_parts.append(f"{lat_orig},{lon_orig}")

    # Une las partes con '/' para la URL de Google Maps
    return "https://www.google.com/maps/dir/" + "/".join(route_parts)

# =============================================================================
# CONFIGURACIÓN INICIAL Y ESTILO
# =============================================================================

# Título de la pestaña del navegador y layout
st.set_page_config(page_title="Optimizador de Rutas - Seaboard", layout="wide")

# Ocultar menú de Streamlit y footer
st.markdown("""
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)

# Inicializar el estado de la sesión para guardar el historial
if 'historial_rutas' not in st.session_state:
    st.session_state.historial_rutas = []

if 'results' not in st.session_state:
    st.session_state.results = None

# =============================================================================
# ESTRUCTURA DEL MENÚ LATERAL
# =============================================================================

st.sidebar.title("Menú Principal")
page = st.sidebar.radio(
    "Seleccione una opción:",
    ["Calcular Nueva Ruta", "Historial", "Estadísticas"]
)
st.sidebar.divider()
st.sidebar.info(f"Rutas Guardadas: {len(st.session_state.historial_rutas)}")

# =============================================================================
# 1. PÁGINA: CALCULAR NUEVA RUTA (PÁGINA PRINCIPAL Y REPORTE UNIFICADO)
# =============================================================================

if page == "Calcular Nueva Ruta":
    st.title("🚚 Optimizator📍")
    st.caption("Planificación y división óptima de lotes para vehículos de entrega.")

    # --- ENTRADA Y VALIDACIÓN ---
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
            # Visualización del mapa
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
    
    # Este botón inicia el cálculo
    if st.button("Calcular Rutas Óptimas", key="calc_btn_main", type="primary", disabled=calculate_disabled):
        
        # Inicialización de resultados en la sesión
        if 'results' not in st.session_state:
            st.session_state.results = None

        with st.spinner('Realizando cálculo óptimo y agrupando rutas'):
            try:
                # LLAMADA A LA LÓGICA DE RUTEADO
                results = solve_route_optimization(all_stops_to_visit) 
                
                if "error" in results:
                    st.session_state.results = None
                    st.error(f"❌ Error en la API de Ruteo: {results['error']}")
                else:
                    # --- GENERACIÓN DE ENLACES ---
                    
                    # 1. Google Maps (Ruta Completa, Origen Ingenio)
                    results['ruta_a']['gmaps_link'] = generate_gmaps_link(results['ruta_a']['orden_optimo'])
                    results['ruta_b']['gmaps_link'] = generate_gmaps_link(results['ruta_b']['orden_optimo'])
                    
                    # 2. Gaia GPS (Usa el mismo URL que GeoJSON/geojson_link)
                    # NOTA: Se asume que 'geojson_link' es devuelto por solve_route_optimization.
                    results['ruta_a']['gaia_link'] = results['ruta_a'].get('geojson_link', '#') 
                    results['ruta_b']['gaia_link'] = results['ruta_b'].get('geojson_link', '#') 

                    # GUARDAR EN EL HISTORIAL
                    new_route = {
                        "fecha": date.today().strftime("%Y-%m-%d"),
                        "lotes_ingresados": ", ".join(all_stops_to_visit),
                        "lotes_a": results['ruta_a']['lotes_asignados'],
                        "lotes_b": results['ruta_b']['lotes_asignados'],
                        "km_a": results['ruta_a']['distancia_km'],
                        "km_b": results['ruta_b']['distancia_km'],
                    }
                    st.session_state.historial_rutas.append(new_route)
                    
                    st.session_state.results = results
                    st.success("✅ Cálculo finalizado y rutas optimizadas.")
                    
            except Exception as e:
                st.session_state.results = None
                st.error(f"❌ Ocurrió un error inesperado durante el ruteo: {e}")
                
    # -------------------------------------------------------------------------
    # 2. REPORTE DE RESULTADOS UNIFICADO (Aparece aquí, debajo del botón)
    # -------------------------------------------------------------------------
    
    # Solo mostramos el reporte si hay resultados guardados en la sesión
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
                
                st.markdown("---")
                # OPCIÓN 1: Google Maps (Navegación por voz, respeta origen)
                st.link_button("🗺️ Ruta en Google Maps (Multi-Parada)", res_a.get('gmaps_link', '#'))
                
                # OPCIÓN 2: GeoJSON (Referencia y descarga)
                st.link_button("🌐 Ver GeoJSON de Ruta A", res_a.get('geojson_link', '#'))
                
                # OPCIÓN 3: Gaia GPS (Importación de la ruta exacta)
                st.link_button("🌲 Ruta en Gaia GPS (Importar GeoJSON)", res_a.get('gaia_link', '#'))
            
        with col_b:
            st.subheader(f"🚚 Camión 2: {res_b.get('patente', 'N/A')}")
            with st.container(border=True):
                st.markdown(f"**Total Lotes:** {len(res_b.get('lotes_asignados', []))}")
                st.markdown(f"**Distancia Total (TSP):** **{res_b.get('distancia_km', 'N/A')} km**")
                st.markdown(f"**Lotes Asignados:** `{' → '.join(res_b.get('lotes_asignados', []))}`")
                st.info(f"**Orden Óptimo:** Ingenio → {' → '.join(res_b.get('orden_optimo', []))} → Ingenio")
                
                st.markdown("---")
                # OPCIÓN 1: Google Maps (Navegación por voz, respeta origen)
                st.link_button("🗺️ Ruta en Google Maps (Multi-Parada)", res_b.get('gmaps_link', '#'))
                
                # OPCIÓN 2: GeoJSON (Referencia y descarga)
                st.link_button("🌐 Ver GeoJSON de Ruta B", res_b.get('geojson_link', '#'))
                
                # OPCIÓN 3: Gaia GPS (Importación de la ruta exacta)
                st.link_button("🌲 Ruta en Gaia GPS (Importar GeoJSON)", res_b.get('gaia_link', '#'))

    # Si no hay resultados y la página carga por primera vez
    else:
        st.info("El reporte aparecerá aquí después de un cálculo exitoso.")


# =============================================================================
# 2. PÁGINA: HISTORIAL
# =============================================================================

elif page == "Historial":
    st.header("📋 Historial de Rutas Calculadas")
    
    if st.session_state.historial_rutas:
        df_historial = pd.DataFrame(st.session_state.historial_rutas)
        st.subheader(f"Total de {len(df_historial)} Rutas Guardadas")
        
        st.dataframe(df_historial, 
                     use_container_width=True,
                     column_order=("fecha", "km_a", "km_b", "lotes_a", "lotes_b"),
                     column_config={
                         "km_a": st.column_config.NumberColumn("KM Camión A", format="%.2f km"),
                         "km_b": st.column_config.NumberColumn("KM Camión B", format="%.2f km"),
                         "lotes_a": "Lotes Camión A",
                         "lotes_b": "Lotes Camión B",
                         "fecha": "Fecha"
                     })
        
        st.divider()
        if st.button("🗑️ Borrar Historial"):
            st.session_state.historial_rutas = []
            st.rerun()
            

    else:
        st.info("Aún no hay rutas guardadas en el historial. Realice un cálculo en la página principal.")

# =============================================================================
# 3. PÁGINA: ESTADÍSTICAS
# =============================================================================

elif page == "Estadísticas":
    st.header("📈 Estadísticas de Kilometraje")
    
    if st.session_state.historial_rutas:
        df = pd.DataFrame(st.session_state.historial_rutas)
        df['fecha'] = pd.to_datetime(df['fecha'])

        # CÁLCULOS
        df_diario = df.groupby(df['fecha'].dt.date)[['km_a', 'km_b']].sum().reset_index()
        df_diario.columns = ['Fecha', 'KM Camión A', 'KM Camión B']
        
        df['mes_año'] = df['fecha'].dt.to_period('M')
        df_mensual = df.groupby('mes_año')[['km_a', 'km_b']].sum().reset_index()
        df_mensual['Mes'] = df_mensual['mes_año'].astype(str)
        
        df_mensual_final = df_mensual[['Mes', 'km_a', 'km_b']].rename(columns={'km_a': 'KM Camión A', 'km_b': 'KM Camión B'})


        st.subheader("Kilómetros Recorridos por Día")
        st.dataframe(df_diario, use_container_width=True)
        st.bar_chart(df_diario.set_index('Fecha'))

        st.subheader("Kilómetros Mensuales Acumulados")
        st.dataframe(df_mensual_final, use_container_width=True)
        st.bar_chart(df_mensual_final.set_index('Mes'))

    else:
        st.info("No hay datos en el historial para generar estadísticas.")
