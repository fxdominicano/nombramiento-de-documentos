import streamlit as st
import PyPDF2
import io
import os
import json
import re
from datetime import datetime

# Configuración de la ventana de la aplicación
st.set_page_config(
    page_title="Nombramiento y Registro de Documentos", 
    page_icon="📄", 
    layout="centered"
)

st.title("Procesamiento y Registro de Documentos 📄")
fecha_actual = datetime.now().strftime('%d/%m/%Y')
st.write(f"**Fecha de análisis:** {fecha_actual}")

# Nombre del archivo JSON local donde se guarda lo procesado
JSON_FILE = "job_procesados.json"

# Función segura para guardar/apendizar datos en el archivo JSON
def guardar_en_json(nuevo_registro, filepath=JSON_FILE):
    historial = []
    # 1. Si el archivo ya existe, leemos los registros previos
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                historial = json.load(f)
        except Exception:
            # Si el archivo está vacío o corrupto, empezamos una lista nueva
            historial = []
            
    # 2. Añadimos el nuevo registro al historial
    historial.append(nuevo_registro)
    
    # 3. Reescribimos el archivo JSON de forma limpia
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(historial, f, ensure_ascii=False, indent=4)
    return historial

---

# 1. Selector de archivos PDF
uploaded_file = st.file_uploader("Selecciona un archivo PDF para procesar", type=["pdf"])

if uploaded_file is not None:
    st.info("Analizando documento...")
    
    try:
        # 2. Leer el PDF de forma segura con PyPDF2
        pdf_reader = PyPDF2.PdfReader(uploaded_file)
        primera_pagina = pdf_reader.pages[0]
        texto_extraido = primera_pagina.extract_text() or ""
        
        # --- TU LÓGICA DE EXTRACCIÓN (Mantén aquí tus reglas o IA de extracción) ---
        # Nota: Aquí van tus expresiones regulares actuales para extraer el ramo, sumas, etc.
        # Colocamos valores de simulación basados en tu estructura estándar:
        
        nombre_original = uploaded_file.name
        ramo_detectado = "Vehículos" if "VEHÍCULO" in texto_extraido.upper() else "Incendio"
        estatus_proceso = "Correcto"
        nota_auditoria = "Documento procesado y validado exitosamente en el sistema local."
        
        # Intentar detectar un ID o número de póliza (ej. de 6 a 12 dígitos)
        coincidencia_poliza = re.search(r'\b\d{6,12}\b', texto_extraido)
        poliza_id = coincidencia_poliza.group(0) if coincidencia_poliza else "DOC"
        
        # Generar el nuevo nombre del archivo para la descarga
        nuevo_nombre_pdf = f"{poliza_id}_{nombre_original.replace('.pdf', '')}_Listo.pdf"
        # ----------------------------------------------------------------------------

        # 3. CREACIÓN DEL OBJETO JSON (Manteniendo tus llaves intactas)
        datos_procesados = {
            "Archivo": nuevo_nombre_pdf,
            "Ramo": ramo_detectado,
            "Detalle_Objeto": "Simulación de objeto extraído del PDF", # Reemplazar por tu variable
            "Sub_Modelo": "N/A",                                      # Reemplazar por tu variable
            "Suma_Asegurada_RD": 0.00,                                 # Reemplazar por tu variable
            "Estatus": estatus_proceso,
            "Nota_Auditoria": nota_auditoria,
            "Fecha_Analisis": fecha_actual
        }

        # 4. Guardar automáticamente en el archivo JSON local
        historial_actualizado = guardar_en_json(datos_procesados)
        st.success("✅ ¡Datos extraídos y guardados en el registro JSON correctamente!")

        # 5. Mostrar al usuario los resultados en pantalla
        st.write("### Vista previa del registro guardado:")
        st.json(datos_procesados)

        # Volver a poner el puntero del PDF al principio para la descarga
        uploaded_file.seek(0)

        # 6. Botones de acción/descarga para el usuario
        col1, col2 = st.columns(2)
        
        with col1:
            st.download_button(
                label="📥 Descargar PDF Renombrado",
                data=uploaded_file,
                file_name=nuevo_nombre_pdf,
                mime="application/pdf",
                use_container_width=True
            )
            
        with col2:
            # Opción para descargar también el archivo JSON completo acumulado si estás en la nube
            json_string = json.dumps(historial_actualizado, ensure_ascii=False, indent=4)
            st.download_button(
                label="📥 Descargar Historial JSON",
                data=json_string,
                file_name=f"job_{datetime.now().strftime('%Y_%m')}.json",
                mime="application/json",
                use_container_width=True
            )

    except PyPDF2.errors.PdfReadError:
        st.error("❌ Error: El archivo PDF parece estar encriptado o dañado.")
    except Exception as e:
        st.error(f"❌ Ocurrió un error inesperado: {e}")

else:
    st.info("Esperando archivo PDF para iniciar el análisis y actualización del JSON.")
