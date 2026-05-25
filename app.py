import streamlit as st
import os
import re
import io
import zipfile
import google.generativeai as genai
from pydantic import BaseModel

st.set_page_config(page_title="Extractor de Seguros IA", page_icon="🌐", layout="centered")

st.title("🌐 Extractor de Seguros - Canal Local PC")
st.markdown("Sube tus archivos de pólizas o notas de crédito. Gemini 3.5 Flash los procesará en ráfaga.")

# API KEY DIRECTA PARA TU PC LOCAL
GEMINI_API_KEY = "AIzaSyCoL-tqTTNVsnRqhARa9WMbwv1WDVq5ICM"
genai.configure(api_key=GEMINI_API_KEY)

class EsquemaPoliza(BaseModel):
    nombres: str
    apellidos: str
    tipo_documento: str
    numero_poliza: str
    fecha_inicio: str
    fecha_fin: str

model = genai.GenerativeModel(
    model_name="gemini-3.5-flash",
    generation_config={
        "response_mime_type": "application/json",
        "response_schema": EsquemaPoliza,
        "temperature": 0.1
    }
)

def corregir_formato_fecha(fecha_str):
    if not fecha_str or "especificado" in fecha_str.lower():
        return fecha_str
    # Reemplazar separadores comunes por puntos
    limpia = re.sub(r'[-/\s]', '.', fecha_str)
    partes = limpia.split('.')
    if len(partes) == 3:
        dia = partes[0].strip().zfill(2)
        mes = partes[1].strip().zfill(2)
        anio = partes[2].strip()
        
        # AJUSTE: Si el año viene de 2 dígitos (ej: "26"), lo convertimos a 4 dígitos ("2026")
        if len(anio) == 2:
            anio = "20" + anio
        return f"{dia}.{mes}.{anio}"
    return fecha_str

# Zona de arrastrar y soltar archivos en el navegador
archivos_cargados = st.file_uploader(
    "Arrastra aquí los PDFs o imágenes de los clientes:", 
    type=["pdf", "jpg", "jpeg", "png"], 
    accept_multiple_files=True
)

if archivos_cargados:
    st.subheader(f"📋 Archivos listos para procesar: {len(archivos_cargados)}")
    
    if st.button("🚀 Iniciar Procesamiento con IA", type="primary"):
        progreso = st.progress(0)
        status_text = st.empty()
        
        archivos_renombrados_zip = {}
        
        for index, archivo in enumerate(archivos_cargados):
            nombre_original = archivo.name
            extension = os.path.splitext(nombre_original)[1].lower()
            
            status_text.text(f"Analizando documento ({index + 1}/{len(archivos_cargados)}): {nombre_original}")
            
            try:
                archivo_bytes = archivo.read()
                mime_type = "application/pdf" if extension == ".pdf" else "image/jpeg"
                
                # Se le especifica explícitamente el formato deseado de 4 dígitos en el año
                prompt = "Extrae del documento el nombre y apellido del asegurado, tipo de documento, número de póliza exacto y vigencias (con año de 4 dígitos)."
                
                respuesta = model.generate_content([
                    {"mime_type": mime_type, "data": archivo_bytes},
                    prompt
                ])
                
                datos = EsquemaPoliza.model_validate_json(respuesta.text)
                
                if "especificado" in datos.nombres.lower() or "especificado" in datos.numero_poliza.lower():
                    nuevo_nombre = f"[MANUAL] - {nombre_original}"
                else:
                    f_inicio = corregir_formato_fecha(datos.fecha_inicio)
                    f_fin = corregir_formato_fecha(datos.fecha_fin)
                    tipo_doc = datos.tipo_documento.lower().strip()
                    
                    nuevo_nombre = f"{datos.nombres.strip()} {datos.apellidos.strip()} - {tipo_doc} - {datos.numero_poliza.strip()} - vigencia {f_inicio} al {f_fin}{extension}"
                
                archivos_renombrados_zip[nuevo_nombre] = archivo_bytes
                
            except Exception as e:
                st.error(f"❌ Error en {nombre_original}: {str(e)}")
                archivos_renombrados_zip[f"[ERROR] - {nombre_original}"] = archivo_bytes
                
            progreso.progress((index + 1) / len(archivos_cargados))
            
        status_text.text("✨ ¡Procesamiento completado con éxito!")
        
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for nombre_archivo, contenido_bytes in archivos_renombrados_zip.items():
                zip_file.writestr(nombre_archivo, contenido_bytes)
                
        st.download_button(
            label="💾 Descargar Archivos Renombrados (.ZIP)",
            data=zip_buffer.getvalue(),
            file_name="polizas_organizadas.zip",
            mime="application/zip",
            use_container_width=True
        )