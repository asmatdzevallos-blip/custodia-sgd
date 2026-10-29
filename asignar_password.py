"""
Asigna (o cambia) la contraseña de un usuario existente.
Guarda solo el hash en Supabase; la contraseña nunca queda escrita en ningún archivo.

Uso:
    python asignar_password.py titular@custodia.pe
"""
import os
import sys
from getpass import getpass
from werkzeug.security import generate_password_hash
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()

if len(sys.argv) != 2:
    print("Uso: python asignar_password.py <correo>")
    sys.exit(1)

email = sys.argv[1].strip().lower()
supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])

res = supabase.table("usuarios").select("id, nombre").eq("email", email).execute()
if not res.data:
    print(f"No existe un usuario con el correo {email}.")
    sys.exit(1)

# getpass no muestra lo que se escribe en la terminal
password = getpass("Nueva contraseña (mín. 8 caracteres): ")
if len(password) < 8:
    print("La contraseña debe tener al menos 8 caracteres.")
    sys.exit(1)
if password != getpass("Repite la contraseña: "):
    print("Las contraseñas no coinciden.")
    sys.exit(1)

supabase.table("usuarios").update(
    {"password_hash": generate_password_hash(password)}
).eq("email", email).execute()

print(f"Contraseña actualizada para {res.data[0]['nombre']}.")
