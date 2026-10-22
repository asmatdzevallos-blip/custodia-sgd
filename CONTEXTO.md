# CUSTODIA (SGD-EJ)

Sistema web de gestión documental para un estudio jurídico pequeño de Jaén (2 abogados y 1 asistente jurídico, ~300 expedientes en papel). Proyecto del curso Gestión de Proyectos TI (UNMSM, Grupo 05). Centraliza los expedientes con metadatos, búsqueda (por metadatos y por contenido), control de acceso por roles y trazabilidad.

## Stack y cómo correrlo
- Python 3.11, Flask. Base de datos y almacenamiento en Supabase (tablas `expedientes`, `documentos`, `usuarios`; bucket `expedientes-pdf`).
- Texto de los PDF: PyMuPDF para PDF con texto y Tesseract OCR (idioma `spa`) con preprocesamiento OpenCV para escaneos.
- Despliegue: Docker + gunicorn en Render.
- Local (Windows): `python app.py`
- Variables en `.env` (nunca se suben al repo): `SUPABASE_URL`, `SUPABASE_KEY`, `FLASK_SECRET_KEY`.

## Requisitos (qué debe hacer el sistema)
Prioridad: todos Imprescindibles, salvo RF7 (Importante).

| ID | Requisito | Criterio de aceptación | Estado (22/10) |
|---|---|---|---|
| RF1 | Registrar expedientes con cliente, materia, N.º de expediente y fecha | Se registra con los 4 metadatos y aparece en el listado | Funcionando |
| RF2 | Cargar PDF asociados a un expediente, con texto consultable | PDF de la muestra asociados y con texto | Funcionando |
| RF3 | Buscar por cliente, materia, N.º de expediente y rango de fechas | Cada criterio devuelve los expedientes correctos | Pendiente |
| RF4 | Buscar por contenido con consultas en lenguaje natural (embeddings + pgvector) | ≥ 80% de aciertos en una batería de consultas jurídicas | Pendiente |
| RF5 | Restringir acceso por rol: abogado titular, abogado a cargo, asistente jurídico | 12 de 12 pruebas de permisos aprobadas | Pendiente |
| RF6 | Historial de versiones (autor, fecha, número) y auditoría de accesos y cambios | Historial en el 100% de los documentos en revisión | En desarrollo |
| RF7 | Enviar un documento a revisión y registrar aprobación o rechazo con fecha y comentario | El documento queda con estado, fecha, comentario y resultado | Pendiente |
| RNF1 | Acceso autenticado con contraseñas protegidas | Sin sesión no se accede a ningún documento; contraseñas con hash | Pendiente |
| RNF2 | Respaldo automático | Respaldo automático y restauración de prueba exitosa | Pendiente |
| RNF3 | Uso desde el navegador, sin instalar software | Funciona en el navegador de los equipos del estudio | Funcionando |

## Sprint actual
- **Sprint 2 (08/10 al 28/10/2026, Hito 5):** RF3 (búsqueda por metadatos), RF5 (roles y permisos, con RNF1) y avance de la digitalización. La fecha de cierre no cambia.
- Siguientes: Sprint 3 (29/10 al 18/11): RF6, RF7, RF4 (búsqueda semántica) y respaldo (RNF2). Luego capacitación y piloto.

## Reglas del proyecto
- Solo datos **anonimizados o ficticios**: nunca expedientes reales con datos personales (Ley N.º 29733).
- Solo herramientas gratuitas o de nivel gratuito (tope operativo de S/ 305). No proponer servicios de pago.
- Alcance: no incluye firma digital, integraciones con el Poder Judicial o SUNARP, ni compra de hardware.

## Reglas de trabajo
- Una rama por tarea (por ejemplo `sprint2-rf3`) y commits pequeños con mensaje descriptivo.
- No escribir claves, contraseñas ni secretos en el código ni en el repo. Usar variables de entorno.
- Antes de dar una tarea por terminada, ejecutar la app y probar el criterio de aceptación del requisito.
- Hacer cambios mínimos y pedir confirmación antes de reestructurar o borrar archivos.
- Responder en español.
