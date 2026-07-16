import streamlit as st
import PyPDF2
import io
import os
import json
import re
import zipfile
from datetime import datetime

# Configuración de la interfaz de usuario
st.set_page_config(
    page_title="Procesamiento por Lotes - Seguros", 
    page_icon="📄", 
    layout="wide"
)

st.title("Procesamiento por Lotes y Registro de Documentos 📄")
fecha_actual = datetime.now().strftime('%d/%m/%Y')
st.write(f"**Fecha de análisis:** {fecha_actual}")

# Archivo de registro permanente
JSON_FILE = "job_procesados.json"

def guardar_lote_en_json(nuevos_registros, filepath=JSON_FILE):
    historial = []
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                historial = json.load(f)
        except Exception:
            historial = []
            
    historial.extend(nuevos_registros)
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(historial, f, ensure_ascii=False, indent=4)
    return historial

# Componente de carga masiva de archivos
uploaded_files = st.file_uploader(
    "Arrastra o selecciona tus archivos PDF (Soporta múltiples archivos)", 
    type=["pdf"], 
    accept_multiple_files=True
)

if uploaded_files:
    # Crear un identificador único para el lote actual basado en nombres y tamaños
    batch_id = "-".join([f"{f.name}_{f.size}" for f in uploaded_files])
    
    # CONTROL DE ESTADO: Solo procesa y guarda si es un lote nuevo o modificado
    if "current_batch_id" not in st.session_state or st.session_state.current_batch_id != batch_id:
        st.session_state.current_batch_id = batch_id
        st.session_state.tabla_nombres = []
        st.session_state.zip_data = None
        st.session_state.historial_completo = []
        
        registros_json = []
        tabla_nombres = []
        zip_buffer = io.BytesIO()
        
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for uploaded_file in uploaded_files:
                try:
                    # Lectura segura del flujo del PDF
                    pdf_reader = PyPDF2.PdfReader(uploaded_file)
                    texto_extraido = ""
                    if len(pdf_reader.pages) > 0:
                        texto_extraido = pdf_reader.pages[0].extract_text() or ""
                    
                    # ----------------------------------------------------------------
                    # BLOQUE DE EXTRACCIÓN DE DATOS
                    # (Si tenías funciones específicas en tu código original, 
                    # puedes integrarlas directamente en estas variables)
                    # ----------------------------------------------------------------
                    
                    # A. Extraer Nombre del Asegurado
                    lineas = [l.strip() for l in texto_extraido.split('\n') if l.strip()]
                    asegurado = "ASEGURADO_DESCONOCIDO"
                    for linea in lineas:
                        if len(linea) > 4 and not any(p in linea.upper() for p in ["FACTURA", "RENOVAC", "PÓLIZA", "POLIZA", "RNC", "TEL"]):
                            asegurado = linea
                            break
                    
                    # B. Determinar Tipo de Documento
                    tipo_doc = "RENOVACION" if "RENOVAC" in texto_extraido.upper() else "FACTURA"
                    
                    # C. Extraer Número de Póliza
                    coincidencia_poliza = re.search(r'\b([A-Z]{1,5}-\d+|\d+-\d+|\b\d{7,15}\b)\b', texto_extraido)
                    poliza = coincidencia_poliza.group(0) if coincidencia_poliza else "S-N"
                    
                    # D. Extraer Fechas de Vigencia (Convierte barras '/' a guiones '-' para compatibilidad)
                    fechas = re.findall(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b', texto_extraido)
                    fecha_inicio = fechas[0].replace("/", "-") if len(fechas) >= 1 else "INICIO"
                    fecha_fin = fechas[1].replace("/", "-") if len(fechas) >= 2 else "FIN"
                    
                    # ----------------------------------------------------------------
                    # CONSTRUCCIÓN DEL FORMATO SOLICITADO
                    # ----------------------------------------------------------------
                    nuevo_nombre_pdf = f"{asegurado}, {tipo_doc} {poliza}, VIGENCIA {fecha_inicio} AL {fecha_fin}.pdf"
                    
                    # Empaquetado en el archivo comprimido
                    uploaded_file.seek(0)
                    zip_file.writestr(nuevo_nombre_pdf, uploaded_file.read())
                    
                    # Registro para la interfaz y persistencia
                    tabla_nombres.append({
                        "Archivo Original del Archivo": uploaded_file.name,
                        "NUEVO NOMBRE SUGERIDO (REGLA ACORDADA)": nuevo_nombre_pdf
                    })
                    
                    registros_json.append({
                        "Archivo": nuevo_nombre_pdf,
                        "Ramo": "Vehículos" if "VEHÍCULO" in texto_extraido.upper() else "Generales",
                        "Detalle_Objeto": f"Póliza correspondiente a {asegurado}", 
                        "Sub_Modelo": "N/A",                                      
                        "Suma_Asegurada_RD": 0.00,                                 
                        "Estatus": "Correcto",
                        "Nota_Auditoria": "Procesado correctamente por lotes.",
                        "Fecha_Analisis": fecha_actual
                    })
                    
                except Exception as e:
                    st.error(f"❌ Error crítico procesando {uploaded_file.name}: {e}")
        
        # Escritura física y almacenamiento en caché de la sesión (ejecución única)
        if registros_json:
            st.session_state.historial_completo = guardar_lote_en_json(registros_json)
            st.session_state.tabla_nombres = tabla_nombres
            zip_buffer.seek(0)
            st.session_state.zip_data = zip_buffer.getvalue()
            st.success(f"✅ Se han procesado y registrado {len(registros_json)} archivos en el JSON local con éxito.")

    # RENDERIZADO VISUAL: Muestra la información almacenada de forma segura
    if st.session_state.tabla_nombres:
        st.write("### 📋 Tabla de Correspondencia de Archivos")
        st.dataframe(st.session_state.tabla_nombres, use_container_width=True)
        
        st.write("### 📥 Descargar Resultados del Lote")
        col1, col2 = st.columns(2)
        
        with col1:
            st.download_button(
                label="📥 Descargar todos los PDFs renombrados (ZIP)",
                data=st.session_state.zip_data,
                file_name=f"lote_procesado_{datetime.now().strftime('%d_%m_%Y')}.zip",
                mime="application/zip",
                use_container_width=True
            )
            
        with col2:
            json_string = json.dumps(st.session_state.historial_completo, ensure_ascii=False, indent=4)
            st.download_button(
                label="📥 Descargar Historial JSON Completo",
                data=json_string,
                file_name=f"job_{datetime.now().strftime('%Y_%m')}.json",
                mime="application/json",
                use_container_width=True
            )
else:
    st.info("Esperando que selecciones uno o varios archivos PDF para procesar por lotes.")
