# Guía de QA · Backend InnovaLab

Guía para probar la API del backend: qué hace cada endpoint, cómo probarlo, qué respuesta esperar y cómo hacer pruebas de estrés sin tirar el servicio ni gastar la cuota de IA del equipo.

> Estado: backend en `main` al 15/10/2026 (panel `/admin` con login de GitHub vía Clerk, #81 y #83; exportación del material y del informe, #40). Si algo de esta guía no coincide con lo que ves, la referencia es la documentación de la API: [Swagger](https://innovalab-educacion-e18-test.up.railway.app/docs) y [ReDoc](https://innovalab-educacion-e18-test.up.railway.app/redoc). Avisá al equipo de Backend de la diferencia.

---

## Índice

0. [Reglas de oro (leer antes de empezar)](#0-reglas-de-oro-leer-antes-de-empezar)
1. [Entornos y accesos](#1-entornos-y-accesos)
2. [Conceptos que hay que conocer](#2-conceptos-que-hay-que-conocer)
3. [Datos de prueba](#3-datos-de-prueba)
4. [Endpoints](#4-endpoints)
5. [Flujos de punta a punta](#5-flujos-de-punta-a-punta)
6. [Códigos de respuesta](#6-códigos-de-respuesta)
7. [Pruebas de estrés de la API](#7-pruebas-de-estrés-de-la-api)
8. [Pruebas de los límites de la IA](#8-pruebas-de-los-límites-de-la-ia)
9. [Cómo reportar un bug](#9-cómo-reportar-un-bug)
10. [Problemas conocidos](#10-problemas-conocidos)

---

## 0. Reglas de oro (leer antes de empezar)

El backend corre en **una sola instancia** en Railway, con una cuota de IA **gratuita y compartida** por todo el equipo (Frontend, Diseño, Backend y la demo). Para no romper nada:

1. **Usá siempre `?mock=true`** en los endpoints de IA (`/analysis/*` y `POST /adaptations`). El modo mock ejecuta todo lo real (extracción, reglas, validaciones, base de datos) y solo simula la respuesta de la IA: 0 tokens y sin límite de uso. La IA real queda para pocas pruebas puntuales y coordinadas.
2. **Nunca hagas pruebas de estrés contra producción.** Usá el backend local (sección 1.3) o una instancia de staging que te levante Backend. Una prueba de carga en producción deja sin servicio a todo el equipo.
3. **No uses datos reales de estudiantes.** Inventá nombres, DNI y notas. El backend detecta datos personales y los bloquea, y lo vamos a probar con datos ficticios.
4. **No subas archivos grandes en paralelo contra producción.** Cada archivo pesado (cerca de 10 MB) puede ocupar unos 60 MB de memoria mientras se procesa; muchos a la vez pueden reiniciar el servidor (ver 7.4).
5. **En `/admin` de producción, mirá pero no modifiques.** Con rol de solo lectura el panel no te deja cambiar nada; si tenés rol admin, no edites ni borres conexiones salvo que lo coordines con Backend: borrar o deshabilitar una conexión deja sin IA a todos.
6. **`POST /ai/test-connection` siempre usa IA real** (no tiene modo mock). Usalo solo para verificar que la IA responde, no en loops.
7. **Si ves 429 o 503 de la IA, frená y avisá.** Insistir agota la cuota diaria.
8. **Antes de una prueba de estrés o de IA real, avisá en el canal de Backend** con día y hora.

---

## 1. Entornos y accesos

### 1.1 URLs

| Qué | Dónde | Notas |
| :--- | :--- | :--- |
| API (producción) | <https://innovalab-educacion-e18-test.up.railway.app/api/v1> | En los ejemplos se usa la variable `$API`. |
| Swagger (pruebas interactivas) | <https://innovalab-educacion-e18-test.up.railway.app/docs> | Pide iniciar sesión en el panel; después de entrar vuelve sola a Swagger. |
| ReDoc (documentación de lectura) | <https://innovalab-educacion-e18-test.up.railway.app/redoc> | Igual que Swagger. |
| Web de pruebas | <https://innovalab-web-pruebas.onrender.com/> | Pide el mismo login. Recorre el flujo completo con botones y muestra cada request con su `curl`. Modo mock activado por defecto. |
| Panel de administración | <https://innovalab-educacion-e18-test.up.railway.app/admin> | Métricas de uso de la IA, conexiones, uso del producto y diagramas. |

> **Acceso:** el panel, Swagger, ReDoc y la web de pruebas se abren con **tu cuenta de GitHub** (botón *Continue with GitHub* de Clerk). Entran las cuentas que Backend habilitó en la pestaña **Usuarios** del panel:
>
> | Rol | Qué permite |
> | :--- | :--- |
> | Admin (Backend) | Todo: conexiones de IA, usuarios y la web de pruebas con IA real |
> | Solo lectura (resto del equipo) | Ver el panel completo (menos Usuarios), Swagger, ReDoc y usar la web de pruebas **solo en modo mock** |
>
> Si tu cuenta no tiene acceso, vas a ver "La cuenta de GitHub @usuario no tiene acceso": pedíselo al **equipo de Backend**. La sesión dura 8 horas.
>
> **La API (`/api/v1/...`) no pide credenciales** desde curl, Postman o Bruno: el login solo aplica a las páginas de arriba (y a la web de pruebas, que el backend reconoce por su origen).

Para los ejemplos con `curl`:

```bash
export API="https://innovalab-educacion-e18-test.up.railway.app/api/v1"     # producción (solo pruebas funcionales, en mock)
# export API="http://127.0.0.1:8000/api/v1"                     # backend local (estrés y pruebas destructivas)
```

### 1.2 Herramientas recomendadas

- **Web de pruebas**: lo más rápido para pruebas funcionales y exploratorias. Cada llamada queda en el "Registro de requests" con la respuesta y el `curl` equivalente, listo para pegar en un bug.
- **Swagger** (`/docs`): para probar un endpoint puntual con parámetros a mano (botón *Try it out*).
- **ReDoc** (`/redoc`): la misma documentación en formato de lectura, más cómoda para consultar qué recibe y qué devuelve cada endpoint, con todos los campos y sus descripciones. Es la referencia cuando esta guía no alcanza.
- **curl / Postman / Bruno**: para casos de error y para armar colecciones. No necesitan credenciales. Para importar la colección desde OpenAPI: con el backend local, `http://127.0.0.1:8000/api/v1/openapi.json` (abierto en desarrollo); en producción, iniciá sesión en `/admin`, abrí `/api/v1/openapi.json` en el navegador y guardá el archivo.
- **Locust** o **k6**: pruebas de estrés (sección 7).

### 1.3 Backend local (para estrés y pruebas destructivas)

Requiere Python 3.12 o superior.

```bash
git clone https://github.com/e18-innovalab/contenidos_accesibles.git
cd contenidos_accesibles/backend
python -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements-dev.txt

# Variables mínimas (sin AI_API_KEY no hay IA real: todo en mock)
export ADMIN_PASSWORD=admin-local     # panel local: usuario "admin" y esta contraseña
export ADMIN_AUTH=local               # en local se entra solo con usuario y contraseña, sin Clerk
export DB_ENCRYPTION_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

uvicorn app.main:app --port 8000
```

Swagger local: <http://127.0.0.1:8000/docs> (en desarrollo no pide login). Panel local: <http://127.0.0.1:8000/admin> con `admin` / `admin-local`.

Los tests automáticos del backend también se pueden correr con `pytest` desde `backend/`; son los mismos que corre el CI en cada PR.

---

## 2. Conceptos que hay que conocer

### 2.1 Modo mock (`?mock=true`)

| | Mock (`?mock=true`) | IA real |
| :--- | :--- | :--- |
| Extracción de texto, imágenes y estructura | Real | Real |
| Motor de reglas (oraciones largas, consignas, legibilidad, imágenes) | Real | Real |
| Detección de datos personales | Real | Real |
| Diagnóstico de la IA y propuestas de adaptación | Simulado (determinístico) | Modelo de IA |
| Se guarda en la base y genera `analysis_id` | Sí | Sí |
| Cuenta para el rate limit | **No** | Sí |
| Tokens | 0 | Sí |

Las respuestas mock se reconocen por `metadata.provider = "mock"` y textos que empiezan con `[MOCK]`.

### 2.2 Diagnóstico, barreras y vencimiento

- Cada análisis devuelve un `analysis_id` (UUID) y `expires_at`: el diagnóstico y sus propuestas **vencen a las 24 h**. Después responden 404.
- `barriers[]`: cada barrera tiene `id` (`bar-1`, `bar-2`…), `category` (`comprension`, `estructura`, `accesibilidad_visual`), `severity` (`para_revisar`, `barrera_detectada`), `original_text` (el fragmento citado), `source` (`ai` o `rules`) y `fragment_found` (si la cita se encontró textual en el material).
- Hay barreras **globales** que no citan texto (por ejemplo `[Evaluación global de legibilidad: …]` o `[3 imagen(es) en el documento]`): para adaptarlas hay que mandar el fragmento a mano.
- `estimated_score` (0 a 100) combina IA (70 %) y reglas (30 %); el detalle está en `score_breakdown`.

### 2.3 Estados de una propuesta de adaptación

```
pendiente ──aceptar──▶ aceptada  ──deshacer──▶ pendiente
pendiente ──editar───▶ editada   ──deshacer──▶ pendiente
pendiente ──descartar▶ descartada ──deshacer──▶ pendiente
```

- Solo se decide sobre una propuesta `pendiente`; si ya tiene decisión responde **409** (hay que deshacer primero).
- Deshacer una propuesta `pendiente` responde **409**.
- El texto original del material **nunca se modifica**.

### 2.4 Datos personales (respuesta 422 especial)

Antes de enviar algo a la IA, el backend busca datos personales: **nombres con rótulo** (`Alumno:`, `Estudiante:`, `Nombre y apellido:`…), **DNI**, **CUIL/CUIT** y **calificaciones** (`Nota:`, `Calificación:`). Si encuentra alguno, no envía nada a la IA, no guarda nada y responde:

```json
{
  "detail": {
    "code": "sensitive_data_detected",
    "message": "El material contiene posibles datos personales ...",
    "total": 2,
    "findings": [
      {"id": "nombre_rotulado-8", "type": "nombre_rotulado", "label": "Nombre de una persona", "masked": "Alumno: J*** P***", "line": 1},
      {"id": "dni-24", "type": "dni", "label": "DNI", "masked": "DNI: 3*.***.**6", "line": 2}
    ]
  }
}
```

Para seguir igual (si no son datos personales), se reenvía lo mismo con **todos** los `id` descartados: `?dismissed=nombre_rotulado-8&dismissed=dni-24`. Si falta descartar alguno, vuelve a responder 422 con los restantes. La respuesta **nunca** repite el dato completo.

### 2.5 Rate limit de la IA

Los endpoints que usan IA real tienen límites **en memoria** (se reinician con cada deploy):

| Límite | Valor por defecto |
| :--- | :--- |
| Por IP, por minuto | 5 |
| Por IP, por día | 50 |
| Global, por minuto | 20 |
| Global, por día | 500 |

Al superarlo: **429** con header `Retry-After` (segundos). Las llamadas con `?mock=true` **no cuentan**. Endpoints limitados: `POST /analysis/sample`, `/analysis/pdf`, `/analysis/file`, `POST /adaptations` y `POST /ai/test-connection` (y `/ai/test-gemini`).

### 2.6 Límites de entrada

| Entrada | Límite |
| :--- | :--- |
| Archivos (`.pdf`, `.docx`, `.pptx`) | 10 MB (más: **413**) |
| Texto en `/analysis/sample` | 10 a 20.000 caracteres |
| Texto en `/documents/process-text` | 10 a 50.000 caracteres |
| Fragmento manual en `POST /adaptations` | hasta 6.000 caracteres |
| Texto que se envía a la IA | primeras 3.000 palabras (`is_truncated = true` si se recorta) |
| Imágenes con texto alternativo propuesto | hasta 5 por documento |

---

## 3. Datos de prueba

### 3.1 Textos

**Material con barreras** (oración muy larga, consigna con muchas acciones y referencia visual):

```text
Unidad 3: El ciclo del agua

El ciclo del agua, también conocido como ciclo hidrológico, es el proceso de circulación del agua entre los distintos compartimentos de la hidrosfera, en el cual se producen transformaciones de estado físico que dependen de la temperatura y la presión, y que permiten que el agua pase de la superficie terrestre a la atmósfera mediante la evaporación y la transpiración de las plantas, para luego condensarse formando nubes y regresar a la superficie en forma de precipitación.

Actividad 1: Leé el texto de la página 8, subrayá las ideas principales, armá un cuadro comparativo entre evaporación y condensación, respondé las preguntas del final y entregá todo en una hoja aparte antes del viernes.

Observá la imagen de la derecha y completá el esquema.
```

**Material "limpio"** (pocas barreras): `Actividad 1: Leé el texto sobre el ciclo del agua. Después respondé las preguntas de la guía con tus palabras.`

**Datos personales ficticios** (deben dar 422):

```text
Alumno: Juan Pérez
DNI: 30.123.456
CUIL: 20-30123456-7
Nota: 9
Actividad 1: Leé el texto sobre el ciclo del agua y respondé las preguntas.
```

**Falsos positivos que NO deberían dar 422**: `El documento de la página 3 explica el ciclo.` · `La nota musical do es la primera.` (si alguno da 422, reportalo como posible falso positivo).

### 3.2 Archivos

Armá una carpeta con:

| Archivo | Para qué |
| :--- | :--- |
| PDF de texto (exportado de Word o Google Docs) | Caso feliz |
| PDF con títulos, listas e imágenes | Estructura y textos alternativos |
| PDF escaneado (foto o escaneo sin texto seleccionable) | Diagnóstico solo por reglas |
| PDF protegido con contraseña | 400 |
| `.docx` con estilos Título 1 / Título 2, listas, una tabla, una imagen **con** texto alternativo y otra **sin** | Word |
| `.pptx` con portada, diapositivas con título y viñetas, una imagen con y otra sin texto alternativo, una diapositiva oculta | PowerPoint |
| `.doc` / `.ppt` (formato viejo) o `.docx` con contraseña | 400 con mensaje claro |
| `.docm` (con macros) | 400 |
| Un `.txt` renombrado a `.pdf` o `.docx` | 400 (firma inválida) |
| Un `.pptx` renombrado a `.docx` | 400 (la extensión no coincide con el contenido) |
| Archivo de 0 bytes | 400 |
| Archivo de más de 10 MB | 413 |

Para generar uno de más de 10 MB rápido: `head -c 11000000 /dev/urandom > grande.pdf` (Linux/macOS). En Windows (PowerShell): `fsutil file createnew grande.pdf 11000000`. Como no empieza con `%PDF-` puede devolver 400 antes que 413; para probar el 413 de verdad usá un PDF real grande (por ejemplo, uno con muchas fotos).

---

## 4. Endpoints

Resumen (todos bajo `/api/v1`, salvo los marcados):

| Método | Ruta | Para qué | IA | Rate limit |
| :--- | :--- | :--- | :---: | :---: |
| GET | `/` *(raíz, sin /api/v1)* | Bienvenida y enlaces | – | – |
| GET | `/health` | Estado del backend | – | – |
| POST | `/ai/test-connection` | Probar que la IA responde | **Siempre real** | Sí |
| POST | `/ai/test-gemini` | Igual (obsoleto) | **Siempre real** | Sí |
| POST | `/documents/process-text` | Métricas de un texto (sin IA) | – | – |
| POST | `/documents/upload-pdf` | Extraer un PDF (sin IA) | – | – |
| POST | `/documents/upload` | Extraer PDF, Word o PowerPoint (sin IA) | – | – |
| POST | `/analysis/sample` | Diagnóstico de un texto | mock / real | Sí (real) |
| POST | `/analysis/pdf` | Diagnóstico de un PDF | mock / real | Sí (real) |
| POST | `/analysis/file` | Diagnóstico de PDF, Word o PowerPoint | mock / real | Sí (real) |
| GET | `/analysis/{analysis_id}` | Recuperar un diagnóstico | – | – |
| POST | `/adaptations` | Propuesta de adaptación para una barrera | mock / real | Sí (real) |
| GET | `/adaptations?analysis_id=` | Propuestas de un diagnóstico | – | – |
| GET | `/adaptations/{proposal_id}` | Una propuesta | – | – |
| POST | `/adaptations/{proposal_id}/decision` | Aceptar, editar o descartar | – | – |
| POST | `/adaptations/{proposal_id}/undo` | Deshacer la decisión | – | – |
| GET | `/adaptations/decisions?analysis_id=` | Registro de decisiones | – | – |
| GET | `/adaptations/final-version?analysis_id=` | Versión adaptada final | – | – |
| GET | `/adaptations/report?analysis_id=` | Informe final con puntaje antes/después | – (0 tokens) | – |
| GET | `/export/content?analysis_id=&format=` | Descargar el material adaptado (`docx`, `pdf`, `md`, `txt`) | – | – |
| GET | `/export/report?analysis_id=&format=` | Descargar el informe (`pdf`, `md`, `txt`) | – (0 tokens) | – |

### 4.1 `GET /` y `GET /api/v1/health`

Bienvenida (entorno activo y enlaces a la documentación) y chequeo de estado. Railway usa `/health` para saber si el servicio está vivo.

```bash
curl "$API/health"
# 200 {"status":"online","environment":"production","version":"0.1.0", ...}
```

Probar: responde 200 en menos de 1 s; si el servicio estaba dormido, la primera llamada puede tardar más.

### 4.2 `POST /ai/test-connection` (y `/ai/test-gemini`, obsoleto)

Manda un prompt corto a la conexión de IA activa y devuelve proveedor, modelo y respuesta. **Siempre consume IA real.**

```bash
curl -X POST "$API/ai/test-connection"
```

| Caso | Esperado |
| :--- | :--- |
| IA configurada y con cuota | 200 con proveedor, modelo y texto |
| Sin conexiones de IA | 400 |
| Todas las conexiones agotadas | 429 |
| Proveedores caídos o mal configurados | 503 |
| Más de 5 llamadas en un minuto desde tu IP | 429 + `Retry-After` |

### 4.3 `POST /documents/process-text`

Calcula métricas de un texto (palabras, caracteres, listas) sin IA.

```bash
curl -X POST "$API/documents/process-text" -H "Content-Type: application/json" \
  -d '{"text": "Leé el texto y respondé las preguntas."}'
```

| Caso | Esperado |
| :--- | :--- |
| Texto válido | 200, `source_type = "text"`, `word_count = 7` |
| Menos de 10 caracteres | 422 `string_too_short` |
| Más de 50.000 caracteres | 422 `string_too_long` |
| Sin body o sin `text` | 422 |

### 4.4 `POST /documents/upload-pdf` y `POST /documents/upload`

Extraen el contenido de un archivo **sin IA**: texto, páginas, palabras, imágenes, títulos y listas detectados, advertencias. `upload-pdf` acepta solo PDF; `upload` acepta `.pdf`, `.docx` y `.pptx`.

```bash
curl -X POST "$API/documents/upload" -F "file=@guia.docx"
```

Campos a revisar: `source_type` (`pdf`/`docx`/`pptx`), `page_count` (en PowerPoint son diapositivas visibles), `word_count`, `image_count`, `images_with_alt_text` (solo Word/PowerPoint), `is_scanned_pdf`, `structure.headings`, `structure.list_items_count`, `warnings`.

| Caso | Esperado |
| :--- | :--- |
| PDF con texto | 200, `raw_text` con el contenido |
| Word con estilos de título | 200, `structure.headings` con nivel y texto |
| PowerPoint | 200, títulos de diapositiva como encabezados; diapositivas ocultas y notas del orador **no** aparecen |
| Imagen con texto alternativo en Word/PowerPoint | cuenta en `images_with_alt_text` |
| Extensión no admitida (`.txt`, `.doc`) | 400 |
| Content-Type incorrecto (ej. `image/png`) | 400 |
| Contenido que no corresponde a la extensión | 400 |
| PDF o Office protegido con contraseña | 400 con mensaje claro |
| Office con macros (`.docm`/`.pptm`) | 400 |
| Archivo vacío | 400 |
| Archivo de más de 10 MB | 413 |

### 4.5 `POST /analysis/sample`

Diagnóstico de accesibilidad de un texto pegado. Acepta JSON (`{"text": "..."}`) o el texto crudo con `Content-Type: text/plain`.

```bash
curl -X POST "$API/analysis/sample?mock=true" -H "Content-Type: application/json" \
  -d '{"text": "Actividad 1: Leé el texto de la página 8, subrayá las ideas principales, armá un cuadro y respondé las preguntas."}'

# Texto crudo (con saltos de línea), sin armar JSON:
curl -X POST "$API/analysis/sample?mock=true" -H "Content-Type: text/plain" --data-binary @material.txt
```

Parámetros: `mock` (bool), `dismissed` (repetible, ids de datos personales a descartar).

Respuesta 200: `analysis_id`, `expires_at`, `estimated_score`, `summary`, `dimensions[]` (semáforo por categoría), `strengths[]`, `barriers[]`, `warnings[]`, `score_breakdown`, `metadata` (proveedor, modelo, tokens, palabras analizadas, `is_truncated`).

| Caso | Esperado |
| :--- | :--- |
| Material con barreras | 200 con barreras de oración extensa, consigna múltiple y referencia visual |
| Texto en `text/plain` con saltos de línea | 200 |
| Menos de 10 caracteres / más de 20.000 | 422 |
| JSON mal formado o body vacío | 422 `json_invalid` |
| Texto que no es UTF-8 | 422 `unicode_decode` |
| Solo espacios | 422 "no contiene texto analizable" |
| Con datos personales | 422 `sensitive_data_detected` |
| Mismo texto con todos los `dismissed` | 200 y un warning de que el docente descartó hallazgos |
| Sin mock, 6.ª llamada en el mismo minuto | 429 + `Retry-After` |

### 4.6 `POST /analysis/pdf` y `POST /analysis/file`

Mismo diagnóstico a partir de un archivo. `/pdf` acepta solo PDF; `/file` acepta `.pdf`, `.docx` y `.pptx` (es el que debería usar el front).

```bash
curl -X POST "$API/analysis/file?mock=true" -F "file=@clase.pptx"
```

Parámetros extra: `confirm_images_without_personal_data` (bool). Si el archivo no tiene texto digital (un escaneo), sus imágenes solo se envían a la IA para proponer textos alternativos cuando el docente confirma que no tienen datos personales.

Además de lo de 4.5, revisar:

- `alt_text_proposals[]`: una propuesta por imagen relevante **sin** texto alternativo (hasta 5), con `location_description` (`"Página 1, sector superior"`, `"Diapositiva 3, sector inferior"` o `"Imagen 1, sección «Título»"` en Word).
- PDF escaneado: 200 con la barrera "PDF escaneado sin capa de texto" y puntaje bajo (tope 40).
- `metadata.source_type` y `metadata.page_count`.

Errores: los mismos de 4.4 (400 / 413) más los de datos personales (422) y rate limit (429).

### 4.7 `GET /analysis/{analysis_id}`

Recupera un diagnóstico guardado sin volver a analizar (0 tokens).

| Caso | Esperado |
| :--- | :--- |
| `analysis_id` válido | 200, igual al de la respuesta original |
| Inexistente | 404 "Diagnóstico no encontrado" |
| Vencido (más de 24 h) | 404 "El diagnóstico ha expirado" |

### 4.8 `POST /adaptations`

Pide una propuesta de adaptación para una barrera. Responde **201** con la propuesta en estado `pendiente`.

```bash
curl -X POST "$API/adaptations?mock=true" -H "Content-Type: application/json" -d '{
  "analysis_id": "<analysis_id>",
  "barrier_id": "bar-2",
  "adaptation_type": "lenguaje_claro"
}'
```

- `adaptation_type`: `lenguaje_claro`, `consigna_en_pasos`, `reorganizar`, `mejorar_estructura`.
- `fragment` (opcional, máx. 6.000 caracteres): texto a adaptar escrito por el docente. **Obligatorio** en barreras globales (las que citan `[...]`).
- Si la barrera cita un párrafo recortado con `…`, el backend recupera el párrafo completo del material.

| Caso | Esperado |
| :--- | :--- |
| Barrera con cita | 201, `original_text`, `proposed_text`, `explanation`, `preserved_objective` |
| Barrera global sin `fragment` | 422 indicando que hay que enviar el fragmento |
| `fragment` con datos personales | 422 `sensitive_data_detected` (se puede usar `dismissed`) |
| `analysis_id` inexistente o vencido | 404 |
| `barrier_id` inexistente | 404 |
| `adaptation_type` inválido | 422 |
| Sin mock, por encima del rate limit | 429 |

### 4.9 Consultar propuestas

- `GET /adaptations?analysis_id=…`: todas las propuestas del diagnóstico (lista vacía si no hay).
- `GET /adaptations/{proposal_id}`: una propuesta (404 si no existe o venció).

### 4.10 `POST /adaptations/{proposal_id}/decision` y `/undo`

```bash
curl -X POST "$API/adaptations/<proposal_id>/decision" -H "Content-Type: application/json" -d '{"decision": "aceptada"}'
curl -X POST "$API/adaptations/<proposal_id>/decision" -H "Content-Type: application/json" -d '{"decision": "editada", "edited_text": "Texto final del docente."}'
curl -X POST "$API/adaptations/<proposal_id>/decision" -H "Content-Type: application/json" -d '{"decision": "descartada"}'
curl -X POST "$API/adaptations/<proposal_id>/undo"
```

| Caso | Esperado |
| :--- | :--- |
| Aceptar / descartar una pendiente | 200 con el nuevo estado |
| Editar con `edited_text` | 200, `final_text` igual al texto del docente |
| `editada` sin `edited_text` | 422 |
| `edited_text` con otra decisión | 422 |
| Decidir sobre una ya decidida | 409 |
| Deshacer una decidida | 200, vuelve a `pendiente` |
| Deshacer una pendiente | 409 |
| Dos decisiones simultáneas sobre la misma propuesta | una 200 y la otra 409 |
| Propuesta inexistente | 404 |

### 4.11 `GET /adaptations/decisions?analysis_id=`

Registro cronológico de decisiones y deshaceres (para el informe). Cada entrada: `barrier_id`, `adaptation_type`, `action` (`decision` / `deshacer`), `from_status`, `to_status`, `edited_text`, `created_at`. 404 si el diagnóstico no existe.

### 4.12 `GET /adaptations/final-version?analysis_id=`

Versión final del material aplicando **solo** propuestas `aceptada` y `editada`.

Respuesta: `original_text`, `adapted_text`, `total_proposals`, `applied_count`, `discarded_count`, `pending_count`, `applied_proposals[]`, `warnings[]`.

| Caso | Esperado |
| :--- | :--- |
| Sin propuestas | 200, `adapted_text` igual a `original_text` |
| Una aceptada | su `proposed_text` reemplaza el fragmento |
| Una editada | el texto del docente aparece **textual** |
| Descartadas y pendientes | el fragmento original queda igual |
| Dos propuestas sobre el mismo fragmento | gana la editada (o la decisión más reciente) y hay un warning de "solapamiento" |
| Deshacer y volver a pedir | el cambio desaparece |
| `original_text` | **siempre** igual al material original |

### 4.13 `GET /adaptations/report?analysis_id=`

Informe final: `score_before`, `score_after`, `score_delta`, `executive_summary[]`, `barriers_detail[]` (cada barrera con su decisión), conteos y `disclaimer`. No llama a la IA.

| Caso | Esperado |
| :--- | :--- |
| Sin decisiones | `score_after == score_before` (`score_delta = 0`) |
| Editar una oración extensa por una versión corta | `score_delta > 0`, la penalización `oraciones_extensas` baja o desaparece |
| Word/PDF con imágenes | la penalización de imágenes se mantiene (adaptar texto no la resuelve) |
| Barrera sin propuesta | `resolution_status = "pendiente"` |
| Diagnóstico inexistente | 404 |

### 4.14 `GET /export/content` y `GET /export/report`

Descargan el material adaptado y el informe como archivo (`Content-Disposition: attachment`). Se generan en memoria a partir de la versión final (#38) y del informe (#39): no llaman a la IA ni modifican el original.

| Endpoint | Formatos (`format=`) | Por defecto | Nombre del archivo |
| :--- | :--- | :--- | :--- |
| `/export/content?analysis_id=` | `docx`, `pdf`, `md`, `txt` | `docx` | `material_adaptado_<8 primeros caracteres del id>.<formato>` |
| `/export/report?analysis_id=` | `pdf`, `md`, `txt` | `pdf` | `informe_accesibilidad_<8 primeros caracteres del id>.<formato>` |

```bash
curl -o material.docx "$API/export/content?analysis_id=$ID&format=docx"
curl -o informe.pdf   "$API/export/report?analysis_id=$ID&format=pdf"
```

| Caso | Esperado |
| :--- | :--- |
| Cada formato válido | 200, `Content-Type` correcto y el archivo abre (Word, visor de PDF, editor de texto) |
| Propuestas aceptadas, editadas y descartadas | El material incluye las aceptadas y el texto editado; nunca las descartadas |
| Sin ninguna decisión | El material es igual al original |
| Texto con acentos, ñ, ¿ y ¡ | Se ven bien en todos los formatos |
| Texto con flechas, ≥, emojis o escrituras no latinas | En `md`, `txt` y `docx` se conservan; en `pdf` salen como `?` (limitación conocida, ver sección 10) |
| `format` inválido (por ejemplo `exe`, o `docx` en el informe) | 400 |
| Diagnóstico inexistente o vencido | 404 |
| Con `&delivery=link` | 200 JSON con `file` (`kind: "export"`) y `download.url`; abrir esa URL descarga el mismo archivo que sin el parámetro |
| Abrir `download.url` después de 5 minutos | 403 (la URL firmada venció; pedir otra) |

### 4.15 Acceso: panel `/admin`, documentación y web de pruebas

El panel (`/admin`) es HTML, no forma parte de la API. Se entra en `/admin/login` con GitHub (Clerk) o, como respaldo, con el usuario de administración del backend. En QA probar el acceso y la seguridad; **no modificar conexiones en producción**.

| Prueba | Esperado |
| :--- | :--- |
| Abrir `/admin` sin sesión | 303 a `/admin/login?next=/admin/` |
| Entrar con una cuenta de GitHub habilitada | Vuelve al panel; arriba a la derecha se ve tu usuario y tu rol |
| Entrar con una cuenta de GitHub **no** habilitada | Mensaje "La cuenta de GitHub @usuario no tiene acceso" y no entra |
| 5 intentos fallidos del login de respaldo | 429 "Demasiados intentos fallidos" (se libera a los 10 min) |
| Con solo lectura | Aviso "Modo solo lectura", sin botones de agregar, editar ni borrar y sin pestaña Usuarios |
| `GET /admin/api/metrics` sin sesión | 401 |
| Pestaña **Estado** (cualquier rol) | Una tarjeta por servicio con su estado, el aviso general arriba y la tabla de URLs; se actualiza sola cada 30 s |
| `GET /admin/api/status` sin sesión | 401 |
| `GET /admin/api/status` con sesión | Ninguna key, token ni contraseña en la respuesta |
| Con solo lectura, `POST /admin/keys…` | 403 "Tu usuario tiene acceso de solo lectura" |
| `POST` a `/admin/...` con header `Origin` de otro dominio | 403 "Origen no permitido" |
| Abrir `/adminv2` (ruta vieja) | 308 a `/admin/` |
| `/docs` o `/redoc` sin sesión (fuera de desarrollo) | 303 al login; después de entrar vuelve a la documentación |
| `GET /api/v1/openapi.json` sin sesión (fuera de desarrollo) | 401 |
| Cerrar sesión (menú del usuario) | Vuelve al login con "Cerraste la sesión"; `/admin` vuelve a pedir login |

**Web de pruebas:** pide el mismo login antes de mostrarse. Desde su origen, toda llamada a `/api/v1` sin sesión responde 401 con `code: "test_web_login_required"` y la página vuelve al login. Con solo lectura, el modo mock queda fijo y una llamada de IA real responde 403. Desde curl, Postman o Bruno la API sigue respondiendo sin credenciales.

### 4.16 Archivos: subida directa con URL firmada (`/files`)

El archivo **no pasa por la API**: se pide una URL firmada, se sube a esa URL y se confirma. En producción la URL apunta al bucket de Cloudflare R2; en local, a `/api/v1/files/local/...`.

```bash
SIZE=$(stat -c %s material.pdf)
curl -s -X POST "$API/files/upload-url" -H 'Content-Type: application/json' \
  -d "{\"filename\":\"material.pdf\",\"content_type\":\"application/pdf\",\"size_bytes\":$SIZE}"
# Copiar file.file_id y upload.url de la respuesta
curl -X PUT "<upload.url>" -H 'Content-Type: application/pdf' --data-binary @material.pdf
curl -s -X POST "$API/files/<file_id>/complete"
curl -s "$API/files/<file_id>/download-url"
```

| Caso | Esperado |
| :--- | :--- |
| Flujo completo con un `.pdf`, `.docx` o `.pptx` | `upload-url` 201 (`status: "pending"`), `PUT` 200, `complete` 200 (`status: "uploaded"`) y la descarga es idéntica al original, con su nombre |
| `upload-url` con `.exe`, sin extensión o `content_type` `text/html` | 400 |
| `upload-url` con más de 10 MB | 413 |
| `PUT` con un `Content-Type` distinto al de `upload.headers` | 403 (va firmado) |
| `PUT` después de 5 minutos o con la URL modificada | 403 |
| `complete` sin haber subido | 409 |
| Subir un archivo de otro tamaño que el declarado | `complete` 422 y el archivo se borra (`GET /files/<id>` da 404) |
| Subir un `.txt` renombrado a `.pdf` | `complete` 422 ("firma '%PDF-' ausente") |
| `download-url` antes de confirmar | 404 |
| `file_id` inventado | 404 |

---

## 5. Flujos de punta a punta

Se pueden hacer desde la web de pruebas (pasos 1 a 7) o con `curl`. Siempre con mock salvo indicación.

**F1. Texto completo.** Analizar el material con barreras → pedir propuestas para 3 barreras → aceptar una, editar otra y descartar la tercera → ver el registro de decisiones (3 entradas) → versión final (2 aplicadas, 1 descartada; la edición aparece textual) → informe (puntaje después ≥ antes) → exportar el material en `docx` y el informe en `pdf` y abrirlos. Deshacer la aceptada → versión final, informe y exportaciones se actualizan.

**F2. PDF.** Subir un PDF con texto e imágenes → revisar `alt_text_proposals` → proponer y aceptar una adaptación → versión final.

**F3. Word y PowerPoint.** Igual que F2 con `.docx` y `.pptx`; verificar títulos, listas, diapositivas ocultas excluidas e imágenes con texto alternativo que no reciben propuesta.

**F4. Datos personales.** Analizar el texto con datos ficticios → 422 con 4 hallazgos enmascarados → reenviar descartando 2 → 422 con los 2 restantes → reenviar descartando todos → 200.

**F5. PDF escaneado.** Subirlo → 200 con barrera de escaneo y puntaje ≤ 40; probar con y sin `confirm_images_without_personal_data`.

**F6. Vencimiento.** Guardar un `analysis_id` y consultarlo al día siguiente (más de 24 h) → 404 "expirado".

**F7. IA real (coordinado).** Una sola vez por entorno: F1 sin `?mock=true`, revisando `metadata.provider`, `metadata.total_tokens` y que las citas de las barreras tengan `fragment_found = true`.

---

## 6. Códigos de respuesta

| Código | Significado | Qué revisar |
| :--- | :--- | :--- |
| 200 / 201 | OK / propuesta creada | |
| 400 | Archivo inválido, extensión o Content-Type incorrecto, protegido, con macros, vacío, sin conexiones de IA, formato de exportación inválido | `detail` con mensaje para el docente |
| 303 | Página del panel o `/docs` sin sesión: redirige a `/admin/login` | Que después del login vuelva a la página pedida |
| 401 | Falta sesión (`/admin/api/...`, `openapi.json` fuera de desarrollo, web de pruebas sin login) o login incorrecto | |
| 403 | `POST` al panel desde otro origen, acción de edición con rol de solo lectura o IA real desde la web de pruebas con solo lectura | `detail` explica el motivo |
| 404 | Diagnóstico, barrera o propuesta inexistente o vencida | |
| 409 | Decisión sobre una propuesta ya decidida, o deshacer una pendiente | |
| 413 | Archivo de más de 10 MB | |
| 422 | Datos inválidos (`detail` es una **lista**), material sin texto (`detail` es un **texto**) o datos personales (`detail` es un **objeto** con `code`) | Distinguir los tres formatos |
| 429 | Rate limit de la app, cuota del proveedor agotada o demasiados intentos de login fallidos | Header `Retry-After` en los de IA |
| 502 | La IA devolvió una respuesta inválida | |
| 503 | Ningún proveedor de IA respondió | |
| 500 | Error inesperado | **Siempre es bug**: reportarlo |

---

## 7. Pruebas de estrés de la API

### 7.1 Dónde y cómo

- **Contra el backend local** (sección 1.3) o **staging**. Nunca contra producción.
- **Siempre con `?mock=true`**: mide el backend (extracción, reglas, base de datos), no el proveedor de IA.
- Ojo: el modo mock **no tiene rate limit**, así que una prueba mal configurada puede saturar el servidor sin que nada la frene.
- Avisá a Backend antes de usar staging.

### 7.2 Escenarios sugeridos

| Escenario | Configuración | Qué observar |
| :--- | :--- | :--- |
| Carga normal | 10 a 20 usuarios simulados, 5 min | p95 < 1 s, 0 % errores |
| Rampa | de 0 a 100 usuarios en 5 min | En qué punto suben la latencia o los errores |
| Pico | 50 usuarios de golpe, 1 min | Que no haya 500 ni reinicios |
| Resistencia (soak) | 15 usuarios, 30 a 60 min | Memoria estable, sin degradación con el tiempo |
| Archivos pesados | 2, 5 y 10 archivos de 8–10 MB en simultáneo | Memoria del proceso y latencia (ver 7.4) |
| Concurrencia de decisiones | 2 a 5 decisiones a la vez sobre la misma propuesta | Una 200 y el resto 409, nunca dos 200 |
| Entradas en el borde | textos de 20.000 caracteres, PDFs de 100+ páginas | Tiempo de respuesta y que no haya 500 |

Medir: latencia p50/p95/p99, requests por segundo, porcentaje de errores por código, memoria y CPU del proceso (en local con el administrador de tareas o `top`; en staging con las métricas de Railway).

### 7.3 Script de Locust (probado)

```bash
pip install locust pymupdf
locust -f locustfile.py --host http://127.0.0.1:8000            # interfaz web en http://localhost:8089
locust -f locustfile.py --host http://127.0.0.1:8000 --headless -u 20 -r 2 -t 5m   # sin interfaz
```

`locustfile.py`:

```python
import random

from locust import HttpUser, between, task

TEXTO = (
    "Unidad 3: El ciclo del agua\n\n"
    "El ciclo del agua, también conocido como ciclo hidrológico, es el proceso de circulación del agua entre "
    "los distintos compartimentos de la hidrosfera, en el cual se producen transformaciones de estado físico "
    "que dependen de la temperatura y la presión, y que permiten que el agua pase de la superficie terrestre a "
    "la atmósfera mediante la evaporación y la transpiración de las plantas.\n\n"
    "Actividad 1: Leé el texto de la página 8, subrayá las ideas principales, armá un cuadro comparativo, "
    "respondé las preguntas del final y entregá todo antes del viernes."
)


def _pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_textbox(pymupdf.Rect(50, 50, 550, 800), TEXTO, fontsize=11)
    return doc.tobytes()


PDF = _pdf()


class DocenteSimulado(HttpUser):
    """Flujo del docente en modo mock: analizar → proponer → decidir → versión final → informe."""

    wait_time = between(1, 3)

    @task(3)
    def flujo_texto(self):
        r = self.client.post("/api/v1/analysis/sample?mock=true", json={"text": TEXTO}, name="analysis/sample")
        if r.status_code != 200:
            return
        analysis = r.json()
        aid = analysis["analysis_id"]
        con_texto = [b for b in analysis["barriers"] if not b["original_text"].startswith("[")]
        if not con_texto:
            return
        p = self.client.post(
            "/api/v1/adaptations?mock=true",
            json={"analysis_id": aid, "barrier_id": random.choice(con_texto)["id"], "adaptation_type": "lenguaje_claro"},
            name="adaptations",
        )
        if p.status_code == 201:
            self.client.post(f"/api/v1/adaptations/{p.json()['proposal_id']}/decision",
                             json={"decision": "aceptada"}, name="adaptations/{id}/decision")
        self.client.get("/api/v1/adaptations/final-version", params={"analysis_id": aid}, name="final-version")
        self.client.get("/api/v1/adaptations/report", params={"analysis_id": aid}, name="report")

    @task(1)
    def analizar_pdf(self):
        self.client.post("/api/v1/analysis/file?mock=true",
                         files={"file": ("actividad.pdf", PDF, "application/pdf")}, name="analysis/file (pdf)")

    @task(1)
    def health(self):
        self.client.get("/api/v1/health", name="health")
```

**Línea base de referencia** (backend local en una notebook, modo mock, 30 usuarios durante 60 s): unas 51 requests/s, **0 % de errores**, p50 5 ms y p95 13 ms en general; `analysis/file` con un PDF chico, p95 22 ms. En Railway los números van a ser peores (otra CPU, red de por medio): usalos para comparar entre corridas, no como meta.

### 7.4 Archivos pesados y memoria (importante)

Medición en local, modo mock, PDF de 9 MB con imágenes subido N veces en simultáneo a `/analysis/file`:

| Archivos simultáneos | Memoria del proceso (pico) | Latencia máxima | Errores |
| :---: | :---: | :---: | :---: |
| 0 (en reposo) | ~146 MB | – | – |
| 10 | ~605 MB | 6,3 s | 0 |
| 20 | ~1,2 GB | 12,9 s | 0 |

Cada archivo pesado en proceso ocupa unos **60 MB**, y la memoria no se libera del todo al terminar. Si en Railway el límite de memoria del servicio es menor que el pico, **el proceso se reinicia** (todos los usuarios reciben errores un rato y se pierden los contadores en memoria del rate limit). Por eso:

- En staging, subí de a poco (2, 5, 10) y mirá la memoria en Railway antes de seguir.
- Reportá a Backend el número de archivos simultáneos en el que se reinicia o la latencia supera 30 s.

### 7.5 Alternativa con k6

```javascript
// k6 run -e API=http://127.0.0.1:8000/api/v1 carga.js
import http from "k6/http";
import { check, sleep } from "k6";

export const options = {
  stages: [{ duration: "1m", target: 20 }, { duration: "3m", target: 20 }, { duration: "1m", target: 0 }],
  thresholds: { http_req_failed: ["rate<0.01"], http_req_duration: ["p(95)<1000"] },
};

const TEXTO = "Actividad 1: Leé el texto de la página 8, subrayá las ideas principales, armá un cuadro comparativo, respondé las preguntas del final y entregá todo antes del viernes.";

export default function () {
  const r = http.post(`${__ENV.API}/analysis/sample?mock=true`, JSON.stringify({ text: TEXTO }),
    { headers: { "Content-Type": "application/json" } });
  check(r, { "200": (res) => res.status === 200 });
  if (r.status === 200) {
    const aid = r.json("analysis_id");
    http.get(`${__ENV.API}/adaptations/final-version?analysis_id=${aid}`);
    http.get(`${__ENV.API}/adaptations/report?analysis_id=${aid}`);
  }
  sleep(1);
}
```

---

## 8. Pruebas de los límites de la IA

Hay dos capas de límites y conviene probarlas por separado:

1. **Rate limit de la app** (sección 2.5): lo aplica el backend antes de llamar a la IA. Se prueba **sin gastar casi nada**.
2. **Cuota del proveedor**: la que da el proveedor (por ejemplo Groq: 30 requests/min, 8.000 tokens/min y 1.000 requests/día para el modelo principal). Cuando una conexión se acerca a su límite o el proveedor responde 429, el backend la pone en pausa (cooldown) y **rota** a la siguiente conexión configurada en `/admin`; si no queda ninguna, responde 429.

> ⚠️ Todo lo de esta sección consume IA real. Hacelo **en staging o local con una API key propia de prueba** (Groq o Gemini tienen capa gratuita), nunca con la cuota de producción, y avisá antes.

### 8.1 Rate limit de la app (barato)

Truco: el límite por IP corta **antes** de llamar a la IA, así que las llamadas rechazadas no gastan tokens. Solo las primeras 5 consumen.

| Prueba | Pasos | Esperado |
| :--- | :--- | :--- |
| Límite por minuto | 6 llamadas seguidas a `POST /analysis/sample` **sin** mock | 5 × 200, la 6.ª 429 con `Retry-After` ≤ 60 |
| Recuperación | Esperar lo que indica `Retry-After` y reintentar | 200 |
| El mock no cuenta | Tras el 429, la misma llamada con `?mock=true` | 200 |
| GET no cuenta | Tras el 429, `GET /analysis/{id}` | 200 |
| Mensaje | Leer `detail` | Sugiere esperar o usar `?mock=true` |

Para probar los límites diarios y globales sin gastar, pedile a Backend que levante staging con límites bajos, por ejemplo `AI_RATE_LIMIT_PER_IP_MINUTE=2`, `AI_RATE_LIMIT_PER_IP_DAY=3`, `AI_RATE_LIMIT_GLOBAL_MINUTE=4`. El límite global se prueba con dos IPs distintas (por ejemplo, una notebook y un celular con datos móviles).

Script de ejemplo (6 llamadas, imprime código y `Retry-After`):

```bash
for i in 1 2 3 4 5 6; do
  curl -s -o /dev/null -w "%{http_code} retry-after=%header{retry-after}\n" \
    -X POST "$API/analysis/sample" -H "Content-Type: application/json" \
    -d '{"text": "Leé el texto y respondé las preguntas del final."}'
done
```

### 8.2 Cuota del proveedor y rotación (coordinar con Backend)

Requiere rol **admin** en `/admin` de staging (con solo lectura no se pueden cambiar umbrales), así que conviene hacerlo **junto con alguien de Backend**.

| Prueba | Cómo | Esperado |
| :--- | :--- | :--- |
| Umbral de la conexión | En `/admin`, poner RPM = 2 a la conexión principal y hacer 3 análisis reales | El 3.º usa la conexión de respaldo (`metadata.connection` cambia) |
| Sin respaldo | Dejar una sola conexión con RPM = 1 y hacer 2 análisis | 2.º → 429 "todas las conexiones agotadas" |
| Proveedor caído | Conexión con una URL o key inválida como principal | El backend la pausa y usa la siguiente; si no hay otra, 503 |
| Vuelta a la normalidad | Esperar 1 minuto (o el cooldown) | La conexión principal vuelve a usarse |
| Consumo | Mirar el panel de `/admin` antes y después | Requests y tokens suben según lo enviado |

### 8.3 Costo de cada llamada (para planificar)

| Llamada | Requests al proveedor |
| :--- | :--- |
| `POST /analysis/sample` | 1 |
| `POST /analysis/pdf` o `/file` sin imágenes | 1 |
| `POST /analysis/pdf` o `/file` con imágenes relevantes sin texto alternativo | 2 (diagnóstico + textos alternativos) |
| `POST /adaptations` | 1 |
| `POST /ai/test-connection` | 1 |
| Cualquier llamada con `?mock=true` o cualquier GET | 0 |

Un material de 3.000 palabras puede usar varios miles de tokens: con un límite de 8.000 tokens/min, **dos o tres análisis grandes en el mismo minuto** ya pueden agotar la conexión. Si una prueba necesita muchos análisis reales, espaciarlos (1 cada 20–30 s).

---

## 9. Cómo reportar un bug

Crear un issue en [e18-innovalab/contenidos_accesibles](https://github.com/e18-innovalab/contenidos_accesibles/issues) con el label `backend` y:

```markdown
**Endpoint:** POST /api/v1/analysis/file?mock=true
**Entorno:** producción / staging / local · fecha y hora
**Pasos:** 1. … 2. …
**Esperado:** …
**Obtenido:** código HTTP y `detail`
**curl:** (copiarlo del registro de la web de pruebas)
**analysis_id / proposal_id:** si aplica (vencen a las 24 h)
**Archivo:** adjuntar si no tiene datos reales
```

Prioridad sugerida: **alta** para cualquier 500, reinicios del servidor, datos personales que pasan sin 422 o una edición del docente que no aparece en la versión final; **media** para respuestas incorrectas o mensajes confusos; **baja** para textos o detalles de documentación.

---

## 10. Problemas conocidos

- Un texto de **solo signos** (por ejemplo `"............ ----"`) pasa como válido y se analiza; con IA real gastaría tokens. Pendiente en el issue #42.
- Con muchos archivos pesados en simultáneo la memoria del proceso crece unos 60 MB por archivo (ver 7.4).
- Los contadores del rate limit y las pausas de las conexiones viven en memoria: se reinician con cada deploy o reinicio.
- En la exportación a **PDF**, los caracteres fuera de latin-1 (flechas, ≥, emojis, escrituras no latinas) salen como `?`, y el PDF no está etiquetado para lectores de pantalla. El español común se ve bien. Para material accesible conviene **DOCX** (el formato por defecto). Pendiente en #87.
- La web de pruebas todavía no tiene botones de exportación: usar `curl` o Swagger.
- `POST /ai/test-gemini` está obsoleto; usar `/ai/test-connection`.
