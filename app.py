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
st.markdown("Procesamiento de pólizas con protección de consumo y registro de actividad en Google Drive.")

# 1. CONFIGURACIÓN DE SEGURIDAD Y LLAVES
if "GEMINI_API_KEY" in st.secrets and "GCP_SERVICE_ACCOUNT" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.error("🔑 Falta configurar 'GEMINI_API_KEY' o 'GCP_SERVICE_ACCOUNT' en los secretos de Streamlit.")
    st.stop()

# Inicializar memoria de la sesión actual
if "archivos_listos" not in st.session_state:
    st.session_state.archivos_listos = {}

# ID DIRECTO DEL ARCHIVO EN DRIVE (registro_procesados)
FILE_ID = "1qC9450pvpyFgTOc6uyDegSmdVjWmT4qM"

# Conectar de forma segura con Google Cloud
def obtener_servicio_drive():
    secreto_gcp = st.secrets["GCP_SERVICE_ACCOUNT"]
    if isinstance(secreto_gcp, str):
        info_claves = json.loads(secreto_gcp)
    else:
        info_claves = dict(secreto_gcp)
    
    if "private_key" in info_claves:
        info_claves["private_key"] = info_claves["private_key"].replace("\\n", "\n")
        
    credenciales = service_account.Credentials.from_service_account_info(
        info_claves, 
        scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build('drive', 'v3', credentials=credenciales)

# LECTURA EVOLUCIONADA
def cargar_log_desde_drive():
    estructura_base = {"procesados": {}, "actividad_reciente": []}
    try:
        drive_service = obtener_servicio_drive()
        
        try:
            peticion = drive_service.files().get_media(fileId=FILE_ID)
            fh = io.BytesIO()
            descargador = MediaIoBaseDownload(fh, peticion)
            done = False
            while not done:
                _, done = descargador.next_chunk()
            contenido = fh.getvalue().decode('utf-8').strip()
            
        except Exception as e_binario:
            if "fileNotDownloadable" not in str(e_binario):
                st.error(f"❌ Error de acceso a la API de Drive: {e_binario}")
                return estructura_base
                
            try:
                metadatos = drive_service.files().get(fileId=FILE_ID, fields="mimeType").execute()
                mime_type = metadatos.get("mimeType", "")
                if "spreadsheet" in mime_type:
                    peticion = drive_service.files().export_media(fileId=FILE_ID, mimeType="text/csv")
                else:
                    peticion = drive_service.files().export_media(fileId=FILE_ID, mimeType="text/plain")
                    
                fh = io.BytesIO()
                descargador = MediaIoBaseDownload(fh, peticion)
                done = False
                while not done:
                    _, done = descargador.next_chunk()
                contenido = fh.getvalue().decode('utf-8').strip()
            except Exception:
                st.error("❌ Elemento de Drive incompatible. Asegúrate de usar un archivo .json válido.")
                return estructura_base

        if not contenido:
            return estructura_base
            
        datos_cargados = json.loads(contenido)
        if "procesados" not in datos_cargados:
            return {"procesados": datos_cargados, "actividad_reciente": []}
            
        return datos_cargados
        
    except json.JSONDecodeError:
        st.warning("⚠️ Formato JSON inválido detectado en la nube. Reestructurando historial...")
        return estructura_base
    except Exception as e:
        st.error(f"❌ Error general al procesar el historial: {e}")
        return estructura_base

# ACTUALIZACIÓN DEL LOG DE ACTIVIDAD
def guardar_log_en_drive(log_actualizado):
    try:
        drive_service = obtener_servicio_drive()
        contenido_json = json.dumps(log_actualizado, ensure_ascii=False, indent=4).encode('utf-8')
        media = MediaInMemoryUpload(contenido_json, mimetype='application/json', resumable=True)
        drive_service.files().update(fileId=FILE_ID, media_body=media).execute()
    except Exception as e:
        st.error(f"❌ Error al actualizar el historial en Google Drive: {e}")

# Esquema de datos para la IA
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

# Formateo estricto de fechas (dd.MM.AAAA)
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

# Interfaz de usuario
archivos_cargados = st.file_uploader(
    "Arrastra aquí los PDFs o imágenes de las pólizas:", 
    type=["pdf", "jpg", "jpeg", "png"], 
    accept_multiple_files=True
)

# Cargar el registro actual de Drive para mostrarlo en la interfaz
log_completo = cargar_log_desde_drive()

if archivos_cargados:
    st.subheader(f"📋 Archivos listos para procesar: {len(archivos_cargados)}")
    
    if st.button("🔄 Limpiar progreso de esta tanda"):
        st.session_state.archivos_listos = {}
        st.rerun()
        
    if st.button("🚀 Iniciar Procesamiento con IA", type="primary"):
        progreso = st.progress(0)
        status_text = st.empty()
        
        status_text.text("🔄 Sincronizando historial de seguridad desde Drive...")
        log_completo = cargar_log_desde_drive()
        
        dicc_procesados = log_completo["procesados"]
        lista_actividad = log_completo["actividad_reciente"]
        
        for index, archivo in enumerate(archivos_cargados):
            nombre_original = archivo.name
            extension = os.path.splitext(nombre_original)[1].lower()
            fecha_hora_accion = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            
            try:
                archivo_bytes = archivo.read()
                hash_archivo = hashlib.sha256(archivo_bytes).hexdigest()
                
                # Validar si ya se procesó
                if hash_archivo in dicc_procesados:
                    registro = dicc_procesados[hash_archivo]
                    st.info(f"ℹ️ Omitiendo IA para '{nombre_original}'. Encontrado en registro histórico.")
                    st.session_state.archivos_listos[registro['nombre_nuevo']] = archivo_bytes
                    progreso.progress((index + 1) / len(archivos_cargados))
                    continue
                    
                status_text.text(f"Analizando póliza ({index + 1}/{len(archivos_cargados)}): {nombre_original}")
                
                mime_type = "application/pdf" if extension == ".pdf" else "image/jpeg"
                prompt = "Extrae del documento el nombre y apellido del asegurado, tipo de documento, número de póliza exacto y vigencias (con año de 4 dígitos)."
                
                # LÍMITE ESTRICTO DE TIEMPO: 30 Segundos máximos sin reintentos
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
                    nuevo_nombre = f"{datos.nombres.strip()} {datos.apellidos.strip()} - {datos.numero_poliza.strip()} - vigencia {f_inicio} al {f_fin}{extension}"
                
                st.session_state.archivos_listos[nuevo_nombre] = archivo_bytes
                
                # Registrar éxito
                dicc_procesados[hash_archivo] = {
                    "nombre_original": nombre_original,
                    "nombre_nuevo": nuevo_nombre,
                    "fecha_revision": fecha_hora_accion.split()[0]
                }
                
                lista_actividad.insert(0, {
                    "fecha": fecha_hora_accion,
                    "archivo": nombre_original,
                    "estado": "Éxito",
                    "detalle": f"Organizado como: {nuevo_nombre}"
                })
                
                log_completo["actividad_reciente"] = lista_actividad[:40]
                guardar_log_en_drive(log_completo)
                
                st.success(f"✅ Sincronizado correctamente: {nuevo_nombre}")
                
            except Exception as e:
                error_msg = str(e)
                st.error(f"❌ Error en {nombre_original}: {error_msg}")
                st.session_state.archivos_listos[f"[ERROR] - {nombre_original}"] = archivo_bytes
                
                # GUARDAR EL HISTORIAL DE ERRORES (Timeout o archivo corrupto)
                lista_actividad.insert(0, {
                    "fecha": fecha_hora_accion,
                    "archivo": nombre_original,
                    "estado": "Error",
                    "detalle": error_msg[:150]
                })
                log_completo["actividad_reciente"] = lista_actividad[:40]
                guardar_log_en_drive(log_completo)
                
            progreso.progress((index + 1) / len(archivos_cargados))
            
        status_text.text("✨ ¡Lote finalizado! Historial de control actualizado en la nube.")

# Descarga de resultados estructurados
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

# 📊 SECCIÓN DE AUDITORÍA
st.markdown("---")
with st.expander("📊 Panel de Control e Historial de Errores (Google Drive)", expanded=True):
    col1, col2 = st.columns(2)
    col1.metric("Pólizas en Base de Datos", len(log_completo.get("procesados", {})))
    col2.metric("Eventos de Actividad Grabados", len(log_completo.get("actividad_reciente", [])))
    
    actividades = log_completo.get("actividad_reciente", [])
    if actividades:
        st.markdown("**Últimos movimientos detectados en tu cuenta:**")
        for act in actividades:
            if act["estado"] == "Éxito":
                st.caption(f"🟢 **[{act['fecha']}]** {act['archivo']} ➔ *{act['detalle']}*")
            else:
                st.caption(f"🔴 **[{act['fecha']}] FALLÓ:** {act['archivo']} ➔ `{act['detalle']}`")
    else:
        st.info("No se registran movimientos en el historial de actividad de este archivo JSON.")
