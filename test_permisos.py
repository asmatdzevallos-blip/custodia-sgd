"""
Pruebas de permisos por rol (RF5): 4 acciones x 3 roles = 12 pruebas,
más algunas pruebas extra (reasignar, sin sesión).

Usan una base de datos FALSA en memoria: no leen ni modifican Supabase,
así que se pueden ejecutar todas las veces que se quiera.

Uso:
    python -m unittest test_permisos -v
"""
import io
import unittest

import app as m


# ---------------------------------------------------------------------------
# Supabase falso: imita solo los métodos que usa app.py
# ---------------------------------------------------------------------------
class Resultado:
    def __init__(self, data):
        self.data = data


class ConsultaFalsa:
    def __init__(self, db, tabla):
        self.db, self.tabla = db, tabla
        self.filtros, self.operacion, self.datos = [], "select", None
        self.columnas, self.limite = "*", None

    def select(self, columnas="*"):
        self.columnas = columnas
        return self

    def insert(self, fila):
        self.operacion, self.datos = "insert", fila
        return self

    def update(self, datos):
        self.operacion, self.datos = "update", datos
        return self

    def delete(self):
        self.operacion = "delete"
        return self

    def eq(self, col, valor):
        self.filtros.append(lambda f: f.get(col) == valor)
        return self

    def in_(self, col, valores):
        self.filtros.append(lambda f: f.get(col) in valores)
        return self

    def ilike(self, col, patron):
        texto = patron.strip("%").lower()
        self.filtros.append(lambda f: texto in str(f.get(col, "")).lower())
        return self

    def gte(self, col, valor):
        self.filtros.append(lambda f: str(f.get(col)) >= valor)
        return self

    def lte(self, col, valor):
        self.filtros.append(lambda f: str(f.get(col)) <= valor)
        return self

    def order(self, col, desc=False):
        return self

    def limit(self, n):
        self.limite = n
        return self

    def execute(self):
        filas = self.db[self.tabla]
        if self.operacion == "insert":
            fila = dict(self.datos, id=max([f["id"] for f in filas] or [0]) + 1)
            filas.append(fila)
            return Resultado([fila])

        elegidas = [f for f in filas if all(filtro(f) for filtro in self.filtros)]
        if self.operacion == "update":
            for f in elegidas:
                f.update(self.datos)
            return Resultado(elegidas)
        if self.operacion == "delete":
            self.db[self.tabla] = [f for f in filas if f not in elegidas]
            return Resultado(elegidas)

        if self.limite is not None:
            elegidas = elegidas[: self.limite]
        resultado = [dict(f) for f in elegidas]
        if "usuarios(nombre)" in self.columnas:  # imita el join de Supabase
            campo = "abogado_responsable_id" if self.tabla == "expedientes" else "autor_id"
            for f in resultado:
                u = next((u for u in self.db["usuarios"] if u["id"] == f.get(campo)), None)
                f["usuarios"] = {"nombre": u["nombre"]} if u else None
        return Resultado(resultado)


class StorageFalso:
    def __init__(self):
        self.subidos, self.borrados = [], []

    def from_(self, bucket):
        return self

    def upload(self, path, file, file_options=None):
        self.subidos.append(path)

    def get_public_url(self, path):
        return f"https://falso/storage/v1/object/public/expedientes-pdf/{path}"

    def create_signed_url(self, path, segundos):
        return {"signedURL": f"https://falso/firmado/{path}"}

    def remove(self, rutas):
        self.borrados.extend(rutas)


class SupabaseFalso:
    def __init__(self):
        self.storage = StorageFalso()
        self.db = {
            "usuarios": [
                {"id": 1, "nombre": "Titular", "rol": "titular"},
                {"id": 2, "nombre": "Abogado", "rol": "abogado"},
                {"id": 3, "nombre": "Asistente", "rol": "asistente"},
            ],
            "expedientes": [
                # 10 es del titular, 20 es del abogado a cargo
                {"id": 10, "numero_expediente": "EXP-DEL-TITULAR", "cliente": "A",
                 "materia": "Civil", "fecha_apertura": "2026-01-01",
                 "abogado_responsable_id": 1, "estado": "abierto", "creado_en": "1"},
                {"id": 20, "numero_expediente": "EXP-DEL-ABOGADO", "cliente": "B",
                 "materia": "Penal", "fecha_apertura": "2026-02-01",
                 "abogado_responsable_id": 2, "estado": "abierto", "creado_en": "2"},
            ],
            "documentos": [
                {"id": 100, "expediente_id": 10, "nombre_archivo": "a.pdf",
                 "archivo_url": "https://falso/storage/v1/object/public/expedientes-pdf/exp_10_a.pdf",
                 "numero_version": 1, "total_paginas": 1, "texto_extraido": "", "autor_id": 1, "creado_en": "1"},
                {"id": 200, "expediente_id": 20, "nombre_archivo": "b.pdf",
                 "archivo_url": "https://falso/storage/v1/object/public/expedientes-pdf/exp_20_b.pdf",
                 "numero_version": 1, "total_paginas": 1, "texto_extraido": "", "autor_id": 2, "creado_en": "2"},
            ],
        }

    def table(self, nombre):
        return ConsultaFalsa(self.db, nombre)


# ---------------------------------------------------------------------------
# Pruebas
# ---------------------------------------------------------------------------
USUARIOS = {
    "titular": (1, "Titular"),
    "abogado": (2, "Abogado"),
    "asistente": (3, "Asistente"),
}


class PruebasPermisos(unittest.TestCase):
    def setUp(self):
        self.falso = SupabaseFalso()
        self._original = m.supabase, m.extraer_texto_pdf_hibrido
        m.supabase = self.falso
        m.extraer_texto_pdf_hibrido = lambda pdf_bytes: (1, "texto de prueba")
        m.app.config["TESTING"] = True
        self.cliente = m.app.test_client()

    def tearDown(self):
        m.supabase, m.extraer_texto_pdf_hibrido = self._original

    # -- utilidades --
    def entrar_como(self, rol):
        uid, nombre = USUARIOS[rol]
        with self.cliente.session_transaction() as s:
            s.update(usuario_id=uid, nombre=nombre, rol=rol)

    def expedientes(self):
        return {e["id"]: e for e in self.falso.db["expedientes"]}

    def subir_pdf(self, expediente_id):
        return self.cliente.post("/documentos/subir", data={
            "expediente_id": str(expediente_id),
            "archivo_pdf": (io.BytesIO(b"%PDF-1.4 falso"), "nuevo.pdf"),
        }, content_type="multipart/form-data")

    def docs_de(self, expediente_id):
        return [d for d in self.falso.db["documentos"] if d["expediente_id"] == expediente_id]

    def registrar(self, responsable_id):
        return self.cliente.post("/expedientes/nuevo", data={
            "numero_expediente": "NUEVO-1", "cliente": "C", "materia": "Civil",
            "fecha_apertura": "2026-03-01", "abogado_responsable_id": str(responsable_id),
        })

    def nuevo(self):
        return next((e for e in self.falso.db["expedientes"] if e["numero_expediente"] == "NUEVO-1"), None)

    # ===== 1-3. VER expedientes y PDF =====
    def test_01_titular_ve_todos(self):
        self.entrar_como("titular")
        html = self.cliente.get("/").get_data(as_text=True)
        self.assertIn("EXP-DEL-TITULAR", html)
        self.assertIn("EXP-DEL-ABOGADO", html)
        self.assertIn("/firmado/", self.cliente.get("/documentos/200/pdf").location)

    def test_02_asistente_ve_todos(self):
        self.entrar_como("asistente")
        html = self.cliente.get("/").get_data(as_text=True)
        self.assertIn("EXP-DEL-TITULAR", html)
        self.assertIn("EXP-DEL-ABOGADO", html)
        self.assertIn("/firmado/", self.cliente.get("/documentos/100/pdf").location)

    def test_03_abogado_solo_ve_los_suyos(self):
        self.entrar_como("abogado")
        html = self.cliente.get("/").get_data(as_text=True)
        self.assertIn("EXP-DEL-ABOGADO", html)
        self.assertNotIn("EXP-DEL-TITULAR", html)
        # Tampoco puede abrir el PDF ajeno escribiendo la URL a mano
        self.assertNotIn("/firmado/", self.cliente.get("/documentos/100/pdf").location)
        self.assertIn("/firmado/", self.cliente.get("/documentos/200/pdf").location)

    # ===== 4-6. REGISTRAR expedientes =====
    def test_04_titular_registra_y_elige_responsable(self):
        self.entrar_como("titular")
        self.registrar(responsable_id=2)
        self.assertEqual(self.nuevo()["abogado_responsable_id"], 2)

    def test_05_asistente_registra_pero_no_puede_asignarse(self):
        self.entrar_como("asistente")
        self.registrar(responsable_id=3)  # el asistente no puede ser responsable
        self.assertIsNone(self.nuevo())
        self.registrar(responsable_id=1)
        self.assertEqual(self.nuevo()["abogado_responsable_id"], 1)

    def test_06_abogado_registra_y_queda_como_responsable(self):
        self.entrar_como("abogado")
        self.registrar(responsable_id=1)  # aunque intente asignarlo a otro
        self.assertEqual(self.nuevo()["abogado_responsable_id"], 2)

    # ===== 7-9. SUBIR PDF =====
    def test_07_titular_sube_a_cualquiera(self):
        self.entrar_como("titular")
        self.subir_pdf(20)
        self.assertEqual(len(self.docs_de(20)), 2)

    def test_08_asistente_sube_a_cualquiera(self):
        self.entrar_como("asistente")
        self.subir_pdf(10)
        self.assertEqual(len(self.docs_de(10)), 2)

    def test_09_abogado_solo_sube_a_los_suyos(self):
        self.entrar_como("abogado")
        self.subir_pdf(10)
        self.assertEqual(len(self.docs_de(10)), 1)  # no se agregó nada
        self.assertEqual(self.falso.storage.subidos, [])
        self.subir_pdf(20)
        self.assertEqual(len(self.docs_de(20)), 2)

    # ===== 10-12. ELIMINAR =====
    def test_10_titular_elimina(self):
        self.entrar_como("titular")
        self.cliente.post("/expedientes/20/eliminar")
        self.assertNotIn(20, self.expedientes())
        self.assertEqual(self.docs_de(20), [])
        self.assertEqual(self.falso.storage.borrados, ["exp_20_b.pdf"])

    def test_11_asistente_no_elimina(self):
        self.entrar_como("asistente")
        self.cliente.post("/expedientes/10/eliminar")
        self.assertIn(10, self.expedientes())

    def test_12_abogado_no_elimina_ni_los_suyos(self):
        self.entrar_como("abogado")
        self.cliente.post("/expedientes/20/eliminar")
        self.assertIn(20, self.expedientes())

    # ===== Extra =====
    def test_extra_solo_titular_reasigna(self):
        for rol in ("asistente", "abogado"):
            self.entrar_como(rol)
            self.cliente.post("/expedientes/10/reasignar", data={"abogado_responsable_id": "2"})
            self.assertEqual(self.expedientes()[10]["abogado_responsable_id"], 1)
        self.entrar_como("titular")
        self.cliente.post("/expedientes/10/reasignar", data={"abogado_responsable_id": "2"})
        self.assertEqual(self.expedientes()[10]["abogado_responsable_id"], 2)

    def test_extra_sin_sesion_no_accede_a_nada(self):
        for metodo, url in [("get", "/"), ("get", "/documentos/100/pdf"),
                            ("post", "/expedientes/nuevo"), ("post", "/documentos/subir"),
                            ("post", "/expedientes/10/eliminar"), ("post", "/expedientes/10/reasignar")]:
            r = getattr(self.cliente, metodo)(url)
            self.assertTrue(r.location.endswith("/login"), url)
        self.assertEqual(len(self.expedientes()), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
