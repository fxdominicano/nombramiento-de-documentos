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
    layout="wide"
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
    
    tabla_nombres = []
    registros_json = []
    zip_buffer = io.BytesIO()
    
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for uploaded_file in uploaded_files:
            try:
                # Leer el PDF individual
                pdf_reader = PyPDF2.PdfReader(uploaded_file)
                texto_extraido = ""
                if len(pdf_reader.pages) > 0:
                    texto_extraido = pdf_reader.pages[0].extract_text() or ""
                
                # ====================================================================
                # LÓGICA DE EXTRACCIÓN SEGÚN TUS INSTRUCCIONES ORIGINALES
                # ====================================================================
                
                # A. Extraer Nombre del Asegurado (Primera línea válida de texto)
                lineas = [l.strip() for l in texto_extraido.split('\n') if l.strip()]
                asegurado = "ASEGURADO_DESCONOCIDO"
                for linea in lineas:
                    # Filtramos líneas que contengan números largos (IDs/NCF) o palabras del sistema
                    if not re.search(r'\d{5,}', linea) and len(linea) > 4 and "PAGE" not in linea.upper():
                        asegurado = linea
                        break
                
                # B. Determinar Tipo de Documento
                tipo_doc = "RENOVACION"
                if "FACTURA" in texto_extraido.upper():
                    tipo_doc = "FACTURA"
                
                # C. Extraer Número de Póliza (Formatos comunes: AU-223124, AUXS-29576 o 01-123201)
                coincidencia_poliza = re.search(r'\b([A-Z]{1,5}-\d+|\d+-\d+)\b', texto_extraido)
                poliza = coincidencia_poliza.group(0) if coincidencia_poliza else "S-N"
                
                # D. Extraer Vigencia (Busca las dos primeras fechas en formato DD/MM/AAAA)
                fechas = re.findall(r'\b\d{2}/\d{2}/\d{4}\b', texto_extraido)
                fecha_inicio = "INICIO"
                fecha_fin = "FIN"
                if len(fechas) >= 2:
                    # Cambiamos las "/" por "-" para que el sistema operativo no falle al guardar
                    fecha_inicio = fechas[0].replace("/", "-")
                    fecha_fin = fechas[1].replace("/", "-")
                
                # E. ESTRUCTURA DE NOMBRE FINAL SOLICITADA:
                # Nombre del asegurado, tipo de documento, póliza y vigencia
                nuevo_nombre_pdf = f"{asegurado}, {tipo_doc} {poliza}, VIGENCIA {fecha_inicio} AL {fecha_fin}.pdf"
                # ====================================================================
                
                # Guardar el archivo en el ZIP en memoria
                uploaded_file.seek(0)
                zip_file.writestr(nuevo_nombre_pdf, uploaded_file.read())
                
                # Añadir a la tabla visual que verás en Streamlit
                tabla_nombres.append({
                    "Nombre Original del Archivo": uploaded_file.name,
                    "NUEVO NOMBRE SUGERIDO (REGLA ACORDADA)": nuevo_nombre_pdf
                })
                
                # Añadir al registro JSON (Manteniendo tus llaves intactas)
                registros_json.append({
                    "Archivo": nuevo_nombre_pdf,
                    "Ramo": "Vehículos" if "VEHÍCULO" in texto_extraido.upper() else "Generales",
                    "Detalle_Objeto": f"Póliza correspondiente a {asegurado}", 
                    "Sub_Modelo": "N/A",                                      
                    "Suma_Asegurada_RD": 0.00,                                 
                    "Estatus": "Correcto",
                    "Nota_Auditoria": "Procesado correctamente con el formato estándar.",
                    "Fecha_Analisis": fecha_actual
                })
                
            except PyPDF2.errors.PdfReadError:
                st.error(f"❌ Error al leer el archivo {uploaded_file.name} (posiblemente dañado).")
            except Exception as e:
                st.error(f"❌ Error inesperado con {uploaded_file.name}: {e}")

    # 2. Guardar todo el lote en el archivo JSON
    if registros_json:
        historial_completo = guardar_lote_en_json(registros_json)
        st.success(f"✅ Se han procesado y guardado {len(registros_json)} archivos en el registro JSON local.")
        
        # 3. Mostrar la correspondencia clara de nombres al usuario
        st.write("### 📋 Tabla de Correspondencia de Archivos")
        st.dataframe(tabla_nombres, use_container_width=True)
        
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
