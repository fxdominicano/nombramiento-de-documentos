import streamlit as st
import os
import re
import io
import zipfile
import google.generativeai as genai
from pydantic import BaseModel

st.set_page_config(page_title="Extractor de Seguros IA", page_icon="🌐", layout="centered")

st.title("🌐 Extractor de Seguros Inteligente")
st.markdown("Sube tus archivos de pólizas o notas de crédito. Gemini 3.5 Flash los procesará uno a uno.")

# PROTECCIÓN DE SEGURIDAD EN LA NUBE
if "GEMINI_API_KEY" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.error("🔑 Falta configurar la API Key en los secretos de Streamlit.")
    st.stop()

# Inicializar la memoria interna de Streamlit para guardar archivos sobre la marcha
if "archivos_listos" not in st.session_state:
    st.session_state.archivos_listos = {}

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
    limpia = re.sub(r'[-/\s]', '.', fecha_str)
    partes = limpia.split('.')
    if len(partes) == 3:
        dia = partes[0].strip().zfill(2)
        mes = partes[1].strip().zfill(2)
        anio = partes[2].strip()
        
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
    
    # Botón para limpiar la memoria anterior si subes un lote nuevo
    if st.button("🔄 Limpiar progreso anterior"):
        st.session_state.archivos_listos = {}
        st.rerun()
    
    if st.button("🚀 Iniciar Procesamiento con IA", type="primary"):
        progreso = st.progress(0)
        status_text = st.empty()
        
        for index, archivo in enumerate(archivos_cargados):
            nombre_original = archivo.name
            extension = os.path.splitext(nombre_original)[1].lower()
            
            # Si el archivo ya se procesó con éxito en este lote, lo saltamos
            if nombre_original in st.session_state.archivos_listos:
                progreso.progress((index + 1) / len(archivos_cargados))
                continue
                
            status_text.text(f"Analizando documento ({index + 1}/{len(archivos_cargados)}): {nombre_original}")
            
            try:
                archivo_bytes = archivo.read()
                mime_type = "application/pdf" if extension == ".pdf" else "image/jpeg"
                prompt = "Extrae del documento el nombre y apellido del asegurado, tipo de documento, número de póliza exacto y vigencias (con año de 4 dígitos)."
                
                # SE AGREGA TIMEOUT: Si tarda más de 30 segundos, pasa al siguiente archivo
                respuesta = model.generate_content(
                    [{"mime_type": mime_type, "data": archivo_bytes}, prompt],
                    request_options={"timeout": 30.0}
                )
                
                datos = EsquemaPoliza.model_validate_json(respuesta.text)
                
                if "especificado" in datos.nombres.lower() or "especificado" in datos.numero_poliza.lower():
                    nuevo_nombre = f"[MANUAL] - {nombre_original}"
                else:
                    f_inicio = corregir_formato_fecha(datos.fecha_inicio)
                    f_fin = corregir_formato_fecha(datos.fecha_fin)
                    tipo_doc = datos.tipo_documento.lower().strip()
                    nuevo_nombre = f"{datos.nombres.strip()} {datos.apellidos.strip()} - {tipo_doc} - {datos.numero_poliza.strip()} - vigencia {f_inicio} al {f_fin}{extension}"
                
                # GUARDADO INMEDIATO: Se guarda directamente en el estado global
                st.session_state.archivos_listos[nuevo_nombre] = archivo_bytes
                st.success(f"✅ Procesado: {nuevo_nombre}")
                
            except Exception as e:
                # Si falla o da timeout, mostramos el error pero NO detenemos el programa
                st.error(f"⚠️ El archivo '{nombre_original}' dio un problema o tardó demasiado. Saltando... (Error: {str(e)})")
                # Lo guardamos marcado como error para que puedas descargarlo de todos modos si deseas
                st.session_state.archivos_listos[f"[PROBLEMA_IA] - {nombre_original}"] = archivo_bytes
                
            progreso.progress((index + 1) / len(archivos_cargados))
            
        status_text.text("✨ ¡Procesamiento de lote completado!")

# BOTÓN DE DESCARGA PERMANENTE: Se muestra siempre que haya al menos un archivo procesado con éxito
if st.session_state.archivos_listos:
    st.markdown("---")
    st.subheader(f"📦 Descarga tus resultados ({len(st.session_state.archivos_listos)} archivos listos)")
    
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for nombre_archivo, contenido_bytes in st.session_state.archivos_listos.items():
            zip_file.writestr(nombre_archivo, contenido_bytes)
            
    st.download_button(
        label="💾 Descargar Archivos Correctos (.ZIP)",
        data=zip_buffer.getvalue(),
        file_name="polizas_organizadas.zip",
        mime="application/zip",
        use_container_width=True
    )
