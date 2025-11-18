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

        # --- AÑADIR LA COLUMNA DE KM TOTALES (Km_CamionA + Km_CamionB) ---
        # Aseguramos que las columnas sean numéricas antes de la suma
        try:
            df_historial['Km_CamionA'] = pd.to_numeric(df_historial['Km_CamionA'], errors='coerce').fillna(0)
            df_historial['Km_CamionB'] = pd.to_numeric(df_historial['Km_CamionB'], errors='coerce').fillna(0)
            df_historial['Km_Totales'] = df_historial['Km_CamionA'] + df_historial['Km_CamionB']
        except Exception as e:
            st.warning(f"⚠️ No se pudo calcular Km Totales. Verifique que Km_CamionA y Km_CamionB sean números en la hoja de cálculo: {e}")
            df_historial['Km_Totales'] = 0 # Asignar 0 si falla

        # Muestra el DF, usando los nombres amigables
        st.dataframe(df_historial,
                      use_container_width=True,
                      column_order=[ # Definimos el orden de las columnas a mostrar
                          "Fecha", 
                          "Hora", 
                          "LotesIngresados", 
                          "Lotes_CamionA", 
                          "Lotes_CamionB", 
                          "Km_CamionA", 
                          "Km_CamionB", 
                          "Km_Totales" # << ¡NUEVA COLUMNA AQUÍ!
                      ],
                      column_config={
                          "Km_CamionA": st.column_config.NumberColumn("KM Camión A", format="%.2f km"),
                          "Km_CamionB": st.column_config.NumberColumn("KM Camión B", format="%.2f km"),
                          "Km_Totales": st.column_config.NumberColumn("**KM Totales**", format="%.2f km", help="Suma de KM Camión A + KM Camión B"), # ¡CONFIGURACIÓN DE LA NUEVA COLUMNA!
                          "Lotes_CamionA": "Lotes Camión A",
                          "Lotes_CamionB": "Lotes Camión B",
                          "Fecha": "Fecha",
                          "Hora": "Hora de Carga", # Nombre visible en Streamlit
                          "LotesIngresados": "Lotes Ingresados"
                      })

    else:
        st.info("No hay rutas guardadas. Realice un cálculo en la página principal.")
