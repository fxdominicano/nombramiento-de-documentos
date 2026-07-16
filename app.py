import streamlit as st
import PyPDF2
import io
import os
import json
import re
import zipfile
from datetime import datetime

# Configuración de la ventana de la aplicación
st.set_page_config(
    page_title="Procesamiento por Lotes - Seguros", 
    page_icon="📄", 
    layout="wide"  # Usamos diseño ancho para ver mejor la tabla de nombres
)

st.title("Procesamiento por Lotes y Registro de Documentos 📄")
fecha_actual = datetime.now().strftime('%d/%m/%Y')
st.write(f"**Fecha de análisis:** {fecha_actual}")

# Nombre del archivo JSON local donde se guarda lo procesado
JSON_FILE = "job_procesados.json"

# Función para guardar un lote completo de registros en el JSON
def guardar_lote_en_json(nuevos_registros, filepath=JSON_FILE):
    historial = []
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                historial = json.load(f)
        except Exception:
            historial = []
            
    # Añadimos todos los nuevos registros al historial existente
    historial.extend(nuevos_registros)
    
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(historial, f, ensure_ascii=False, indent=4)
    return historial


# 1. Selector de múltiples archivos PDF (Lotes)
uploaded_files = st.file_uploader(
    "Arrastra o selecciona tus archivos PDF (Soporta múltiples archivos)", 
    type=["pdf"], 
    accept_multiple_files=True
)

if uploaded_files:
    st.info(f"Se han cargado {len(uploaded_files)} archivos. Iniciando procesamiento...")
    
    # Preparar contenedores para el proceso
    tabla_nombres = []
    registros_json = []
    
    # Creamos un archivo ZIP en memoria para empaquetar las descargas
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for uploaded_file in uploaded_files:
            try:
                # Leer el PDF individual de forma segura
                pdf_reader = PyPDF2.PdfReader(uploaded_file)
                texto_extraido = ""
                if len(pdf_reader.pages) > 0:
                    texto_extraido = pdf_reader.pages[0].extract_text() or ""
                
                # --- LÓGICA DE EXTRACCIÓN DE METADATA ---
                nombre_original = uploaded_file.name
                nombre_sin_ext = nombre_original.replace(".pdf", "").replace(".PDF", "")
                
                # Intentar detectar ramo automáticamente
                ramo_detectado = "Vehículos" if "VEHÍCULO" in texto_extraido.upper() else "Incendio"
                
                # Intentar buscar un número de póliza/documento (ej. 6 a 12 dígitos)
                coincidencia_poliza = re.search(r'\b\d{6,12}\b', texto_extraido)
                poliza_id = coincidencia_poliza.group(0) if coincidencia_poliza else "DOC"
                
                # Generar el nuevo nombre dinámico
                nuevo_nombre_pdf = f"{poliza_id}_{nombre_sin_ext}_Listo.pdf"
                
                # --- GUARDAR ARCHIVO EN EL ZIP ---
                # Reseteamos el puntero para leer el archivo desde el inicio
                uploaded_file.seek(0)
                zip_file.writestr(nuevo_nombre_pdf, uploaded_file.read())
                
                # --- REGISTRO INDIVIDUAL PARA LA TABLA VISUAL ---
                tabla_nombres.append({
                    "Nombre Original": nombre_original,
                    "Nombre Nuevo Sugerido": nuevo_nombre_pdf,
                    "Ramo": ramo_detectado,
                    "Identificador": poliza_id
                })
                
                # --- REGISTRO INDIVIDUAL PARA EL JSON ---
                registros_json.append({
                    "Archivo": nuevo_nombre_pdf,
                    "Ramo": ramo_detectado,
                    "Detalle_Objeto": f"Análisis de {nombre_original}", 
                    "Sub_Modelo": "N/A",                                      
                    "Suma_Asegurada_RD": 0.00,                                 
                    "Estatus": "Correcto",
                    "Nota_Auditoria": "Procesado correctamente por lotes.",
                    "Fecha_Analisis": fecha_actual
                })
                
            except PyPDF2.errors.PdfReadError:
                st.error(f"❌ Error al leer el archivo {uploaded_file.name} (posiblemente dañado o encriptado).")
            except Exception as e:
                st.error(f"❌ Error inesperado con {uploaded_file.name}: {e}")

    # 2. Guardar todo el lote en el archivo JSON local de una sola vez
    if registros_json:
        historial_completo = guardar_lote_en_json(registros_json)
        st.success(f"✅ Se han procesado y guardado {len(registros_json)} archivos en el registro JSON local.")
        
        # 3. Mostrar la correspondencia de nombres al usuario
        st.write("### 📋 Tabla de Correspondencia de Archivos")
        st.dataframe(tabla_nombres, use_container_width=True)
        
        # Preparar los buffers de descarga para el final del archivo ZIP
        zip_buffer.seek(0)
        
        # 4. Botones de descarga del lote
        st.write("### 📥 Descargar Resultados del Lote")
        col1, col2 = st.columns(2)
        
        with col1:
            st.download_button(
                label="📥 Descargar todos los PDFs renombrados (ZIP)",
                data=zip_buffer,
                file_name=f"lote_procesado_{datetime.now().strftime('%d_%m_%Y')}.zip",
                mime="application/zip",
                use_container_width=True
            )
            
        with col2:
            json_string = json.dumps(historial_completo, ensure_ascii=False, indent=4)
            st.download_button(
                label="📥 Descargar Historial JSON Completo",
                data=json_string,
                file_name=f"job_{datetime.now().strftime('%Y_%m')}.json",
                mime="application/json",
                use_container_width=True
            )
else:
    st.info("Esperando que selecciones uno o varios archivos PDF para procesar por lotes.")
