import os
import time
import fitz  # PyMuPDF
import pytesseract
import cv2
import numpy as np
from PIL import Image
from flask import Flask, render_template, request, redirect, url_for, flash
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY")

# Conexión a Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def preprocesar_imagen_para_ocr(pil_img):
    """
    Mejora imágenes borrosas y tablas (como boletas de pago) antes de pasar a Tesseract.
    Cumple con la mitigación del Riesgo R3 del Acta de Constitución.
    """
    # Convertir de PIL a formato OpenCV (escala de grises)
    img_np = np.array(pil_img)
    gris = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)

    # 1. Reescalar ligeramente si es necesario y aplicar filtro bilateral (quita ruido sin borrar bordes de letras)
    limpia = cv2.bilateralFilter(gris, 9, 75, 75)

    # 2. Umbralización adaptativa (ideal para sombras de escaneo y cuadros/tablas)
    binarizada = cv2.adaptiveThreshold(
        limpia, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 12
    )

    return Image.fromarray(binarizada)


def extraer_texto_pdf_hibrido(pdf_bytes):
    """
    Recorre el PDF página por página:
    - Si la página tiene texto digital nativo, lo extrae directo (100% preciso y rápido).
    - Si la página es escaneada (imagen), la renderiza a 300 DPI, mejora la imagen con OpenCV
      y aplica Tesseract en español con --psm 6 (bloques uniformes/tablas).
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_paginas = len(doc)
    texto_completo = []

    # --psm 6 asume un bloque uniforme de texto, conserva mucho mejor las filas de tablas/boletas
    config_tesseract = "--oem 3 --psm 6"

    for i, pagina in enumerate(doc):
        num_pag = i + 1
        # Intentar extraer texto digital primero
        texto_digital = pagina.get_text("text").strip()

        # Si hay más de 40 caracteres digitales reales, es página digital nativa
        if len(texto_digital) > 40:
            texto_completo.append(
                f"--- [PÁGINA {num_pag} | TEXTO DIGITAL] ---\n{texto_digital}"
            )
        else:
            # Es página escaneada -> renderizar a 300 DPI y aplicar OCR con preprocesamiento
            pix = pagina.get_pixmap(dpi=300)
            modo = "RGBA" if pix.alpha else "RGB"
            img_pil = Image.frombytes(modo, [pix.width, pix.height], pix.samples)
            if modo == "RGBA":
                img_pil = img_pil.convert("RGB")

            img_mejorada = preprocesar_imagen_para_ocr(img_pil)
            texto_ocr = pytesseract.image_to_string(
                img_mejorada, lang="spa", config=config_tesseract
            ).strip()

            texto_completo.append(
                f"--- [PÁGINA {num_pag} | OCR ESCANEADO] ---\n{texto_ocr}"
            )

    doc.close()
    return total_paginas, "\n\n".join(texto_completo)


@app.route("/")
def index():
    # Obtener expedientes ordenados del más reciente al más antiguo
    res_exp = (
        supabase.table("expedientes")
        .select("*, usuarios(nombre)")
        .order("creado_en", desc=True)
        .execute()
    )
    expedientes = res_exp.data if res_exp.data else []

    # Obtener documentos para mostrarlos dentro de cada expediente
    res_docs = (
        supabase.table("documentos")
        .select("*, usuarios(nombre)")
        .order("creado_en", desc=True)
        .execute()
    )
    documentos = res_docs.data if res_docs.data else []

    # Obtener lista de usuarios para el selector de responsable
    res_users = supabase.table("usuarios").select("*").execute()
    usuarios = res_users.data if res_users.data else []

    return render_template(
        "index.html",
        expedientes=expedientes,
        documentos=documentos,
        usuarios=usuarios,
    )


@app.route("/expedientes/nuevo", methods=["POST"])
def crear_expediente():
    numero_expediente = request.form.get("numero_expediente", "").strip()
    cliente = request.form.get("cliente", "").strip()
    materia = request.form.get("materia", "").strip()
    fecha_apertura = request.form.get("fecha_apertura", "").strip()
    abogado_id = request.form.get("abogado_responsable_id")

    try:
        supabase.table("expedientes").insert(
            {
                "numero_expediente": numero_expediente,
                "cliente": cliente,
                "materia": materia,
                "fecha_apertura": fecha_apertura,
                "abogado_responsable_id": int(abogado_id) if abogado_id else None,
            }
        ).execute()
        flash(f"Expediente {numero_expediente} registrado correctamente.", "success")
    except Exception as e:
        flash(f"Error al registrar expediente: {str(e)}", "danger")

    return redirect(url_for("index"))


@app.route("/documentos/subir", methods=["POST"])
def subir_documento():
    expediente_id = request.form.get("expediente_id")
    autor_id = request.form.get("autor_id")
    archivo = request.files.get("archivo_pdf")

    if not archivo or archivo.filename == "":
        flash("Por favor selecciona un archivo PDF válido.", "warning")
        return redirect(url_for("index"))

    try:
        pdf_bytes = archivo.read()
        nombre_limpio = archivo.filename.replace(" ", "_")
        ruta_storage = f"exp_{expediente_id}_{int(time.time())}_{nombre_limpio}"

        # 1. Subir PDF a Supabase Storage (Bucket: expedientes-pdf)
        supabase.storage.from_("expedientes-pdf").upload(
            path=ruta_storage,
            file=pdf_bytes,
            file_options={"content-type": "application/pdf"},
        )
        archivo_url = supabase.storage.from_("expedientes-pdf").get_public_url(
            ruta_storage
        )

        # 2. Extraer texto (Híbrido Digital + OCR con preprocesamiento)
        total_paginas, texto_extraido = extraer_texto_pdf_hibrido(pdf_bytes)

        # 2.5. Calcular número de versión: si ya existe un documento con el mismo
        # nombre en este expediente, la nueva versión es la siguiente; si no, es v1.
        res_versiones = (
            supabase.table("documentos")
            .select("numero_version")
            .eq("expediente_id", int(expediente_id))
            .eq("nombre_archivo", archivo.filename)
            .order("numero_version", desc=True)
            .limit(1)
            .execute()
        )
        if res_versiones.data:
            numero_version = res_versiones.data[0]["numero_version"] + 1
        else:
            numero_version = 1

        # 3. Guardar registro y texto OCR en la tabla documentos
        supabase.table("documentos").insert(
            {
                "expediente_id": int(expediente_id),
                "nombre_archivo": archivo.filename,
                "archivo_url": archivo_url,
                "total_paginas": total_paginas,
                "texto_extraido": texto_extraido,
                "autor_id": int(autor_id) if autor_id else None,
                "numero_version": numero_version,
            }
        ).execute()

        flash(
            f"PDF '{archivo.filename}' ({total_paginas} págs.) subido y procesado con OCR exitosamente.",
            "success",
        )
    except Exception as e:
        flash(f"Error al procesar el PDF: {str(e)}", "danger")

    return redirect(url_for("index"))


@app.route("/expedientes/<int:expediente_id>/eliminar", methods=["POST"])
def eliminar_expediente(expediente_id):
    try:
        # 1. Borrar los archivos del expediente en Supabase Storage (best-effort)
        try:
            archivos = supabase.storage.from_("expedientes-pdf").list()
            prefijo = f"exp_{expediente_id}_"
            rutas_a_borrar = [
                a["name"] for a in archivos if a["name"].startswith(prefijo)
            ]
            if rutas_a_borrar:
                supabase.storage.from_("expedientes-pdf").remove(rutas_a_borrar)
        except Exception:
            pass  # si falla el borrado de Storage, igual seguimos con la BD

        # 2. Borrar los documentos asociados
        supabase.table("documentos").delete().eq(
            "expediente_id", expediente_id
        ).execute()

        # 3. Borrar el expediente
        supabase.table("expedientes").delete().eq("id", expediente_id).execute()

        flash("Expediente eliminado correctamente.", "success")
    except Exception as e:
        flash(f"Error al eliminar el expediente: {str(e)}", "danger")

    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(debug=True, port=5000)