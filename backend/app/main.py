import asyncio
import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.admin.router import LEGACY_PREFIX as ADMIN_LEGACY_PREFIX
from app.admin.router import PREFIX as ADMIN_PREFIX
from app.admin.router import legacy_router as admin_legacy_router
from app.admin.router import router as admin_router
from app.api import share_links
from app.api.v1.router import api_router
from app.core.config import settings
from app.core.docs import setup_protected_docs
from app.admin import clerk_auth, test_web_auth
from app.db import admin_member_repository, ai_usage_repository, analysis_repository
from app.db.database import init_db
from app.services import key_rotation_service, retention_service

# En development, Swagger/ReDoc/openapi.json quedan abiertos (rutas por defecto
# de FastAPI). En staging/production se desactivan y se registran de nuevo
# detrás de la sesión del panel (Clerk o el usuario del .env) con setup_protected_docs.
_openapi_url = f"{settings.API_V1_STR}/openapi.json"
_public_docs = not settings.docs_protected


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    key_rotation_service.sync_env_connection()
    analysis_repository.delete_expired()
    ai_usage_repository.delete_expired()
    admin_member_repository.seed(settings.admin_github_seed)
    # Cuenta del .env en Clerk: en segundo plano para no demorar el arranque si Clerk tarda
    if settings.admin_clerk_enabled:
        threading.Thread(
            target=lambda: logging.getLogger("app.admin").warning(clerk_auth.sync_env_admin()),
            name="clerk-sync-env-admin",
            daemon=True,
        ).start()
    # Limpieza de archivos vencidos (Issue #64): una sola réplica, así que alcanza con una tarea en el proceso
    cleanup = None
    if settings.FILE_CLEANUP_INTERVAL_MINUTES > 0:
        cleanup = asyncio.create_task(retention_service.cleanup_loop(settings.FILE_CLEANUP_INTERVAL_MINUTES))
    yield
    if cleanup:
        cleanup.cancel()


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="""
# 🧠 Asistente de Accesibilidad para Contenidos Educativos
*Innova Lab - Agencia de Habilidades para el Futuro*

API REST desarrollada con **FastAPI** y modelos de lenguaje de **cualquier proveedor**
(Gemini, Groq, Mistral, OpenRouter, Ollama o cualquier API compatible con OpenAI) para
analizar, diagnosticar y adaptar materiales educativos digitales (PDF, texto), reduciendo
barreras de accesibilidad y potenciando la labor pedagógica docente.

---

### 📌 Módulos de la API:
* **Health**: Verificación del estado del servidor, entorno y versión.
* **AI Integration**: Prueba de conectividad con la conexión de IA activa.
* **Documents**: Extracción de texto y PDF en memoria.
* **Accessibility Analysis**: Diagnóstico de accesibilidad con salidas estructuradas.

---

### 🛠️ Panel de Administración (`/admin`)

Fuera de esta API versionada (es HTML, no JSON) vive un panel interno, por eso no aparece como
path en este esquema OpenAPI. Se entra con **Clerk** (cuenta de GitHub del equipo o el usuario
`ADMIN_USERNAME`) o, como respaldo, con `ADMIN_USERNAME` / `ADMIN_PASSWORD` del `.env`
(`ADMIN_AUTH`: `local`, `clerk` o `ambos`). Hay dos roles: **admin** (todo) y **solo lectura**.
Permite:

* Ver métricas de uso de la IA (llamadas, tokens, latencia y errores por período) y del
  producto (diagnósticos, barreras y decisiones del docente), con gráficos accesibles.
* Cargar **conexiones de IA** sin límite de cantidad: proveedor, modelo, base URL,
  API key (cifrada en reposo con Fernet), prioridad, parámetros extra y umbrales
  propios de RPM/TPM/RPD/TPD. Incluye plantillas para los proveedores más usados.
* **Rotación automática entre proveedores**: se usa la conexión disponible de mayor
  prioridad; si alcanza su umbral, el proveedor la limita (429) o falla, el backend pasa
  a la siguiente —aunque sea de otro proveedor— sin caídas ni intervención manual.
* Administrar qué usuarios de GitHub entran y con qué rol.

Acceso: `http://<host>:<puerto>/admin`. Fuera de `development`, esta documentación también
pide iniciar sesión en el panel.
""",
    version=settings.VERSION,
    openapi_url=_openapi_url if _public_docs else None,
    docs_url="/docs" if _public_docs else None,
    redoc_url="/redoc" if _public_docs else None,
    debug=settings.DEBUG,
    lifespan=lifespan,
)

# La web de pruebas (TEST_WEB_URL) necesita iniciar sesión para usar la API. Se registra antes
# que CORS para que CORS quede por fuera y también agregue sus encabezados a los 401.
app.add_middleware(BaseHTTPMiddleware, dispatch=test_web_auth.require_test_web_session)

# Middleware de CORS: orígenes configurados en .env más el de la web de pruebas (TEST_WEB_URL)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Registro unificado de rutas bajo el prefijo /api/v1
app.include_router(api_router, prefix=settings.API_V1_STR)
# Login de la web de pruebas (fuera del esquema OpenAPI: no es parte del contrato de la API)
app.include_router(test_web_auth.router, prefix=settings.API_V1_STR)
# Enlaces para compartir archivos (/s/{token}, Issue #65): en la raíz para que sean cortos
app.include_router(share_links.router)

# Panel de administración (HTML interno: login con Clerk o con el usuario del .env)
app.include_router(admin_router, prefix=ADMIN_PREFIX)
# /adminv2 era la ruta del panel mientras se validaba el rediseño: redirige a /admin
app.include_router(admin_legacy_router, prefix=ADMIN_LEGACY_PREFIX)

if settings.docs_protected:
    setup_protected_docs(app, _openapi_url)


@app.get("/", tags=["Root"], summary="Punto de bienvenida de la API")
def root():
    """
    Ruta raíz informativa.
    Devuelve el estado del entorno activo y accesos directos a la documentación.
    """
    return {
        "message": "Bienvenido a la API de Accesibilidad Educativa (Innova Lab).",
        "environment": settings.ENVIRONMENT,
        "version": settings.VERSION,
        "docs": "/docs (requiere iniciar sesión en /admin)" if settings.docs_protected else "/docs",
        "health": f"{settings.API_V1_STR}/health",
    }
