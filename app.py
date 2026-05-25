import streamlit as st
import os
import re
import io
import zipfile
import json
import hashlib
from datetime import datetime
import google.generativeai as genai
from pydantic import BaseModel

# Librerías oficiales para conectar con Google Drive
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaInMemoryUpload

st.set_page_config(page_title="Extractor de Seguros IA", page_icon="🌐", layout="centered")

st.title("🌐 Extractor de Seguros - Nube Auto-Sincronizada")
st.markdown("Procesamiento de pólizas con historial guardado automáticamente en tu Google Drive.")

# 1. CONFIGURACIÓN DE SEGURIDAD (GEMINI Y GOOGLE DRIVE)
if "GEMINI_API_KEY" in st.secrets and "GOOGLE_SERVICE_ACCOUNT" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.error("🔑 Falta configurar las llaves y credenciales en los secretos de Streamlit.")
    st.stop()

# Inicializar memoria de la sesión actual
if "archivos_listos" not in st.session_state:
    st.session_state.archivos_listos = {}

NOMBRE_ARCHIVO_DRIVE = "registro_procesados.json"

# Conectar de forma segura con la API de Google Drive
def obtener_servicio_drive():
    info_claves = dict(st.secrets["GOOGLE_SERVICE_ACCOUNT"])
    credenciales = service_account.Credentials.from_service_account_info(
        info_claves, 
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build('drive', 'v3', credentials=credenciales)

# DESCARGA AUTOMÁTICA DESDE TU DRIVE
def cargar_log_desde_drive():
    try:
        drive_service = obtener_servicio_drive()
        resultado = drive_service.files().list(
            q=f"name='{NOMBRE_ARCHIVO_DRIVE}' and trashed=false",
            fields="files(id)",
            pageSize=1
        ).execute()
        
        archivos = resultado.get('files', [])
        if not archivos:
            return {}, None
            
        file_id = archivos[0]['id']
        peticion = drive_service.files().get_media(fileId=file_id)
        fh = io.BytesIO()
        descargador = MediaIoBaseDownload(fh, peticion)
        done = False
        while not done:
            _, done = descargador.next_chunk()
            
        fh.seek(0)
        return json.loads(fh.read().decode('utf-8')), file_id
    except Exception as e:
        st.warning(f"⚠️ No se pudo descargar el historial de Google Drive (Se iniciará uno nuevo): {e}")
        return {}, None

# SUBIDA AUTOMÁTICA A TU DRIVE
def guardar_log_en_drive(log_actualizado, file_id):
    try:
        drive_service = obtener_servicio_drive()
        contenido_json = json.dumps(log_actualizado, ensure_ascii=False, indent=4).encode('utf-8')
        media = MediaInMemoryUpload(contenido_json, mimetype='application/json', resumable=True)
        
        if file_id:
            drive_service.files().update(fileId=file_id, media_body=media).execute()
        else:
            meta_archivo = {'name': NOMBRE_ARCHIVO_DRIVE}
            drive_service.files().create(body=meta_archivo, media_body=media, fields='id').execute()
    except Exception as e:
        st.error(f"❌ Error al salvar el historial en Google Drive: {e}")

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

archivos_cargados = st.file_uploader(
    "Arrastra aquí los PDFs o imágenes de los clientes:", 
    type=["pdf", "jpg", "jpeg", "png"], 
    accept_multiple_files=True
)

if archivos_cargados:
    st.subheader(f"📋 Archivos listos para procesar: {len(archivos_cargados)}")
    
    if st.button("🔄 Limpiar progreso de esta tanda"):
        st.session_state.archivos_listos = {}
        st.rerun()
        
    if st.button("🚀 Iniciar Procesamiento con IA", type="primary"):
        progreso = st.progress(0)
        status_text = st.empty()
        
        status_text.text("🔄 Sincronizando historial con tu Google Drive...")
        log_historico, file_id = cargar_log_desde_drive()
        
        for index, archivo in enumerate(archivos_cargados):
            nombre_original = archivo.name
            extension = os.path.splitext(nombre_original)[1].lower()
            
            try:
                archivo_bytes = archivo.read()
                hash_archivo = hashlib.sha256(archivo_bytes).hexdigest()
                
                if hash_archivo in log_historico:
                    registro = log_historico[hash_archivo]
                    st.info(f"ℹ️ El documento '{nombre_original}' ya fue revisado el {registro['fecha_revision']}. Omitiendo IA.")
                    st.session_state.archivos_listos[registro['nombre_nuevo']] = archivo_bytes
                    progreso.progress((index + 1) / len(archivos_cargados))
                    continue
                
                if nombre_original in st.session_state.archivos_listos:
                    progreso.progress((index + 1) / len(archivos_cargados))
                    continue
                    
                status_text.text(f"Analizando documento ({index + 1}/{len(archivos_cargados)}): {nombre_original}")
                
                mime_type = "application/pdf" if extension == ".pdf" else "image/jpeg"
                prompt = "Extrae del documento el nombre y apellido del asegurado, tipo de documento, número de póliza exacto y vigencias (con año de 4 dígitos)."
                
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
                    
                    # 🛠️ CORRECCIÓN PARA EMPRESAS: Si detecta 'rnc', lo cambia limpiamente por 'póliza'
                    if "rnc" in tipo_doc:
                        tipo_doc = "póliza"
                    
                    nuevo_nombre = f"{datos.nombres.strip()} {datos.apellidos.strip()} - {tipo_doc} - {datos.numero_poliza.strip()} - vigencia {f_inicio} al {f_fin}{extension}"
                
                st.session_state.archivos_listos[nuevo_nombre] = archivo_bytes
                
                fecha_hoy = datetime.now().strftime("%d/%m/%Y")
                log_historico[hash_archivo] = {
                    "nombre_original": nombre_original,
                    "nombre_nuevo": nuevo_nombre,
                    "fecha_revision": fecha_hoy
                }
                
                guardar_log_en_drive(log_historico, file_id)
                if not file_id:
                    log_historico, file_id = cargar_log_desde_drive()
                    
                st.success(f"✅ Procesado y salvado en Drive: {nuevo_nombre}")
                
            except Exception as e:
                st.error(f"❌ Error en {nombre_original}: {str(e)}")
                st.session_state.archivos_listos[f"[ERROR] - {nombre_original}"] = archivo_bytes
                
            progreso.progress((index + 1) / len(archivos_cargados))
            
        status_text.text("✨ ¡Lote completado e historial sincronizado por completo!")

if st.session_state.archivos_listos:
    st.markdown("---")
    st.subheader(f"📦 Resultados listos ({len(st.session_state.archivos_listos)} archivos)")
    
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for nombre_archivo, contenido_bytes in st.session_state.archivos_listos.items():
            zip_file.writestr(nombre_archivo, contenido_bytes)
            
    st.download_button(
        label="💾 Descargar Archivos Organizados (.ZIP)",
        data=zip_buffer.getvalue(),
        file_name="polizas_organizadas.zip",
        mime="application/zip",
        use_container_width=True
    )
