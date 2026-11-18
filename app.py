from datetime import datetime
import hashlib
import io
import json
import os
import re
import time
import zipfile

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaInMemoryUpload, MediaIoBaseDownload
import google.generativeai as genai
import streamlit as st
import typing_extensions as typing

# Configuración de la página de Streamlit
st.set_page_config(
    page_title="Extractor de Seguros IA", page_icon="🌐", layout="centered"
)

st.title("🌐 Extractor de Seguros - Nube Sincronizada")
st.markdown(
    "Procesamiento de pólizas con protección de consumo, edición en pantalla y registro en Google Drive."
)

# 1. CONFIGURACIÓN DE SEGURIDAD Y LLAVES
if "GEMINI_API_KEY" in st.secrets and "GCP_SERVICE_ACCOUNT" in st.secrets:
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
else:
    st.error(
        "🔑 Falta configurar 'GEMINI_API_KEY' o 'GCP_SERVICE_ACCOUNT' en los secretos de Streamlit."
    )
    st.stop()

# Inicializar memorias de la sesión actual
if "archivos_listos" not in st.session_state:
    st.session_state.archivos_listos = {}

if "archivos_con_error" not in st.session_state:
    st.session_state.archivos_con_error = {}

if "metadatos_lote" not in st.session_state:
    st.session_state.metadatos_lote = {}

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
        info_claves["private_key"] = info_claves["private_key"].replace(
            "\\n", "\n"
        )

    credenciales = service_account.Credentials.from_service_account_info(
        info_claves, scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=credenciales, cache_discovery=False)


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
            contenido = fh.getvalue().decode("utf-8").strip()

        except Exception as e_binario:
            if "fileNotDownloadable" not in str(e_binario):
                st.error(f"❌ Error de acceso a la API de Drive: {e_binario}")
                return estructura_base

            try:
                metadatos = (
                    drive_service.files()
                    .get(fileId=FILE_ID, fields="mimeType")
                    .execute()
                )
                mime_type = metadatos.get("mimeType", "")
                if "spreadsheet" in mime_type:
                    peticion = drive_service.files().export_media(
                        fileId=FILE_ID, mimeType="text/csv"
                    )
                else:
                    peticion = drive_service.files().export_media(
                        fileId=FILE_ID, mimeType="text/plain"
                    )

                fh = io.BytesIO()
                descargador = MediaIoBaseDownload(fh, peticion)
                done = False
                while not done:
                    _, done = descargador.next_chunk()
                contenido = fh.getvalue().decode("utf-8").strip()
            except Exception:
                st.error(
                    "❌ Elemento de Drive incompatible. Asegúrate de usar un archivo .json válido."
                )
                return estructura_base

        if not contenido:
            return estructura_base

        datos_cargados = json.loads(contenido)
        if "procesados" not in datos_cargados:
            return {"procesados": datos_cargados, "actividad_reciente": []}

        return datos_cargados

    except json.JSONDecodeError:
        st.warning(
            "⚠️ Formato JSON inválido detectado en la nube. Reestructurando historial..."
        )
        return estructura_base
    except Exception as e:
        st.error(f"❌ Error general al procesar el historial: {e}")
        return estructura_base


# ACTUALIZACIÓN DEL LOG DE ACTIVIDAD CON REINTENTO DE RED
def guardar_log_en_drive(log_actualizado):
    contenido_json = json.dumps(
        log_actualizado, ensure_ascii=False, indent=4
    ).encode("utf-8")

    for intento_drive in range(2):
        try:
            drive_service = obtener_servicio_drive()
            media = MediaInMemoryUpload(
                contenido_json, mimetype="application/json", resumable=True
            )
            drive_service.files().update(
                fileId=FILE_ID, media_body=media
            ).execute()
            return
        except Exception as e:
            if intento_drive == 0:
                time.sleep(2)
            else:
                st.error(
                    f"❌ Error al actualizar el historial en Google Drive: {e}"
                )


# Esquema nativo compatible 100% con Google Generative AI (TypedDict)
class EsquemaPoliza(typing.TypedDict):
    nombres: str
    apellidos: str
    tipo_documento: str
    concepto: str
    numero_poliza: str
    fecha_inicio: str
    fecha_fin: str


# Configuración compartida de generación
config_generacion = {
    "response_mime_type": "application/json",
    "response_schema": EsquemaPoliza,
    "temperature": 0.1,
}

# Modelo principal (máxima precisión) y modelo de respaldo (contingencia ante 504)
modelo_principal = genai.GenerativeModel(
    model_name="gemini-3.8-flash",
    generation_config=config_generacion,
)

modelo_respaldo = genai.GenerativeModel(
    model_name="gemini-3.7-flash",
    generation_config=config_generacion,
)


# Formateo de fechas para nombres de archivo (dd-MM-AAAA)
def corregir_formato_fecha(fecha_str):
    if not fecha_str or "especificado" in str(fecha_str).lower():
        return fecha_str
    limpia = re.sub(r"[-/.\s]", "-", str(fecha_str).strip())
    partes = limpia.split("-")
    if len(partes) >= 3:
        dia = partes[0].strip().zfill(2)
        mes = partes[1].strip().zfill(2)
        anio = partes[2].strip()
        if len(anio) == 2:
            anio = "20" + anio
        return f"{dia}-{mes}-{anio}"
    return fecha_str


# Construir nombre estandarizado en mayúsculas
def construir_nombre_estandar(
    cliente, concepto, numero_poliza, fecha_inicio, fecha_fin, extension
):
    f_inicio = corregir_formato_fecha(fecha_inicio)
    f_fin = corregir_formato_fecha(fecha_fin)
    tipo_doc = concepto.strip() if concepto.strip() else "DOCUMENTO"
    cliente_limpio = cliente.strip()
    poliza_limpia = numero_poliza.strip()

    cadena_nombre = (
        f"{cliente_limpio} - PÓLIZA - {tipo_doc} - {poliza_limpia} "
        f"- VIGENCIA {f_inicio} AL {f_fin}{extension}"
    )
    return re.sub(r"\s+", " ", cadena_nombre).upper()


# Extraer campos de un nombre ya formateado (para edición de archivos recuperados de Drive)
def desglosar_nombre_estructurado(nombre_estructurado, extension):
    base = nombre_estructurado
    if base.lower().endswith(extension.lower()):
        base = base[: -len(extension)]

    patron = (
        r"^(.*?)\s*-\s*PÓLIZA\s*-\s*(.*?)\s*-\s*(.*?)\s*-\s*VIGENCIA\s*(.*?)\s*AL\s*(.*)\$"
    )
    coincidencia = re.match(patron, base, re.IGNORECASE)
    if coincidencia:
        return {
            "cliente": coincidencia.group(1).strip(),
            "concepto": coincidencia.group(2).strip(),
            "numero_poliza": coincidencia.group(3).strip(),
            "fecha_inicio": coincidencia.group(4).strip(),
            "fecha_fin": coincidencia.group(5).strip(),
        }
    return {
        "cliente": "",
        "concepto": "",
        "numero_poliza": "",
        "fecha_inicio": "",
        "fecha_fin": "",
    }


# Limpieza preventiva de bloques Markdown en respuestas JSON
def limpiar_respuesta_json(texto_respuesta):
    texto = texto_respuesta.strip()
    if texto.startswith("```"):
        texto = re.sub(r"^```(?:json)?\s*", "", texto)
        texto = re.sub(r"\s*```$", "", texto)
    return texto.strip()


# MOTOR UNIFICADO DE PROCESAMIENTO (LOTE INICIAL Y REINTENTOS)
def procesar_lote_documentos(lista_documentos, es_reintento=False):
    progreso = st.progress(0)
    status_text = st.empty()

    status_text.text("🔄 Sincronizando historial de seguridad desde Drive...")
    log_actual = cargar_log_desde_drive()

    dicc_procesados = log_actual["procesados"]
    lista_actividad = log_actual["actividad_reciente"]
    total_docs = len(lista_documentos)

    prompt = (
        "Analiza minuciosamente el documento de seguro y extrae los datos requeridos.\n"
        "- Si el asegurado es una PERSONA JURÍDICA / EMPRESA (ej. 'FARMACIA PUJOLS SRL'), coloca la razón social completa en 'nombres' y deja 'apellidos' como cadena vacía ('').\n"
        "- Extrae el concepto/tipo de movimiento (ej. 'Factura Ajuste', 'Renovación', 'Inclusión', 'Póliza Nueva', 'Incendio y Líneas Aliadas').\n"
        "- Extrae el número de póliza exacto.\n"
        "- VIGENCIAS: Extrae la vigencia del movimiento o factura actual (ej. '13/05/2026' a '31/05/2026'). No utilices vigencias anuales históricas de páginas secundarias si difieren de la principal.\n"
        "- Si algún dato no se encuentra en el documento, devuelve una cadena vacía ('') en ese campo, pero NUNCA omitas las claves del JSON."
    )

    for index, doc in enumerate(lista_documentos):
        nombre_original = doc["nombre_original"]
        archivo_bytes = doc["bytes"]
        clave_error = f"[ERROR] - {nombre_original}".upper()
        extension = os.path.splitext(nombre_original)[1].lower()
        fecha_hora_accion = datetime.now().strftime("%d/%m/%Y %H:%M:%S")

        try:
            hash_archivo = hashlib.sha256(archivo_bytes).hexdigest()

            # Validar si ya se procesó en el pasado
            if hash_archivo in dicc_procesados:
                registro = dicc_procesados[hash_archivo]
                nombre_recuperado = registro["nombre_nuevo"]
                st.info(
                    f"ℹ️ Omitiendo IA para '{nombre_original}'. Recuperado del registro histórico."
                )
                st.session_state.archivos_listos[nombre_recuperado] = (
                    archivo_bytes
                )
                st.session_state.archivos_listos.pop(clave_error, None)
                st.session_state.archivos_con_error.pop(clave_error, None)

                campos_previos = desglosar_nombre_estructurado(
                    nombre_recuperado, extension
                )
                st.session_state.metadatos_lote[hash_archivo] = {
                    "hash": hash_archivo,
                    "nombre_original": nombre_original,
                    "nombre_actual": nombre_recuperado,
                    "extension": extension,
                    "bytes": archivo_bytes,
                    "es_manual": nombre_recuperado.startswith("[MANUAL]"),
                    "cliente": campos_previos["cliente"],
                    "concepto": campos_previos["concepto"],
                    "numero_poliza": campos_previos["numero_poliza"],
                    "fecha_inicio": campos_previos["fecha_inicio"],
                    "fecha_fin": campos_previos["fecha_fin"],
                }

                progreso.progress((index + 1) / total_docs)
                continue

            # Asignación exacta de MIME type según extensión
            if extension == ".pdf":
                mime_type = "application/pdf"
            elif extension == ".png":
                mime_type = "image/png"
            else:
                mime_type = "image/jpeg"

            # Control de reintentos con timeout extendido y respaldo automático
            intentos_maximos = 3
            tiempos_espera = [8, 15]
            respuesta = None

            for intento in range(intentos_maximos):
                try:
                    modelo_actual = (
                        modelo_principal if intento < 2 else modelo_respaldo
                    )
                    nombre_modelo = (
                        "3.8-Flash"
                        if intento < 2
                        else "3.7-Flash (Respaldo)"
                    )
                    timeout_actual = 120.0 if intento == 0 else 180.0

                    etiqueta_modo = (
                        "Reintentando" if es_reintento else "Analizando"
                    )
                    status_text.text(
                        f"{etiqueta_modo} póliza ({index + 1}/{total_docs}): "
                        f"{nombre_original} [Intento {intento + 1} - {nombre_modelo}]"
                    )

                    respuesta = modelo_actual.generate_content(
                        [
                            {"mime_type": mime_type, "data": archivo_bytes},
                            prompt,
                        ],
                        request_options={"timeout": timeout_actual},
                    )
                    if respuesta and respuesta.text:
                        break
                except Exception as e_ia:
                    if intento < intentos_maximos - 1:
                        espera = tiempos_espera[intento]
                        st.warning(
                            f"⚠️ Reintento {intento + 1} para '{nombre_original}' "
                            f"tras pausa de {espera}s por respuesta tardía o cuota de API..."
                        )
                        time.sleep(espera)
                    else:
                        raise e_ia

            # Parseo seguro con limpieza preventiva y valores por defecto
            texto_limpio = limpiar_respuesta_json(respuesta.text)
            datos = json.loads(texto_limpio)
            nombres = str(datos.get("nombres", "") or "").strip()
            apellidos = str(datos.get("apellidos", "") or "").strip()
            concepto = str(datos.get("concepto", "") or "").strip()
            numero_poliza = str(datos.get("numero_poliza", "") or "").strip()
            fecha_inicio = str(datos.get("fecha_inicio", "") or "").strip()
            fecha_fin = str(datos.get("fecha_fin", "") or "").strip()

            nombre_completo = f"{nombres} {apellidos}".strip()
            es_manual = (
                "especificado" in nombres.lower()
                or "especificado" in numero_poliza.lower()
                or (not nombres and not apellidos)
            )

            if es_manual:
                nuevo_nombre = f"[MANUAL] - {nombre_original}".upper()
            else:
                nuevo_nombre = construir_nombre_estandar(
                    cliente=nombre_completo,
                    concepto=concepto,
                    numero_poliza=numero_poliza,
                    fecha_inicio=fecha_inicio,
                    fecha_fin=fecha_fin,
                    extension=extension,
                )

            # Guardar resultado exitoso y limpiar marcas de error previas
            st.session_state.archivos_listos.pop(clave_error, None)
            st.session_state.archivos_con_error.pop(clave_error, None)
            st.session_state.archivos_listos[nuevo_nombre] = archivo_bytes

            # Guardar metadatos estructurados para permitir edición manual en pantalla
            st.session_state.metadatos_lote[hash_archivo] = {
                "hash": hash_archivo,
                "nombre_original": nombre_original,
                "nombre_actual": nuevo_nombre,
                "extension": extension,
                "bytes": archivo_bytes,
                "es_manual": es_manual,
                "cliente": nombre_completo.upper(),
                "concepto": (concepto if concepto else "DOCUMENTO").upper(),
                "numero_poliza": numero_poliza.upper(),
                "fecha_inicio": corregir_formato_fecha(fecha_inicio),
                "fecha_fin": corregir_formato_fecha(fecha_fin),
            }

            # Registrar en Google Drive
            dicc_procesados[hash_archivo] = {
                "nombre_original": nombre_original,
                "nombre_nuevo": nuevo_nombre,
                "fecha_revision": fecha_hora_accion.split()[0],
            }

            lista_actividad.insert(
                0,
                {
                    "fecha": fecha_hora_accion,
                    "archivo": nombre_original,
                    "estado": "Éxito",
                    "detalle": f"Organizado como: {nuevo_nombre}",
                },
            )

            log_actual["actividad_reciente"] = lista_actividad[:40]
            guardar_log_en_drive(log_actual)

            if es_manual:
                st.warning(
                    f"⚠️ Requiere revisión manual en pantalla: {nuevo_nombre}"
                )
            else:
                st.success(f"✅ Sincronizado correctamente: {nuevo_nombre}")

            if index < total_docs - 1:
                time.sleep(1)

        except Exception as e:
            error_msg = str(e)
            st.error(f"❌ Error en {nombre_original}: {error_msg}")

            # Registrar en memoria de errores para permitir reintento selectivo
            st.session_state.archivos_listos[clave_error] = archivo_bytes
            st.session_state.archivos_con_error[clave_error] = {
                "nombre_original": nombre_original,
                "bytes": archivo_bytes,
            }

            # Historial de errores en Google Drive
            lista_actividad.insert(
                0,
                {
                    "fecha": fecha_hora_accion,
                    "archivo": nombre_original,
                    "estado": "Error",
                    "detalle": error_msg[:150],
                },
            )
            log_actual["actividad_reciente"] = lista_actividad[:40]
            guardar_log_en_drive(log_actual)

        progreso.progress((index + 1) / total_docs)

    status_text.text(
        "✨ ¡Proceso finalizado! Historial de control actualizado en la nube."
    )
    return log_actual


# Interfaz de usuario
archivos_cargados = st.file_uploader(
    "Arrastra aquí los PDFs o imágenes de las pólizas:",
    type=["pdf", "jpg", "jpeg", "png"],
    accept_multiple_files=True,
)

# Cargar el registro actual de Drive para mostrarlo en la interfaz
log_completo = cargar_log_desde_drive()

if archivos_cargados:
    st.subheader(f"📋 Archivos listos para procesar: {len(archivos_cargados)}")

    col_btn1, col_btn2 = st.columns(2)

    with col_btn1:
        if st.button(
            "🔄 Limpiar progreso de esta tanda", use_container_width=True
        ):
            st.session_state.archivos_listos = {}
            st.session_state.archivos_con_error = {}
            st.session_state.metadatos_lote = {}
            st.rerun()

    with col_btn2:
        iniciar_lote = st.button(
            "🚀 Iniciar Procesamiento con IA",
            type="primary",
            use_container_width=True,
        )

    if iniciar_lote:
        lote_inicial = []
        for archivo in archivos_cargados:
            archivo.seek(0)
            lote_inicial.append(
                {
                    "nombre_original": archivo.name,
                    "bytes": archivo.read(),
                }
            )
        log_completo = procesar_lote_documentos(
            lote_inicial, es_reintento=False
        )

# ⚠️ PANEL DE REINTENTO EXCLUSIVO PARA ARCHIVOS CON [ERROR]
if st.session_state.archivos_con_error:
    cantidad_errores = len(st.session_state.archivos_con_error)
    st.markdown("---")
    st.warning(
        f"⚠️ Hay **{cantidad_errores} archivo(s)** que no pudieron completarse por tiempo de espera o cuota:"
    )

    for item_err in st.session_state.archivos_con_error.values():
        st.caption(f"• `{item_err['nombre_original']}`")

    if st.button(
        f"🔁 Reintentar únicamente los {cantidad_errores} archivo(s) con [ERROR]",
        type="primary",
        use_container_width=True,
    ):
        lote_reintento = list(st.session_state.archivos_con_error.values())
        log_completo = procesar_lote_documentos(
            lote_reintento, es_reintento=True
        )
        time.sleep(1)
        st.rerun()

# ✏️ PANEL DE EDICIÓN MANUAL EN PANTALLA ([MANUAL] Y CORRECCIÓN GENERAL)
if st.session_state.metadatos_lote:
    st.markdown("---")

    # Ordenar poniendo primero los archivos que quedaron marcados como [MANUAL]
    lista_meta = sorted(
        st.session_state.metadatos_lote.values(),
        key=lambda x: (not x["es_manual"], x["nombre_original"]),
    )
    cantidad_manuales = sum(1 for m in lista_meta if m["es_manual"])

    titulo_expander = (
        f"✏️ Editor Manual de Nombres ({cantidad_manuales} pendiente(s) de revisión [MANUAL])"
        if cantidad_manuales > 0
        else "✏️ Editor Manual de Nombres (Ajustar o corregir cualquier póliza antes de descargar)"
    )

    with st.expander(titulo_expander, expanded=(cantidad_manuales > 0)):
        st.markdown(
            "Selecciona un documento para completar sus datos o corregir su nombre. "
            "Al guardar, se actualizará tanto el archivo en el **.ZIP** como la base de datos en **Google Drive**."
        )

        opciones_selector = {
            f"{'⚠️ [MANUAL] ' if m['es_manual'] else '✅ '}{m['nombre_original']} ➔ {m['nombre_actual']}": m[
                "hash"
            ]
            for m in lista_meta
        }

        etiqueta_seleccionada = st.selectbox(
            "Archivo a editar:",
            options=list(opciones_selector.keys()),
        )
        hash_seleccionado = opciones_selector[etiqueta_seleccionada]
        meta_actual = st.session_state.metadatos_lote[hash_seleccionado]

        # Botón auxiliar para descargar el PDF original si necesitas consultarlo visualmente
        st.download_button(
            label=f"👁️ Abrir documento original ({meta_actual['nombre_original']}) para verificar datos",
            data=meta_actual["bytes"],
            file_name=meta_actual["nombre_original"],
            mime="application/octet-stream",
            key=f"ver_orig_{hash_seleccionado}",
        )

        with st.form(key=f"form_edicion_{hash_seleccionado}"):
            col_f1, col_f2 = st.columns(2)
            with col_f1:
                input_cliente = st.text_input(
                    "Cliente o Razón Social:",
                    value=meta_actual["cliente"],
                    placeholder="Ej. FARMACIA PUJOLS SRL",
                )
                input_poliza = st.text_input(
                    "Número de Póliza:",
                    value=meta_actual["numero_poliza"],
                    placeholder="Ej. 01-0023456-0",
                )
                input_f_inicio = st.text_input(
                    "Vigencia Desde (DD-MM-AAAA):",
                    value=meta_actual["fecha_inicio"],
                    placeholder="Ej. 13-05-2026",
                )

            with col_f2:
                input_concepto = st.text_input(
                    "Concepto / Movimiento:",
                    value=meta_actual["concepto"],
                    placeholder="Ej. RENOVACIÓN / FACTURA AJUSTE",
                )
                st.text_input(
                    "Extensión del archivo:",
                    value=meta_actual["extension"].upper(),
                    disabled=True,
                )
                input_f_fin = st.text_input(
                    "Vigencia Hasta (DD-MM-AAAA):",
                    value=meta_actual["fecha_fin"],
                    placeholder="Ej. 31-05-2026",
                )

            guardar_edicion = st.form_submit_button(
                "💾 Guardar cambios y sincronizar con Google Drive",
                type="primary",
                use_container_width=True,
            )

            if guardar_edicion:
                if not input_cliente.strip() or not input_poliza.strip():
                    st.error(
                        "⚠️ Debes completar al menos el Cliente/Razón Social y el Número de Póliza."
                    )
                else:
                    nuevo_nombre_editado = construir_nombre_estandar(
                        cliente=input_cliente,
                        concepto=input_concepto,
                        numero_poliza=input_poliza,
                        fecha_inicio=input_f_inicio,
                        fecha_fin=input_f_fin,
                        extension=meta_actual["extension"],
                    )

                    nombre_anterior = meta_actual["nombre_actual"]
                    fecha_hora_edicion = datetime.now().strftime(
                        "%d/%m/%Y %H:%M:%S"
                    )

                    # 1. Reemplazar en la lista de descargas de la sesión
                    st.session_state.archivos_listos.pop(nombre_anterior, None)
                    st.session_state.archivos_listos[nuevo_nombre_editado] = (
                        meta_actual["bytes"]
                    )

                    # 2. Actualizar metadatos en memoria
                    meta_actual["nombre_actual"] = nuevo_nombre_editado
                    meta_actual["es_manual"] = False
                    meta_actual["cliente"] = input_cliente.strip().upper()
                    meta_actual["concepto"] = (
                        input_concepto.strip().upper()
                        if input_concepto.strip()
                        else "DOCUMENTO"
                    )
                    meta_actual["numero_poliza"] = input_poliza.strip().upper()
                    meta_actual["fecha_inicio"] = corregir_formato_fecha(
                        input_f_inicio
                    )
                    meta_actual["fecha_fin"] = corregir_formato_fecha(
                        input_f_fin
                    )

                    # 3. Sincronizar cambio con el archivo JSON en Google Drive
                    log_edicion = cargar_log_desde_drive()
                    log_edicion["procesados"][hash_seleccionado] = {
                        "nombre_original": meta_actual["nombre_original"],
                        "nombre_nuevo": nuevo_nombre_editado,
                        "fecha_revision": fecha_hora_edicion.split()[0],
                    }
                    log_edicion["actividad_reciente"].insert(
                        0,
                        {
                            "fecha": fecha_hora_edicion,
                            "archivo": meta_actual["nombre_original"],
                            "estado": "Éxito",
                            "detalle": f"Editado manualmente: {nuevo_nombre_editado}",
                        },
                    )
                    log_edicion["actividad_reciente"] = log_edicion[
                        "actividad_reciente"
                    ][:40]
                    guardar_log_en_drive(log_edicion)

                    st.success(
                        f"✅ Nombre actualizado a: {nuevo_nombre_editado}"
                    )
                    time.sleep(0.8)
                    st.rerun()

# SECCIÓN DE DESCARGAS
archivos_exitosos = {
    k: v
    for k, v in st.session_state.archivos_listos.items()
    if not k.startswith("[ERROR]")
}

if st.session_state.archivos_listos:
    st.markdown("---")
    st.subheader(
        f"📦 Resultados listos ({len(archivos_exitosos)} disponibles de {len(st.session_state.archivos_listos)} procesados)"
    )

    if archivos_exitosos:
        # 1. Botón unificado en ZIP
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
            for nombre_archivo, contenido_bytes in archivos_exitosos.items():
                zip_file.writestr(nombre_archivo, contenido_bytes)

        st.download_button(
            label="💾 Descargar TODO el Lote Organizado (.ZIP)",
            data=zip_buffer.getvalue(),
            file_name="polizas_organizadas.zip",
            mime="application/zip",
            use_container_width=True,
        )

        st.markdown("### 📄 Descargas Individuales")
        st.markdown(
            "Haz clic en cualquier archivo procesado para guardarlo de nuevo con su nombre estructurado:"
        )

        # 2. Descargas individuales
        for nombre_archivo, contenido_bytes in archivos_exitosos.items():
            llave_boton = hashlib.md5(
                nombre_archivo.encode("utf-8")
            ).hexdigest()

            st.download_button(
                label=f"⬇️ Descargar: {nombre_archivo}",
                data=contenido_bytes,
                file_name=nombre_archivo,
                mime="application/octet-stream",
                key=llave_boton,
            )

# 📊 SECCIÓN DE AUDITORÍA
st.markdown("---")
with st.expander(
    "📊 Panel de Control e Historial de Errores (Google Drive)", expanded=True
):
    col1, col2 = st.columns(2)
    col1.metric(
        "Pólizas en Base de Datos", len(log_completo.get("procesados", {}))
    )
    col2.metric(
        "Eventos de Actividad Grabados",
        len(log_completo.get("actividad_reciente", [])),
    )

    actividades = log_completo.get("actividad_reciente", [])
    if actividades:
        st.markdown("**Últimos movimientos detectados en tu cuenta:**")
        for act in actividades:
            if act["estado"] == "Éxito":
                st.caption(
                    f"🟢 **[{act['fecha']}]** {act['archivo']} ➔ *{act['detalle']}*"
                )
            else:
                st.caption(
                    f"🔴 **[{act['fecha']}] FALLÓ:** {act['archivo']} ➔ `{act['detalle']}`"
                )
    else:
        st.info(
            "No se registran movimientos en el historial de actividad de este archivo JSON."
        )
