import os
import time
from datetime import date
from functools import wraps
import fitz  # PyMuPDF
import pytesseract
import cv2
import numpy as np
from PIL import Image
from flask import Flask, render_template, request, redirect, url_for, flash, session
from werkzeug.security import check_password_hash
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
# Si falta la variable, la app no arranca (mejor que fallar más tarde al usar la sesión)
app.secret_key = os.environ["FLASK_SECRET_KEY"]
# La cookie de sesión no se envía en peticiones originadas desde otros sitios
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"

# Conexión a Supabase
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)


def login_required(vista):
    """
    Decorador para rutas protegidas (RNF1): si no hay sesión iniciada,
    redirige al login en lugar de ejecutar la vista.
    """
    @wraps(vista)
    def envoltura(*args, **kwargs):
        if "usuario_id" not in session:
            flash("Inicia sesión para continuar.", "warning")
            return redirect(url_for("login"))
        return vista(*args, **kwargs)

    return envoltura


# ---- Roles y permisos (RF5) ----
# titular:   ve todo, hace todo; único que elimina y reasigna
# asistente: ve todo, registra expedientes y sube PDF
# abogado:   solo ve sus expedientes (abogado_responsable_id) y sube PDF a ellos
ROLES_RESPONSABLES = ["titular", "abogado"]  # roles que pueden llevar un expediente


def requiere_rol(*roles):
    """
    Decorador: solo deja pasar a los roles indicados. Va debajo de @login_required.
    Ej.: @requiere_rol("titular")
    """
    def decorador(vista):
        @wraps(vista)
        def envoltura(*args, **kwargs):
            if session.get("rol") not in roles:
                flash("No tienes permiso para realizar esta acción.", "danger")
                return redirect(url_for("index"))
            return vista(*args, **kwargs)

        return envoltura

    return decorador


def ve_todos_los_expedientes():
    return session.get("rol") in ("titular", "asistente")


def obtener_expediente_permitido(expediente_id):
    """
    Devuelve el expediente si existe y el usuario puede verlo; si no, None.
    Para el usuario no hay diferencia entre "no existe" y "no es tuyo".
    """
    res = (
        supabase.table("expedientes")
        .select("id, numero_expediente, abogado_responsable_id")
        .eq("id", expediente_id)
        .limit(1)
        .execute()
    )
    expediente = res.data[0] if res.data else None
    if expediente is None:
        return None
    if ve_todos_los_expedientes():
        return expediente
    if expediente["abogado_responsable_id"] == session.get("usuario_id"):
        return expediente
    return None


def id_responsable_valido(valor):
    """
    Devuelve el id si corresponde a un usuario que puede ser responsable
    (titular o abogado); si no, None. Evita asignar al asistente o ids inventados.
    """
    if not valor or not str(valor).isdigit():
        return None
    res = (
        supabase.table("usuarios")
        .select("id")
        .eq("id", int(valor))
        .in_("rol", ROLES_RESPONSABLES)
        .limit(1)
        .execute()
    )
    return res.data[0]["id"] if res.data else None


def ruta_en_storage(archivo_url):
    """
    Extrae la ruta dentro del bucket a partir de la URL guardada en la BD.
    Ej.: .../object/public/expedientes-pdf/exp_1_123_doc.pdf -> exp_1_123_doc.pdf
    """
    return archivo_url.split("/expedientes-pdf/", 1)[-1]


def fecha_valida(texto):
    """Devuelve la fecha en formato AAAA-MM-DD si es válida; si no, cadena vacía."""
    try:
        return date.fromisoformat(texto.strip()).isoformat()
    except ValueError:
        return ""


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


@app.route("/login", methods=["GET", "POST"])
def login():
    if "usuario_id" in session:
        return redirect(url_for("index"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        res = (
            supabase.table("usuarios")
            .select("id, nombre, rol, password_hash")
            .eq("email", email)
            .limit(1)
            .execute()
        )
        usuario = res.data[0] if res.data else None

        # Mismo mensaje si el correo no existe o si la contraseña es incorrecta,
        # para no revelar qué correos están registrados.
        if usuario and check_password_hash(usuario["password_hash"] or "", password):
            session.clear()
            session["usuario_id"] = usuario["id"]
            session["nombre"] = usuario["nombre"]
            session["rol"] = usuario["rol"]
            return redirect(url_for("index"))

        flash("Correo o contraseña incorrectos.", "danger")

    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    flash("Sesión cerrada.", "success")
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    # Filtros de búsqueda (RF3): llegan por la URL, ej. /?cliente=perez&materia=Civil
    filtros = {
        "numero": request.args.get("numero", "").strip(),
        "cliente": request.args.get("cliente", "").strip(),
        "materia": request.args.get("materia", "").strip(),
        "desde": fecha_valida(request.args.get("desde", "")),
        "hasta": fecha_valida(request.args.get("hasta", "")),
    }
    hay_filtros = any(filtros.values())

    if filtros["desde"] and filtros["hasta"] and filtros["desde"] > filtros["hasta"]:
        flash("La fecha 'desde' es posterior a la fecha 'hasta'.", "warning")

    # Obtener expedientes ordenados del más reciente al más antiguo
    consulta = supabase.table("expedientes").select("*, usuarios(nombre)")
    # RF5: el abogado a cargo solo ve los expedientes de los que es responsable
    if not ve_todos_los_expedientes():
        consulta = consulta.eq("abogado_responsable_id", session["usuario_id"])
    # ilike = coincidencia parcial sin distinguir mayúsculas ("pere" encuentra "Pérez")
    if filtros["numero"]:
        consulta = consulta.ilike("numero_expediente", f"%{filtros['numero']}%")
    if filtros["cliente"]:
        consulta = consulta.ilike("cliente", f"%{filtros['cliente']}%")
    if filtros["materia"]:
        consulta = consulta.eq("materia", filtros["materia"])
    if filtros["desde"]:
        consulta = consulta.gte("fecha_apertura", filtros["desde"])
    if filtros["hasta"]:
        consulta = consulta.lte("fecha_apertura", filtros["hasta"])

    res_exp = consulta.order("creado_en", desc=True).execute()
    expedientes = res_exp.data if res_exp.data else []

    # Obtener solo los documentos de los expedientes mostrados
    documentos = []
    if expedientes:
        res_docs = (
            supabase.table("documentos")
            .select("*, usuarios(nombre)")
            .in_("expediente_id", [exp["id"] for exp in expedientes])
            .order("creado_en", desc=True)
            .execute()
        )
        documentos = res_docs.data if res_docs.data else []

    # Obtener lista de posibles responsables (titular y abogados) para los selectores
    # Solo las columnas necesarias: nunca enviar password_hash a la plantilla
    res_users = (
        supabase.table("usuarios")
        .select("id, nombre, rol")
        .in_("rol", ROLES_RESPONSABLES)
        .execute()
    )
    usuarios = res_users.data if res_users.data else []

    return render_template(
        "index.html",
        expedientes=expedientes,
        documentos=documentos,
        usuarios=usuarios,
        filtros=filtros,
        hay_filtros=hay_filtros,
    )


@app.route("/expedientes/nuevo", methods=["POST"])
@login_required
def crear_expediente():
    numero_expediente = request.form.get("numero_expediente", "").strip()
    cliente = request.form.get("cliente", "").strip()
    materia = request.form.get("materia", "").strip()
    fecha_apertura = request.form.get("fecha_apertura", "").strip()

    try:
        if session["rol"] == "abogado":
            # RF5: el abogado a cargo queda como responsable de lo que registra
            abogado_id = session["usuario_id"]
        else:
            abogado_id = id_responsable_valido(request.form.get("abogado_responsable_id"))
            if abogado_id is None:
                flash("Selecciona un abogado responsable válido.", "warning")
                return redirect(url_for("index"))

        supabase.table("expedientes").insert(
            {
                "numero_expediente": numero_expediente,
                "cliente": cliente,
                "materia": materia,
                "fecha_apertura": fecha_apertura,
                "abogado_responsable_id": abogado_id,
            }
        ).execute()
        flash(f"Expediente {numero_expediente} registrado correctamente.", "success")
    except Exception as e:
        flash(f"Error al registrar expediente: {str(e)}", "danger")

    return redirect(url_for("index"))


@app.route("/documentos/subir", methods=["POST"])
@login_required
def subir_documento():
    expediente_id = request.form.get("expediente_id")
    # El autor es quien tiene la sesión iniciada, no un valor elegido en el formulario
    autor_id = session["usuario_id"]
    archivo = request.files.get("archivo_pdf")

    # RF5: solo se sube a expedientes que el usuario puede ver
    if not expediente_id or not expediente_id.isdigit() or obtener_expediente_permitido(int(expediente_id)) is None:
        flash("No tienes permiso para subir documentos a ese expediente.", "danger")
        return redirect(url_for("index"))

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
        # Se guarda con el formato de URL de siempre; con el bucket privado esta URL
        # ya no abre el PDF: se accede por /documentos/<id>/pdf (ver ver_pdf)
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
                "autor_id": autor_id,
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


@app.route("/documentos/<int:doc_id>/pdf")
@login_required
def ver_pdf(doc_id):
    """
    RF5: el bucket es privado. Se revisa el permiso y se redirige a un enlace
    firmado que expira en 5 minutos, así copiar el enlace no da acceso permanente.
    """
    res = (
        supabase.table("documentos")
        .select("expediente_id, archivo_url")
        .eq("id", doc_id)
        .limit(1)
        .execute()
    )
    documento = res.data[0] if res.data else None
    if documento is None or obtener_expediente_permitido(documento["expediente_id"]) is None:
        flash("No tienes permiso para ver ese documento.", "danger")
        return redirect(url_for("index"))

    try:
        firmado = supabase.storage.from_("expedientes-pdf").create_signed_url(
            ruta_en_storage(documento["archivo_url"]), 300
        )
        return redirect(firmado.get("signedURL") or firmado["signedUrl"])
    except Exception as e:
        flash(f"No se pudo abrir el PDF: {str(e)}", "danger")
        return redirect(url_for("index"))


@app.route("/expedientes/<int:expediente_id>/reasignar", methods=["POST"])
@login_required
@requiere_rol("titular")
def reasignar_expediente(expediente_id):
    abogado_id = id_responsable_valido(request.form.get("abogado_responsable_id"))
    if abogado_id is None:
        flash("Selecciona un abogado responsable válido.", "warning")
        return redirect(url_for("index"))

    try:
        supabase.table("expedientes").update(
            {"abogado_responsable_id": abogado_id}
        ).eq("id", expediente_id).execute()
        flash("Responsable del expediente actualizado.", "success")
    except Exception as e:
        flash(f"Error al reasignar el expediente: {str(e)}", "danger")

    return redirect(url_for("index"))


@app.route("/expedientes/<int:expediente_id>/eliminar", methods=["POST"])
@login_required
@requiere_rol("titular")
def eliminar_expediente(expediente_id):
    try:
        # 1. Borrar los PDF del expediente en Supabase Storage (best-effort).
        # Las rutas salen de la tabla documentos (list() del bucket solo devuelve 100).
        try:
            res_docs = (
                supabase.table("documentos")
                .select("archivo_url")
                .eq("expediente_id", expediente_id)
                .execute()
            )
            rutas_a_borrar = [ruta_en_storage(d["archivo_url"]) for d in res_docs.data or []]
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