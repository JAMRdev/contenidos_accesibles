# 🧠 Asistente de Accesibilidad para Contenidos Educativos - Backend
> **Innova Lab** | Agencia de Habilidades para el Futuro  
> *Herramienta de Inteligencia Artificial para la creación y adaptación de contenidos educativos accesibles.*

---

## 📋 Descripción del Proyecto

Los materiales educativos digitales suelen presentar barreras de accesibilidad relacionadas con su estructura, complejidad del lenguaje, legibilidad y ausencia de alternativas textuales en imágenes.

Este backend provee la API REST que impulsa el producto:
- **Escaneo y diagnóstico:** Combina reglas heurísticas automáticas con el análisis semántico de **Google Gemini API**.
- **Asistencia a la adaptación:** Genera sugerencias (simplificación de textos, consignas paso a paso, textos alternativos para imágenes) sin reemplazar el criterio pedagógico del docente.
- **Control docente:** Cada propuesta de adaptación puede ser aceptada, editada o descartada por el docente antes de exportar el material final.

---

## 🏛️ Arquitectura del Software y Decisiones Técnicas

El backend fue diseñado bajo una **arquitectura en capas desacopladas (Clean Architecture simplificada)**, asegurando escalabilidad, mantenibilidad y facilidad de prueba:

```text
backend/
├── app/
│   ├── admin/
│   │   ├── router.py                 # Panel de administración HTML (/admin): páginas, métricas, conexiones y usuarios
│   │   ├── session.py                # Sesión del panel (cookie firmada), roles y límite de intentos de login
│   │   ├── clerk_auth.py             # Login con Clerk: validación del token y cuenta del .env en Clerk
│   │   ├── test_web_auth.py          # Login de la web de pruebas (token Bearer) y su middleware
│   │   ├── diagrams/                 # HTML de los diagramas servidos en /admin/diagramas/{slug}
│   │   ├── static/                   # admin.css y admin.js (gráficos SVG, tablas y modales)
│   │   └── templates/                # base, login, dashboard y diagram (Jinja2 + Obelisco V2)
│   ├── api/
│   │   └── v1/
│   │       ├── endpoints/            # Capa de Controladores HTTP
│   │       │   ├── health.py         # Estado del sistema y entorno
│   │       │   ├── test_ai.py        # Prueba de conectividad con IA
│   │       │   ├── documents.py      # Extracción de PDF, Word, PowerPoint y texto directo
│   │       │   └── analysis.py       # Escáner de accesibilidad con IA
│   │       ├── router.py             # Versionado y unificación de rutas v1
│   │       └── uploads.py            # Validación de archivos subidos (.pdf, .docx, .pptx)
│   ├── core/
│   │   ├── config.py                 # Configuración centralizada y tipada (Pydantic Settings)
│   │   ├── crypto.py                 # Cifrado (Fernet) de las API keys guardadas en la DB
│   │   ├── docs.py                   # /docs, /redoc y openapi.json detrás de la sesión del panel (fuera de development)
│   │   ├── rate_limit.py             # Límite de uso de IA por IP y global
│   │   └── security.py               # Verificación anti-CSRF (Origin) de los POST del panel
│   ├── db/
│   │   ├── database.py               # Conexión SQLite + creación del esquema
│   │   ├── d1_client.py              # Cliente del Worker pasarela de Cloudflare D1
│   │   ├── api_key_repository.py     # CRUD de conexiones de IA y contadores de uso
│   │   ├── ai_usage_repository.py    # Historial de llamadas a la IA (métricas del panel)
│   │   └── admin_member_repository.py # Usuarios de GitHub con acceso al panel y su rol
│   ├── schemas/
│   │   ├── document.py               # Contratos de extracción (ExtractedDocument, TextInputRequest)
│   │   └── analysis.py               # Contratos de diagnóstico (AccessibilityAnalysisResponse)
│   ├── services/
│   │   ├── document_service.py       # Extracción de PDF en memoria (PyMuPDF, párrafos por bloque) y heurísticas
│   │   ├── office_service.py         # Extracción de Word y PowerPoint (python-docx / python-pptx) al mismo contrato
│   │   ├── accessibility_service.py  # Orquestador del diagnóstico: IA + reglas, validación de citas y puntaje
│   │   ├── rules_service.py          # Motor de reglas automáticas (oraciones/párrafos extensos, visual) y puntaje
│   │   ├── key_rotation_service.py   # Rotación automática y tracking de consumo por key
│   │   ├── llm_service.py            # Orquestación multi-proveedor de modelos de lenguaje
│   │   ├── admin_metrics_service.py  # Métricas agregadas del panel (uso de IA, conexiones y producto)
│   │   └── llm/                      # Adaptadores de proveedores (Gemini, OpenAI-compatible)
│   └── main.py                       # Instanciación de FastAPI, Middlewares y OpenAPI
├── docs/
│   ├── riesgos_y_limitaciones_semana1.md     # Riesgos técnicos de documentos y arquitectura
│   ├── riesgos_limitaciones_semana1_ia.md    # Riesgos técnicos de IA y cuotas
│   ├── motor_reglas.md                       # Reglas automáticas, umbrales y fórmula del puntaje
│   ├── diagramas/                            # Fuentes (JSON de Archify) de los diagramas del panel
│   └── benchmarks/                           # Reportes de latencia, tokens y variabilidad
├── d1-gateway/                       # Worker pasarela de Cloudflare D1 (wrangler, migraciones)
├── scripts/
│   ├── benchmark_analysis.py         # Benchmark de IA y calibración de reglas (--rules-only)
│   └── d1_ping.py                    # Conexión y latencia contra el Worker de D1
├── tests/                            # Tests unitarios y de integración (pytest, modo mock) y fixtures
├── app_data.db                       # DB local SQLite: API keys y contadores (IGNORADO en Git)
├── .env                              # Variables sensibles locales (IGNORADO en Git)
├── .env.example                      # Plantilla pública de variables requeridas
├── .gitignore                        # Reglas de exclusión de seguridad y entorno
├── requirements.txt                  # Dependencias fijadas del proyecto
├── requirements-dev.txt              # Dependencias de desarrollo (pytest)
├── pytest.ini                        # Configuración de pytest
└── README.md                         # Documentación general de arquitectura y ejecución
```

### 💡 Justificación de Decisiones Técnicas:

1. **FastAPI como Framework Web:**
   - **Asincronía nativa (`async / await`) + Threadpool (`asyncio.to_thread`):** Las llamadas a modelos de lenguaje (LLMs) usan I/O asíncrono, mientras que el procesamiento de PDFs en C++ (`PyMuPDF`) se ejecuta en hilos separados para nunca bloquear el *event loop*.
   - **Validación con Pydantic:** Validación estricta de esquemas de entrada y salida, asegurando contratos de datos claros con el equipo de Frontend y reduciendo errores en tiempo de ejecución.
2. **Procesamiento de Documentos 100% en Memoria (`PyMuPDF`):**
   - Los archivos PDF subidos por los docentes se procesan directamente en streams de memoria RAM sin persistirse en disco, cumpliendo estrictamente con las pautas de privacidad y protección de datos sensibles de estudiantes.
   - Incluye heurísticas para detectar documentos escaneados (sin capa de texto OCR) y recorte inteligente (`text_for_analysis`) para proteger las cuotas de tokens.
   - Word (`.docx`) y PowerPoint (`.pptx`) se extraen con `python-docx` / `python-pptx` al mismo `ExtractedDocument` que un PDF, así reglas, IA, textos alternativos y detección de datos personales funcionan igual. Los títulos salen de los estilos o del título de cada diapositiva, y las imágenes que ya tienen texto alternativo no se envían a la IA. Antes de abrirlos se rechazan formatos antiguos, archivos protegidos, con macros o que descomprimidos superan 150 MB (zip bombs).
3. **Salidas Estructuradas y Control de Alucinaciones:**
   - El escáner utiliza `response_schema` para garantizar respuestas JSON tipadas con semáforo por dimensión (`comprension`, `estructura`, `accesibilidad_visual`) y verifica textualmente cada cita (`fragment_found`) contra el documento original.
4. **Documentación OpenAPI Automática (Swagger UI):**
   - Disponibilidad inmediata de documentación interactiva en `/docs` y `/redoc` sin necesidad de mantener colecciones manuales en Postman.
5. **Capa de Servicios y Adaptadores LLM Aislados (`services/`):**
   - La lógica de integración con proveedores de IA está desacoplada de los controladores HTTP, permitiendo rotar entre múltiples API keys y proveedores por orden de prioridad.
6. **Manejo Seguro de Secretos y Configuración (`pydantic-settings`):**
   - Ninguna credencial sensible está hardcodeada. Las claves se leen dinámicamente desde variables de entorno (`.env`) con `SecretStr` y cifrado en reposo mediante **Fernet** (`cryptography`).
7. **Políticas de CORS Preconfiguradas:**
   - Configuración explícita de orígenes seguros (`localhost:3000`, `localhost:5173`) para evitar problemas de bloqueo de peticiones cruzadas durante el desarrollo con React/Vite/Next.js.
8. **Motor de Reglas Automáticas y Puntaje Combinado (`rules_service.py`):**
   - Reglas determinísticas en Python (oraciones de más de 35 palabras, párrafos de más de 150, imágenes y PDF escaneado) que corren sobre el texto completo sin consumir tokens. El `estimated_score` pondera IA (70%) y reglas (30%) y expone su cálculo en `score_breakdown`. Detalle en [`docs/motor_reglas.md`](docs/motor_reglas.md).

---

## 🔐 Panel de Administración y Rotación de API Keys

Para evitar caídas de servicio cuando se agota la capa gratuita de los proveedores de IA, el backend expone un panel interno en **`/admin`** (HTML con Obelisco V2) que permite:

- **Ver métricas de uso** de la IA por período (24 h, 7 y 30 días): llamadas, tokens, latencia p95 y errores, tokens por proveedor y llamadas por resultado. También uso del producto: diagnósticos, puntajes, barreras y decisiones del docente. Los gráficos tienen leyenda, tooltip, navegación con teclado y vista de tabla, con colores validados para daltonismo.
- **Visualizar el consumo en tiempo real** por conexión: requests y tokens usados vs. sus umbrales — **RPM** (requests/minuto), **TPM** (tokens/minuto), **RPD** (requests/día) y **TPD** (tokens/día).
- **Cargar y priorizar múltiples conexiones de IA**, cada una con sus propios umbrales configurables.
- **Rotación automática**: cuando la key activa se acerca a cualquiera de sus umbrales o recibe un error de cuota (`429`/`503`), el backend cambia automáticamente a la siguiente conexión disponible.
- **Administrar el acceso**: qué usuarios de GitHub entran y con qué rol.

**Acceso y roles.** Se entra en `/admin/login` con **Clerk** (cuenta de GitHub del equipo o el usuario `ADMIN_USERNAME`) o con el formulario de respaldo (`ADMIN_USERNAME` / `ADMIN_PASSWORD`), según `ADMIN_AUTH` (`local`, `clerk` o `ambos`). La sesión dura 8 h (cookie firmada `HttpOnly`, `SameSite=Strict`, `Secure` fuera de development) y se bloquea tras 5 intentos fallidos por IP o 30 en total en 10 minutos.

| Rol | Quién | Puede |
| :--- | :--- | :--- |
| Admin | Cuenta del `.env` y los usuarios de `ADMIN_GITHUB_ADMINS` | Todo: conexiones, usuarios y web de pruebas con IA real |
| Solo lectura | Usuarios de `ADMIN_GITHUB_VIEWERS` | Ver el panel completo (menos la pestaña Usuarios), Swagger y la web de pruebas en modo mock |

Las listas de GitHub solo cargan usuarios al arrancar; después se administran desde la pestaña **Usuarios** y los cambios aplican al instante. Las rutas viejas `/adminv2/...` redirigen a `/admin/...`.

---

## 📖 Documentación de la API (Swagger y ReDoc)

FastAPI autogenera dos interfaces de documentación interactiva:

* **Swagger UI (Pruebas interactivas):** [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)  
  *Permite probar cada endpoint en vivo haciendo clic en **"Try it out"** y luego en **"Execute"**.*
* **ReDoc (Documentación técnica limpia):** [http://127.0.0.1:8000/redoc](http://127.0.0.1:8000/redoc)

En local (`ENVIRONMENT=development`) quedan abiertas. En staging/production piden **iniciar sesión en el panel** (`/admin/login`) y vuelven solas a la documentación después de entrar.

---

## 📡 Endpoints Implementados (Semanas 0 a 3)

| Método | Endpoint | Descripción | Estado |
| :--- | :--- | :--- | :--- |
| `GET` | `/` | Bienvenida, entorno activo y enlaces a la documentación | ✅ Operativo |
| `GET` | `/api/v1/health` | Chequeo de estado y versión del backend | ✅ Operativo |
| `POST` | `/api/v1/ai/test-gemini` | Validación de conectividad con IA (con rotación automática de keys) | ✅ Operativo |
| `POST` | `/api/v1/documents/process-text` | Procesamiento y extracción de métricas de texto plano directo | ✅ Operativo |
| `POST` | `/api/v1/documents/upload-pdf` | Carga y extracción en memoria de archivos PDF (texto, páginas, imágenes y detección de escaneo) | ✅ Operativo |
| `POST` | `/api/v1/documents/upload` | Igual que `upload-pdf`, aceptando también Word (`.docx`) y PowerPoint (`.pptx`) | ✅ Operativo |
| `POST` | `/api/v1/analysis/sample` | Escáner de accesibilidad IA + reglas sobre texto pegado (`application/json` o `text/plain`) con semáforo, puntaje combinado y barreras. `?mock=true` simula la IA | ✅ Operativo |
| `POST` | `/api/v1/analysis/pdf` | Escáner de accesibilidad IA + reglas sobre un PDF subido (máx. 10 MB). `?mock=true` simula la IA | ✅ Operativo |
| `POST` | `/api/v1/analysis/file` | Igual que `/analysis/pdf`, aceptando `.pdf`, `.docx` y `.pptx` (máx. 10 MB). `?mock=true` simula la IA | ✅ Operativo |
| `POST` | `/api/v1/adaptations` | Propuesta de adaptación con IA para una barrera (`lenguaje_claro`, `consigna_en_pasos`, `reorganizar`, `mejorar_estructura`): original + propuesta + explicación, en estado `pendiente`. `?mock=true` simula la IA | ✅ Operativo |
| `GET` | `/api/v1/adaptations?analysis_id=` | Propuestas de adaptación generadas para un diagnóstico | ✅ Operativo |
| `GET` | `/api/v1/adaptations/{proposal_id}` | Recuperar una propuesta de adaptación por ID | ✅ Operativo |
| `POST` | `/api/v1/adaptations/{proposal_id}/decision` | Revisión docente: aceptar, editar (con `edited_text`) o descartar una propuesta pendiente. El original nunca se modifica | ✅ Operativo |
| `POST` | `/api/v1/adaptations/{proposal_id}/undo` | Deshacer la decisión: la propuesta vuelve a `pendiente` | ✅ Operativo |
| `GET` | `/api/v1/adaptations/decisions?analysis_id=` | Registro cronológico de decisiones y deshaceres del diagnóstico (para el informe) | ✅ Operativo |
| `GET` | `/api/v1/adaptations/final-version?analysis_id=` | Versión final del material: aplica solo las propuestas aceptadas o editadas (solapamientos resueltos a favor de la edición del docente y la decisión más reciente) | ✅ Operativo |
| `GET` | `/api/v1/adaptations/report?analysis_id=` | Informe final: barreras con la decisión del docente, resumen y puntaje antes/después (comparable: conserva la IA y las reglas del archivo, recalcula las de texto; 0 tokens) | ✅ Operativo |
| `GET` | `/admin` | Panel de administración: métricas, conexiones de IA, uso del producto, usuarios y diagramas (HTML, requiere sesión) | ✅ Operativo |
| `GET`/`POST` | `/admin/login` | Login del panel: Clerk (GitHub o usuario/contraseña) y respaldo con el usuario del `.env` | ✅ Operativo |
| `GET` | `/admin/diagramas/{slug}` | Diagrama de arquitectura dentro del panel. Fuentes y cómo regenerarlos en [`docs/diagramas/`](docs/diagramas/README.md) | ✅ Operativo |
| `GET` | `/admin/api/metrics?range=24h\|7d\|30d` | Métricas agregadas del panel (JSON, requiere sesión) | ✅ Operativo |
| `GET`/`POST`/`PATCH`/`DELETE` | `/admin/api/members` | Usuarios de GitHub con acceso al panel y su rol (JSON, solo admins) | ✅ Operativo |
| `GET`/`POST` | `/api/v1/test-web/auth/...` | Login de la web de pruebas: configuración, usuario/contraseña, Clerk y sesión actual (token Bearer) | ✅ Operativo |

> `/admin` y `/api/v1/test-web/auth` son internos, no forman parte del contrato de la API — por eso no aparecen en el esquema OpenAPI de `/docs`/`/redoc` (`include_in_schema=False`).

---

## 🛠️ Guía de Puesta en Marcha Local

### 1. Requisitos Previos
- Python 3.10 o superior (recomendado 3.11 a 3.13).
- Obtener una API Key gratuita de Gemini en [Google AI Studio](https://aistudio.google.com/).

### 2. Clonar el repositorio y configurar el entorno
El backend vive en la carpeta `backend/` del monorepo: todos los comandos de esta guía se corren desde ahí.
```powershell
git clone https://github.com/e18-innovalab/contenidos_accesibles.git
cd contenidos_accesibles/backend

# 1. Crear el entorno virtual
py -m venv venv

# 2. Activar el entorno virtual (Windows PowerShell)
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\venv\Scripts\Activate.ps1

# 3. Instalar las dependencias
pip install -r requirements.txt
```

### 3. Configurar variables de entorno
Copiar la plantilla `.env.example` para crear el archivo local `.env`:
```powershell
Copy-Item .env.example .env
```
Editar `.env` y completar, como mínimo:
```env
# Credenciales del panel /admin (elegí tu propia contraseña). En local se entra solo con ellas, sin Clerk.
ADMIN_USERNAME=admin
ADMIN_PASSWORD=una_contraseña_segura
ADMIN_AUTH=local

# Clave de cifrado para las API keys guardadas en la DB local (generarla una sola vez)
DB_ENCRYPTION_KEY=
```
Generar `DB_ENCRYPTION_KEY` con:
```powershell
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

### 4. Ejecutar el servidor de desarrollo
```powershell
uvicorn app.main:app --reload
```
Acceder a la documentación interactiva en: **[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)**.  
Acceder al panel de administración en: **[http://127.0.0.1:8000/admin](http://127.0.0.1:8000/admin)** (login con `ADMIN_USERNAME` / `ADMIN_PASSWORD`).

### 5. Ejecutar los tests
```powershell
pip install -r requirements-dev.txt
pytest
```
Los tests no llaman a la IA (todo en `?mock=true`) y no necesitan `.env`: `tests/conftest.py` define variables de prueba y usa una base SQLite temporal, así que nunca tocan `app_data.db`. `tests/test_endpoints_integration.py` recorre el flujo mínimo del MVP (material → diagnóstico → propuestas → revisión → versión final → informe) y los errores de entrada de cada endpoint.

**CI:** [`.github/workflows/backend-tests.yml`](../.github/workflows/backend-tests.yml) corre `pytest` con Python 3.12 (la versión de `.python-version`, la misma que producción) en cada PR que toque `backend/` y en cada push a `main`. No usa secretos ni gasta tokens.

Para ver las métricas del motor de reglas sobre los materiales de ejemplo (sin consumir cuota):
```powershell
python -m scripts.benchmark_analysis --rules-only
```

---

## 🚀 Deploy en Railway

El repo ya incluye la configuración necesaria:

- `railway.json`: builder Railpack, comando de arranque (`uvicorn` en `0.0.0.0:$PORT`), healthcheck en `/api/v1/health` y **1 sola réplica** (SQLite + cooldowns en memoria no admiten más).
- `.python-version`: fija Python 3.12.

Pasos en el dashboard de Railway:

1. **New Project → Deploy from GitHub repo** → elegir `e18-innovalab/contenidos_accesibles` (rama `main`). En **Settings** del servicio, por ser un monorepo:
   - **Root Directory**: `/backend`
   - **Config File Path**: `/backend/railway.json` (Railway no lo busca dentro del Root Directory)
   - **Watch Paths**: `/backend/**`, para no redeployar con cambios de frontend, diseño u otras carpetas
2. **Volume**: agregar un Volume al servicio montado en `/data`.
3. **Variables**:
   ```env
   ENVIRONMENT=production
   ADMIN_USERNAME=admin
   ADMIN_PASSWORD=<contraseña fuerte>
   ADMIN_AUTH=ambos
   ADMIN_CLERK_PUBLISHABLE_KEY=<pk_test_... de la aplicación de Clerk del panel>
   ADMIN_CLERK_SECRET_KEY=<sk_test_...>
   ADMIN_CLERK_JWT_KEY=<JWT public key en PEM, pegada tal cual>
   ADMIN_GITHUB_ADMINS=JAMRdev,VictoriaMackenzie,malessan4
   ADMIN_GITHUB_VIEWERS=camposmatias,Silvanam1,Jos022
   TEST_WEB_URL=https://<sitio-de-render>.onrender.com
   AI_API_KEY=<API key de Groq>
   DB_ENCRYPTION_KEY=<generada con el comando Fernet de arriba>
   DATABASE_PATH=/data/app_data.db
   BACKEND_CORS_ORIGINS=https://<dominio-del-frontend>,http://localhost:5173
   ```
   `BACKEND_CORS_ORIGINS` acepta orígenes separados por comas o una lista JSON (`["https://a.app","http://localhost:5173"]`). Reemplaza la lista por defecto, así que hay que incluir los orígenes locales si se van a usar. Cada origen va sin `/` final ni ruta.

   `PORT` no se define: la inyecta Railway. Guardar `DB_ENCRYPTION_KEY` en un lugar seguro: si cambia, las API keys guardadas en la DB ya no se pueden desencriptar.
4. **Settings → Networking → Generate Domain**.
5. Verificar `https://<dominio>/api/v1/health` y, si hace falta, cargar conexiones de IA de respaldo desde `https://<dominio>/admin`.

La conexión de IA principal sale de `AI_API_KEY`: en cada arranque se crea o actualiza la conexión **"IL-1 gpt-oss-120b - PROD"** (Groq, `openai/gpt-oss-120b`) con prioridad 0, por delante de las de `/admin`. Editarla o borrarla desde el panel no persiste. Proveedor, modelo y nombre se pueden cambiar con `AI_PROVIDER`, `AI_MODEL` y `AI_CONNECTION_LABEL`.

Con `ENVIRONMENT=production` (o `staging`), `/docs`, `/redoc` y `/api/v1/openapi.json` piden la **sesión del panel**: sin sesión llevan a `/admin/login` y vuelven solas después de entrar (con GitHub vía Clerk o con `ADMIN_USERNAME` / `ADMIN_PASSWORD`). Las cuentas de solo lectura también pueden verlas. Los endpoints de la API siguen públicos y en `development` (localhost) la documentación queda abierta. `DOCS_USERNAME` / `DOCS_PASSWORD` ya no se usan (si quedaron cargadas, se ignoran).

#### Login del panel y de la web de pruebas

- `ADMIN_AUTH=ambos` ofrece Clerk (GitHub o usuario/contraseña) y, como respaldo, el formulario con `ADMIN_USERNAME` / `ADMIN_PASSWORD`. En local se usa `ADMIN_AUTH=local` (solo el usuario del `.env`, sin Clerk).
- Al arrancar con Clerk, el backend crea o actualiza en Clerk la cuenta `ADMIN_USERNAME` con `ADMIN_PASSWORD` (aparece en el log).
- Los usuarios de GitHub y su rol (admin o solo lectura) se cargan desde `ADMIN_GITHUB_ADMINS` / `ADMIN_GITHUB_VIEWERS` y después se administran en la pestaña **Usuarios** de `/admin`.
- La web de pruebas (`TEST_WEB_URL`) pide el mismo login: toda llamada a la API desde ese origen necesita sesión. Con solo lectura, la web funciona únicamente en modo mock.

### Rate limit de IA

Como la API es pública, los endpoints que consumen IA real (`POST /analysis/sample`, `POST /analysis/pdf`, `POST /analysis/file`, `POST /adaptations` y `POST /ai/test-connection`) tienen un límite en memoria con ventanas deslizantes de 1 minuto y 24 horas. Al superarlo responden `429` con el header `Retry-After`. Las llamadas con `?mock=true` no cuentan.

| Variable | Default | Alcance |
| :--- | :--- | :--- |
| `AI_RATE_LIMIT_PER_IP_MINUTE` | `5` | Por IP, último minuto |
| `AI_RATE_LIMIT_PER_IP_DAY` | `50` | Por IP, últimas 24 h |
| `AI_RATE_LIMIT_GLOBAL_MINUTE` | `20` | Todas las IP, último minuto |
| `AI_RATE_LIMIT_GLOBAL_DAY` | `500` | Todas las IP, últimas 24 h |

`0` desactiva ese límite. El global protege la cuota aunque se falsee la IP (detrás del proxy, la IP sale de `X-Forwarded-For`). Los contadores se reinician con cada deploy.

---

## 🗄️ Cloudflare D1 (Worker pasarela)

La base de datos se va a migrar de SQLite (volumen de Railway) a **Cloudflare D1**. El backend sigue en Railway, fuera de Cloudflare: no puede usar el *binding* de D1 y la API REST de D1 comparte el límite general de la API de Cloudflare (1200 requests cada 5 min), insuficiente porque el backend escribe en cada llamada a la IA. Por eso habla con un **Worker propio** (`d1-gateway/`) que sí tiene el binding (#58). El cambio del backend a D1 llega con #59; hasta entonces el backend sigue usando SQLite.

| Endpoint del Worker | Uso |
| :--- | :--- |
| `GET /health` | Estado, sin token |
| `POST /query` | `{ "sql": "...", "params": [...] }`: una sentencia parametrizada |
| `POST /batch` | `{ "statements": [{ "sql", "params" }, ...] }`: varias sentencias atómicas (si una falla, no se aplica ninguna) |

`/query` y `/batch` exigen `Authorization: Bearer <D1_GATEWAY_TOKEN>` (comparación en tiempo constante; sin token correcto, 401). Solo aceptan sentencias con `?` y parámetros (texto, número, booleano o `null`), una por elemento. El token da acceso completo a la base: es un secreto del backend. Del lado del backend, `app/db/d1_client.py` (`D1Client`) agrega timeout y reintentos ante errores transitorios (red, 502/503/504).

**Local (sin cuenta de Cloudflare).** El backend sigue con SQLite (`DATABASE_BACKEND=sqlite`). Para probar el Worker con una D1 simulada:

```bash
cd d1-gateway
npm install
cp .dev.vars.example .dev.vars          # token local del Worker
npm run migrate:local                   # migraciones en la D1 simulada
npm run dev                             # Worker en http://127.0.0.1:8787
# en otra terminal, desde backend/:
D1_GATEWAY_URL=http://127.0.0.1:8787 D1_GATEWAY_TOKEN=token-local-de-prueba python scripts/d1_ping.py
```

Para probar desde local contra el Worker de Cloudflare, poner en el `.env` `D1_GATEWAY_URL` y `D1_GATEWAY_TOKEN` del Worker desplegado y correr `python scripts/d1_ping.py` (lecturas y escrituras con latencia p50/p95). `DATABASE_BACKEND=d1` hace que la app exija esas dos variables al arrancar.

**Producción (solo con `wrangler`).** Desde `d1-gateway/`, con la sesión de `wrangler` en la cuenta de Cloudflare del proyecto (`npx wrangler whoami` para confirmarla):

```bash
npx wrangler d1 create contenidos-accesibles     # copiar el database_id a wrangler.toml
npm run migrate:remote
npx wrangler secret put D1_GATEWAY_TOKEN         # el mismo valor que D1_GATEWAY_TOKEN en Railway
npm run deploy
```

**Despliegue actual:** Worker `contenidos-accesibles-d1-gateway` en `https://contenidos-accesibles-d1-gateway.jamrdev2.workers.dev`, base `contenidos-accesibles` (región ENAM). `account_id` y `database_id` están fijados en `wrangler.toml`, así que `wrangler` despliega siempre a esa cuenta.

**Latencia medida** con `scripts/d1_ping.py` (incluye ida y vuelta HTTPS hasta el Worker y la consulta en D1):

| Desde | Lectura (`/query`) p50 / p95 | Escritura (`/batch` de 2) p50 / p95 | Fecha |
| :--- | :--- | :--- | :--- |
| Local (Argentina) | 164 / 171 ms | 191 / 199 ms | 15/10/2026 |
| Railway (backend) | pendiente: correr `python scripts/d1_ping.py` en la consola del servicio | | |

Con ~160 ms por consulta (contra microsegundos en SQLite local), en #59 conviene agrupar escrituras en `batch` y evitar consultas en serie dentro de una misma request.

Los tests unitarios del cliente corren sin red. El de integración usa el Worker local si se define `D1_GATEWAY_TEST_URL` (ver `tests/test_d1_client.py`).

---

## 🧪 Web de pruebas (Render)

`web/` es una página estática (HTML + JS, sin build ni dependencias) para recorrer el flujo completo de la API con texto o un archivo PDF, Word o PowerPoint:

1. Extracción opcional (`/documents/process-text`, `/documents/upload`).
2. Diagnóstico (`/analysis/sample`, `/analysis/file`) o recuperación por `analysis_id` (`GET /analysis/{id}`).
3. Propuesta de adaptación por barrera (`POST /adaptations`), con tipo y fragmento opcional.
4. Revisión docente: aceptar, editar, descartar y deshacer (`/adaptations/{id}/decision`, `/undo`).
5. Registro de decisiones (`GET /adaptations/decisions`).
6. Versión adaptada (`GET /adaptations/final-version`): original y versión final lado a lado con los cambios resaltados, para copiar o descargar como `.txt`. Se regenera sola al cambiar una decisión.
7. Informe final (`GET /adaptations/report`): puntaje antes → después, resumen, decisión por barrera y penalizaciones de reglas antes/después, descargable como `.txt`. También se regenera solo.

Si el material tiene posibles datos personales (`422 sensitive_data_detected`), la página muestra los hallazgos enmascarados y permite descartar los que no lo son y reintentar (`?dismissed=`).

Cada llamada queda en un registro con el request, la respuesta JSON y el `curl` equivalente. El modo mock (`?mock=true`) viene activado para no gastar cuota; se desmarca para usar la IA real.

**Login.** La página pide el mismo acceso que el panel (Clerk o el usuario del `.env`) y recibe un token de 8 h que manda en cada llamada (`Authorization: Bearer`). El backend lo exige a toda llamada cuyo `Origin` sea `TEST_WEB_URL`. Las cuentas de solo lectura solo pueden usar el modo mock. Si el backend no tiene `TEST_WEB_URL` (por ejemplo, en local sin configurarla), la página no pide login.

**Deploy (Static Site gratuito, no se duerme):**

1. En Render: **New → Blueprint** → elegir `e18-innovalab/contenidos_accesibles` con *Blueprint Path* `backend/render.yaml`. Crea el sitio `innovalab-web-pruebas` desde `backend/web/`. (Alternativa manual: **New → Static Site**, *Root Directory* `backend/web`, *Publish Directory* `.`, sin build command.)
2. En Railway, agregar la variable del backend con la URL que asignó Render:
   ```env
   TEST_WEB_URL=https://<sitio>.onrender.com
   ```
   Con eso el origen de la web se suma a CORS (no hace falta repetirlo en `BACKEND_CORS_ORIGINS`) y `/admin` muestra el enlace **"Web de pruebas"**, que la abre apuntando a ese mismo backend (`?api=<backend>`).

La URL del backend también se puede cambiar desde la propia página (queda guardada en el navegador); el valor por defecto está en `web/config.js`.

**Local:** `python -m http.server 8765 --directory web` y en el `.env` del backend `TEST_WEB_URL=http://127.0.0.1:8765` (con eso la página también pide login, igual que en Render).

---

## 🗺️ Roadmap de Backend (Plan de 12 Semanas)

- [x] **Semana 0 (Sprint Planning):** Setup del entorno, arquitectura modular, configuración de CORS y validación de Gemini API.
- [x] **Semana 0 (Infra adicional):** Panel de administración (`/admin`) para consumo de IA y rotación automática de API keys con cifrado Fernet.
- [x] **Semana 1 (Sprint 1 - Exploración):** Pipeline de procesamiento en memoria de texto y PDFs (`PyMuPDF`), primeras pruebas de análisis estructurado con IA y documentación de riesgos técnicos (`docs/`).
- [x] **Semana 2-3 (Sprint 1/2 - Flujo Completo e Ideación):** Endpoint unificado PDF -> Diagnóstico, motor de reglas automáticas en Python + lógica de puntaje orientativo (0-100) y modo Mock para Frontend.
- [ ] **Semana 4 (Sprint 2 - Escáner Funcional):** Clasificación completa de barreras por categoría y registro de resultados.
- [ ] **Semana 5-6 (Sprint 3 - Desarrollo):** Generación de adaptaciones con IA (lenguaje claro, consignas en pasos, descripciones de imágenes).
- [ ] **Semana 7-8 (Sprint 4 - Consolidación):** Consolidación de informe final y exportación del material adaptado.
- [ ] **Semana 9-10 (Sprint 5 - Iteración):** Optimización de latencias, prompts estructurados y mitigación de falsos positivos.
- [ ] **Semana 11-12 (Sprint 6 - Cierre):** Entorno estable y validaciones para el **Demo Day**.
