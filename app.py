import streamlit as st
import PyPDF2
import io
import os
import json
import re
import zipfile
from datetime import datetime

# Configuración de la interfaz de la aplicación
st.set_page_config(
    page_title="Procesamiento por Lotes - Seguros", 
    page_icon="📄", 
    layout="wide"
)

st.title("Procesamiento por Lotes y Registro de Documentos 📄")
fecha_actual = datetime.now().strftime('%d/%m/%Y')
st.write(f"**Fecha de análisis:** {fecha_actual}")

# Archivo de persistencia de datos
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

# Validador avanzado para asegurar que la línea corresponde únicamente al nombre del Asegurado
def es_linea_valida_asegurado(linea, es_fallback=False):
    linea_upper = linea.upper().strip()
    
    # Descartar líneas con más de 4 números (RNC, teléfonos, cédulas, números de póliza)
    if len(re.findall(r'\d', linea_upper)) > 4:
        return False
        
    # Descartar fechas escritas (ej: 10 de Julio de 2026)
    if re.search(r'\d{1,2}\s+DE\s+[A-Z]+\s+DE\s+\d{4}', linea_upper):
        return False
        
    # Vocabulario estrictamente prohibido en cualquier contexto
    prohibidas_siempre = [
        "FACTURA", "RENOVACION", "RENOVACIÓN", "PÓLIZA", "POLIZA", "RNC", "TEL:", "TELEFONO", "TELÉFONO", 
        "PÁGINA", "PAGE", "RD$", "PESOS", "SUCURSAL", "CÓDIGO", "CODIGO", "VIGENCIA", "HASTA", "DESDE",
        "ANÁLISIS", "ANALISIS", "ESTA POLIZA", "ESTA PÓLIZA"
    ]
    
    if any(p in linea_upper for p in prohibidas_siempre):
        return False
        
    # Exclusiones específicas si recurrimos al método fallback de adivinar por líneas
    if es_fallback:
        prohibidas_fallback = [
            "CALLE", "AVE", "AVENIDA", "AV.", "NUMERO", "NÚMERO", "NO.", "KM", "ASESORES", "SEGUROS", 
            "CORREDOR", "URB", "EDIFICIO", "C/ ", "SURA", "MAPFRE", "UNIVERSAL", "RESERVAS", "HUMANO", 
            "COLONIAL", "PATRIA", "BANESCO", "WORLDWIDE", "SANTO DOMINGO", "SANTIAGO", "REPÚBLICA", "REPUBLICA",
            "DOMINICANA", "D.N.", "E-MAIL", "EMAIL", "WWW.", "FAX", "APARTADO", "POSTAL", "APDO"
        ]
        if any(pf in linea_upper for pf in prohibidas_fallback):
            return False
            
    if len(linea_upper) < 4:
        return False
        
    return True

# Busca el nombre del Asegurado usando etiquetas de alta precisión y filtros de respaldo
def extraer_asegurado(texto, lineas):
    # 1. Buscar con patrones clave de asignación directa
    patrones = [
        r'(?:asegurado|cliente|tomador|contratante|propietario|nombre\s+del\s+asegurado)\s*[:/-]\s*([A-ZÁÉÍÓÚÑa-záéíóúñ0-9\s\.,&]+)',
        r'(?:asegurado|cliente|tomador|contratante|propietario|nombre\s+del\s+asegurado)\s*\n\s*([A-ZÁÉÍÓÚÑa-záéíóúñ0-9\s\.,&]+)'
    ]
    
    for patron in patrones:
        coincidencias = re.findall(patron, texto, re.IGNORECASE)
        for coincidencia in coincidencias:
            c_limpia = coincidencia.strip()
            c_limpia = re.sub(r'\s+', ' ', c_limpia)  # Limpiar espacios múltiples y saltos de línea
            if es_linea_valida_asegurado(c_limpia, es_fallback=False):
                return c_limpia
                
    # 2. Fallback: Buscar la primera línea que parezca un nombre con el blocklist completo
    for linea in lineas:
        if es_linea_valida_asegurado(linea, es_fallback=True):
            return linea
            
    return "ASEGURADO_DESCONOCIDO"

# Busca el par de fechas en el texto que representen la vigencia anual de la póliza (DD-MM-AAAA)
def extraer_fechas_vigencia(texto):
    fechas_limpias = []
    
    # 1. Buscar formatos numéricos: DD/MM/AAAA, DD-MM-AAAA, DD.MM.AAAA
    fechas_num = re.findall(r'\b\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{4}\b', texto)
    for f in fechas_num:
        f_norm = f.replace("/", "-").replace(".", "-")
        partes = f_norm.split("-")
        if len(partes) == 3:
            dia = partes[0].zfill(2)
            mes = partes[1].zfill(2)
            ano = partes[2]
            try:
                int_dia, int_mes, int_ano = int(dia), int(mes), int(ano)
                # Validar lógica básica del calendario
                if 1 <= int_mes <= 12 and 1 <= int_dia <= 31:
                    fechas_limpias.append(f"{dia}-{mes}-{ano}")
            except ValueError:
                continue

    # 2. Buscar formatos redactados en texto (ej: 10 de Julio de 2026, 10 de Julio del 2026)
    meses_map = {
        "ENERO": "01", "ENE": "01", "FEBRERO": "02", "FEB": "02", "MARZO": "03", "MAR": "03",
        "ABRIL": "04", "ABR": "04", "MAYO": "05", "MAY": "05", "JUNIO": "06", "JUN": "06",
        "JULIO": "07", "JUL": "07", "AGOSTO": "08", "AGO": "08", "SEPTIEMBRE": "09", "SEP": "09", 
        "SETIEMBRE": "09", "SET": "09", "OCTUBRE": "10", "OCT": "10", "NOVIEMBRE": "11", "NOV": "11",
        "DICIEMBRE": "12", "DIC": "12"
    }
    
    patron_texto = r'(\d{1,2})\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|setiembre|octubre|noviembre|diciembre)\s+de[l]?\s+(\d{4})'
    fechas_texto = re.findall(patron_texto, texto, re.IGNORECASE)
    for ft in fechas_texto:
        dia = ft[0].zfill(2)
        mes = meses_map[ft[1].upper()]
        ano = ft[2]
        fechas_limpias.append(f"{dia}-{mes}-{ano}")
        
    # 3. Buscar formato abreviado (ej: 26-Jun-2026)
    patron_mes_corto = r'\b(\d{1,2})[/\-\.](ene|feb|mar|abr|may|jun|jul|ago|sep|set|oct|nov|dic)[/\-\.](\d{4})\b'
    fechas_mes_corto = re.findall(patron_mes_corto, texto, re.IGNORECASE)
    for fmc in fechas_mes_corto:
        dia = fmc[0].zfill(2)
        mes = meses_map[fmc[1].upper()]
        ano = fmc[2]
        fechas_limpias.append(f"{dia}-{mes}-{ano}")

    # Eliminar duplicados manteniendo el orden
    fechas_unicas = list(dict.fromkeys(fechas_limpias))
    
    # Heurística inteligente: Buscar el par de fechas con ~365 días de diferencia
    for i in range(len(fechas_unicas)):
        for j in range(i + 1, len(fechas_unicas)):
            try:
                d1 = datetime.strptime(fechas_unicas[i], "%d-%m-%Y")
                d2 = datetime.strptime(fechas_unicas[j], "%d-%m-%Y")
                d_diff = abs((d2 - d1).days)
                if 360 <= d_diff <= 370:
                    if d1 < d2:
                        return fechas_unicas[i], fechas_unicas[j]
                    else:
                        return fechas_unicas[j], fechas_unicas[i]
            except ValueError:
                continue
                
    # Fallback: Si no hay par anual, tomar los dos primeros encontrados
    if len(fechas_unicas) >= 2:
        return fechas_unicas[0], fechas_unicas[1]
    elif len(fechas_unicas) == 1:
        return fechas_unicas[0], "FIN"
        
    return "INICIO", "FIN"


# Componente de carga masiva de archivos
uploaded_files = st.file_uploader(
    "Arrastra o selecciona tus archivos PDF (Soporta múltiples archivos)", 
    type=["pdf"], 
    accept_multiple_files=True
)

if uploaded_files:
    # Identificador único para el lote actual para evitar reprocesamientos en bucle
    batch_id = "-".join([f"{f.name}_{f.size}" for f in uploaded_files])
    
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
                    pdf_reader = PyPDF2.PdfReader(uploaded_file)
                    texto_extraido = ""
                    if len(pdf_reader.pages) > 0:
                        texto_extraido = pdf_reader.pages[0].extract_text() or ""
                    
                    lineas = [l.strip() for l in texto_extraido.split('\n') if l.strip()]
                    
                    # A. Extracción del Asegurado
                    asegurado = extraer_asegurado(texto_extraido, lineas)
                    
                    # B. Determinación de Tipo de Documento
                    tipo_doc = "RENOVACION" if "RENOVAC" in texto_extraido.upper() else "FACTURA"
                    
                    # C. Extracción de Póliza
                    coincidencia_poliza = re.search(r'\b([A-Z]{1,5}-\d+|\d+-\d+|\b\d{7,15}\b)\b', texto_extraido)
                    poliza = coincidencia_poliza.group(0) if coincidencia_poliza else "S-N"
                    
                    # D. Extracción de Vigencia Real (Par de 1 año)
                    fecha_inicio, fecha_fin = extraer_fechas_vigencia(texto_extraido)
                    
                    # E. Nombre final estructurado (Fechas con guiones para seguridad del SO)
                    nuevo_nombre_pdf = f"{asegurado}, {tipo_doc} {poliza}, VIGENCIA {fecha_inicio} AL {fecha_fin}.pdf"
                    
                    # Empaquetado en el ZIP en memoria
                    uploaded_file.seek(0)
                    zip_file.writestr(nuevo_nombre_pdf, uploaded_file.read())
                    
                    # Registros de datos
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
        
        # Almacenamiento persistente local de una única escritura
        if registros_json:
            st.session_state.historial_completo = guardar_lote_en_json(registros_json)
            st.session_state.tabla_nombres = tabla_nombres
            zip_buffer.seek(0)
            st.session_state.zip_data = zip_buffer.getvalue()
            st.success(f"✅ Se han procesado y registrado {len(registros_json)} archivos en el JSON local con éxito.")

    # Renderizado seguro de componentes visuales en Streamlit
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
