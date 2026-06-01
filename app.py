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

# Configuración de la página de Streamlit
st.set_page_config(page_title="Extractor de Seguros IA", page_icon="🌐", layout="centered")

st.title("🌐 Extractor de Seguros - Nube Sincronizada")
st.markdown("Procesamiento de pólizas con historial guardado directamente en tu Google Drive.")

# 1. CONFIGURACIÓN DE SEGURIDAD Y LLAVES
if "GEMINI_API_KEY" in st.secrets and "GCP_SERVICE_ACCOUNT" in st.secrets and "DRIVE_FILE_ID" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.error("🔑 Falta configurar 'GEMINI_API_KEY', 'GCP_SERVICE_ACCOUNT' o 'DRIVE_FILE_ID' en los secretos de Streamlit.")
    st.stop()

# Inicializar memoria de la sesión actual
if "archivos_listos" not in st.session_state:
    st.session_state.archivos_listos = {}

FILE_ID = st.secrets["DRIVE_FILE_ID"]  # ID directo de tu archivo en Drive

# Conectar de forma segura manejando diccionarios nativos de Streamlit Secrets
def obtener_servicio_drive():
    secreto_gcp = st.secrets["GCP_SERVICE_ACCOUNT"]
    
    # Si viene como string lo convierte; si viene como diccionario lo clona
    if isinstance(secreto_gcp, str):
        info_claves = json.loads(secreto_gcp)
    else:
        info_claves = dict(secreto_gcp)
    
    # Asegurar saltos de línea perfectos para la librería de criptografía
    if "private_key" in info_claves:
        info_claves["private_key"] = info_claves["private_key"].replace("\\n", "\n")
        
    credenciales = service_account.Credentials.from_service_account_info(
        info_claves, 
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build('drive', 'v3', credentials=credenciales)

# LECTURA INTELIGENTE (HÍBRIDA): MANEJA TANTO ARCHIVOS BINARIOS COMO DOCUMENTOS DE GOOGLE
def cargar_log_desde_drive():
    try:
        drive_service = obtener_servicio_drive()
        
        # 1. Consultar metadatos para verificar el tipo de archivo (mimeType)
        metadatos = drive_service.files().get(fileId=FILE_ID, fields="mimeType").execute()
        mime_type = metadatos.get("mimeType", "")
        
        fh = io.BytesIO()
        
        # 2. Elegir el método de descarga correcto según el tipo de archivo
        if mime_type.startswith("application/vnd.google-apps."):
            # Es un documento nativo de Google -> Se debe EXPORTAR a texto plano
            peticion = drive_service.files().export_media(fileId=FILE_ID, mimeType="text/plain")
        else:
            # Es un archivo binario estándar (JSON, TXT, etc.) -> Se descarga con GET convencional
            peticion = drive_service.files().get_media(fileId=FILE_ID)
            
        descargador = MediaIoBaseDownload(fh, peticion)
        
        done = False
        while not done:
            _, done = descargador.next_chunk()
            
        fh.seek(0)
        contenido = fh.read().decode('utf-8').strip()
        
        # Si el documento en la nube está completamente vacío, devolvemos un diccionario base
        if not contenido:
            return {}
            
        return json.loads(contenido)
    except Exception as e:
        st.error(f"❌ Error crítico al leer el historial en Google Drive: {e}")
        return {}

# ACTUALIZACIÓN DIRECTA DEL LOG EN LA NUBE
def guardar_log_en_drive(log_actualizado):
    try:
        drive_service = obtener_servicio_drive()
        contenido_json = json.dumps(log_actualizado, ensure_ascii=False, indent=4).encode('utf-8')
        media = MediaInMemoryUpload(contenido_json, mimetype='application/json', resumable=True)
        
        # El método update reemplaza el contenido del archivo sin cambiar su ID
        drive_service.files().update(fileId=FILE_ID, media_body=media).execute()
    except Exception as e:
        st.error(f"❌ Error al actualizar el historial en Google Drive: {e}")

# Esquema de datos requerido a la IA
class EsquemaPoliza(BaseModel):
    nombres: str
    apellidos: str
    tipo_documento: str
    numero_poliza: str
    fecha_inicio: str
    fecha_fin: str

# Configuración del modelo Gemini
model = genai.GenerativeModel(
    model_name="gemini-3.5-flash",
    generation_config={
        "response_mime_type": "application/json",
        "response_schema": EsquemaPoliza,
        "temperature": 0.1
    }
)

# Corrección adaptada al formato preferido (DD/MM/AAAA)
def corregir_formato_fecha(fecha_str):
    if not fecha_str or "especificado" in fecha_str.lower():
        return fecha_str
    # Reemplaza guiones, puntos o espacios por barras oblicuas
    limpia = re.sub(r'[-.\s]', '/', fecha_str)
    partes = limpia.split('/')
    if len(partes) == 3:
        dia = partes[0].strip().zfill(2)
        mes = partes[1].strip().zfill(2)
        anio = partes[2].strip()
        if len(anio) == 2:
            anio = "20" + anio
        return f"{dia}/{mes}/{anio}"
    return fecha_str

# Interfaz de carga de archivos de Streamlit
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
        
        status_text.text("🔄 Sincronizando historial desde tu Google Drive...")
        log_historico = cargar_log_desde_drive()
        
        for index, archivo in enumerate(archivos_cargados):
            nombre_original = archivo.name
            extension = os.path.splitext(nombre_original)[1].lower()
            
            try:
                archivo_bytes = archivo.read()
                hash_archivo = hashlib.sha256(archivo_bytes).hexdigest()
                
                # Verificar duplicados en el historial de Google Drive (Evita gastar tokens de Gemini)
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
                
                # Estructuración inteligente de nombres para evitar roturas de extensión
                if "especificado" in datos.nombres.lower() or "especificado" in datos.numero_poliza.lower():
                    nuevo_nombre = f"[MANUAL] - {nombre_original}"
                else:
                    f_inicio = corregir_formato_fecha(datos.fecha_inicio)
                    f_fin = corregir_formato_fecha(datos.fecha_fin)
                    tipo_doc = datos.tipo_documento.lower().strip()
                    
                    if "rnc" in tipo_doc:
                        tipo_doc = "póliza"
                    
                    nuevo_nombre = f"{datos.nombres.strip()} {datos.apellidos.strip()} - {tipo_doc} - {datos.numero_poliza.strip()} - vigencia {f_inicio} al {f_fin}{extension}"
                
                st.session_state.archivos_listos[nuevo_nombre] = archivo_bytes
                
                # Guardar registro en el historial con formato (DD/MM/AAAA)
                fecha_hoy = datetime.now().strftime("%d/%m/%Y")
                log_historico[hash_archivo] = {
                    "nombre_original": nombre_original,
                    "nombre_nuevo": nuevo_nombre,
                    "fecha_revision": fecha_hoy
                }
                
                guardar_log_en_drive(log_historico)
                st.success(f"✅ Procesado y sincronizado: {nuevo_nombre}")
                
            except Exception as e:
                st.error(f"❌ Error en {nombre_original}: {str(e)}")
                st.session_state.archivos_listos[f"[ERROR] - {nombre_original}"] = archivo_bytes
                
            progreso.progress((index + 1) / len(archivos_cargados))
            
        status_text.text("✨ ¡Lote completado e historial sincronizado con éxito!")

# Sección de empaquetado y descarga de resultados (.ZIP)
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
