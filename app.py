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
# Presentación en formato estándar DD/MM/AAAA
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

# Validador avanzado para asegurar que la línea corresponde únicamente al nombre del Asegurado
def es_linea_valida_asegurado(linea):
    linea_upper = linea.upper().strip()
    
    # Descartar líneas con demasiados números (RNC, teléfonos, pólizas)
    if len(re.findall(r'\d', linea_upper)) > 4:
        return False
        
    # Descartar fechas escritas (ej: 10 de Julio de 2026)
    if re.search(r'\d{1,2}\s+DE\s+[A-Z]+\s+DE\s+\d{4}', linea_upper):
        return False
        
    # Vocabulario prohibido: ramos, brókers, direcciones, etc.
    palabras_prohibidas = [
        "FACTURA", "RENOVACION", "RENOVACIÓN", "PÓLIZA", "POLIZA", "RNC", "TEL:", "TELEFONO", 
        "PÁGINA", "PAGE", "RD$", "PESOS", "SUCURSAL", "CÓDIGO", "CODIGO", "CALLE", "AVE", "AVENIDA", 
        "NUMERO", "NÚMERO", "KM", "ASESORES", "SEGUROS", "CORREDOR", "VIGENCIA", "HASTA", "DESDE",
        "ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", 
        "OCTUBRE", "NOVIEMBRE", "DICIEMBRE", "URB", "EDIFICIO", "C/ ", "ANÁLISIS", "ANALISIS",
        "INCENDIO", "VEHICULO", "VEHÍCULOS", "VEHICULOS", "AUTO", "AUTOMOVIL", "AUTOMÓVIL", "EXCESO",
        "RESPONSABILIDAD", "CIVIL", "RAMO", "ENDOSO", "COBRANZA", "PRIMA", "ESTA POLIZA"
    ]
    
    if any(p in linea_upper for p in palabras_prohibidas):
        return False
        
    if len(linea_upper) < 5:
        return False
        
    return True

# Busca el par de fechas en el texto que representen la vigencia anual de la póliza
def extraer_fechas_vigencia(texto):
    # Buscar formatos DD/MM/AAAA o DD-MM-AAAA
    fechas_encontradas = re.findall(r'\b\d{1,2}[/-]\d{1,2}[/-]\d{4}\b', texto)
    fechas_limpias = []
    
    for f in fechas_encontradas:
        f_norm = f.replace("/", "-")
        partes = f_norm.split("-")
        if len(partes) == 3:
            dia = partes[0].zfill(2)
            mes = partes[1].zfill(2)
            ano = partes[2]
            fechas_limpias.append(f"{dia}-{mes}-{ano}")
            
    # También buscar fechas escritas en texto (ej: 10 de Julio de 2026)
    meses_map = {
        "ENERO": "01", "FEBRERO": "02", "MARZO": "03", "ABRIL": "04", "MAYO": "05", 
        "JUNIO": "06", "JULIO": "07", "AGOSTO": "08", "SEPTIEMBRE": "09", "OCTUBRE": "10", 
        "NOVIEMBRE": "11", "DICIEMBRE": "12"
    }
    fechas_texto = re.findall(r'(\d{1,2})\s+de\s+(enero|febrero|marzo|abril|mayo|junio|julio|agosto|septiembre|octubre|noviembre|diciembre)\s+de[l]?\s+(\d{4})', texto, re.IGNORECASE)
    for ft in fechas_texto:
        dia = ft[0].zfill(2)
        mes = meses_map[ft[1].upper()]
        ano = ft[2]
        fechas_limpias.append(f"{dia}-{mes}-{ano}")

    # Eliminar duplicados manteniendo el orden de aparición
    fechas_unicas = list(dict.fromkeys(fechas_limpias))
    
    # Heurística inteligente: Buscar cualquier par de fechas separadas por ~1 año (365 días)
    for i in range(len(fechas_unicas)):
        for j in range(i + 1, len(fechas_unicas)):
            try:
                d1 = datetime.strptime(fechas_unicas[i], "%d-%m-%Y")
                d2 = datetime.strptime(fechas_unicas[j], "%d-%m-%Y")
                d_diff = abs((d2 - d1).days)
                if 360 <= d_diff <= 370:
                    # Retornar ordenadas cronológicamente
                    if d1 < d2:
                        return fechas_unicas[i], fechas_unicas[j]
                    else:
                        return fechas_unicas[j], fechas_unicas[i]
            except ValueError:
                continue
                
    # Si no se encuentra un par exacto de 1 año, tomar las dos primeras detectadas
    if len(fechas_unicas) >= 2:
        return fechas_unicas[0], fechas_unicas[1]
    elif len(fechas_unicas) == 1:
        return fechas_unicas[0], "FIN"
        
    return "INICIO", "FIN"


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
                
                # Separar el texto en líneas
                lineas = [l.strip() for l in texto_extraido.split('\n') if l.strip()]
                
                # A. Extracción del Asegurado con el filtro avanzado
                asegurado = "ASEGURADO_DESCONOCIDO"
                for linea in lineas:
                    if es_linea_valida_asegurado(linea):
                        asegurado = linea
                        break
                
                # B. Determinación del Tipo de Documento
                # Priorizar RENOVACION si aparece ese concepto en el texto
                tipo_doc = "RENOVACION" if "RENOVACION" in texto_extraido.upper() or "RENOVACIÓN" in texto_extraido.upper() else "FACTURA"
                
                # C. Extracción de Póliza
                coincidencia_poliza = re.search(r'\b([A-Z]{1,5}-\d+|\d+-\d+|\b\d{7,15}\b)\b', texto_extraido)
                poliza = coincidencia_poliza.group(0) if coincidencia_poliza else "S-N"
                
                # D. Extracción de la Vigencia Real (Par de 1 año)
                fecha_inicio, fecha_fin = extraer_fechas_vigencia(texto_extraido)
                
                # E. Creación del nombre final exacto (Las fechas usan guiones por compatibilidad de archivos en el SO)
                nuevo_nombre_pdf = f"{asegurado}, {tipo_doc} {poliza}, VIGENCIA {fecha_inicio} AL {fecha_fin}.pdf"
                
                # Guardar el archivo renombrado en el ZIP
                uploaded_file.seek(0)
                zip_file.writestr(nuevo_nombre_pdf, uploaded_file.read())
                
                # Preparar datos para la tabla y el JSON
                tabla_nombres.append({
                    "Archivo Original": uploaded_file.name,
                    "NUEVO NOMBRE GENERADO": nuevo_nombre_pdf
                })
                
                registros_json.append({
                    "Archivo": nuevo_nombre_pdf,
                    "Ramo": "Vehículos" if "VEHÍCULO" in texto_extraido.upper() else "Generales",
                    "Detalle_Objeto": f"Póliza de {asegurado}", 
                    "Sub_Modelo": "N/A",                                      
                    "Suma_Asegurada_RD": 0.00,                                 
                    "Estatus": "Correcto",
                    "Nota_Auditoria": "Procesado correctamente por lotes.",
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
        
        # 3. Mostrar correspondencias
        st.write("### 📋 Tabla de Correspondencia de Archivos")
        st.dataframe(tabla_nombres, use_container_width=True)
        
        zip_buffer.seek(0)
        
        # 4. Descargas
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
