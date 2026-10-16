"""
Panel de administración (/admin) con Obelisco V2.

Login con Clerk (GitHub o usuario/contraseña del .env) y, como respaldo, el formulario propio
con las credenciales del .env. Después del login, el panel usa su propia cookie de sesión
(app/admin/session.py). /adminv2 (nombre usado mientras se validaba el diseño) redirige acá.

Roles: 'admin' modifica todo y administra usuarios; 'viewer' (solo lectura) ve todo menos la
zona de usuarios. Los POST además verifican el origen (verify_same_origin).
"""
import hashlib
import json
from pathlib import Path
from urllib.parse import quote, urlencode

from typing import Literal

from clerk_backend_api.security.types import TokenVerificationError
from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from app.admin import clerk_auth, session
from app.core.config import settings
from app.core.security import verify_same_origin
from app.db import admin_member_repository as members
from app.db import ai_usage_repository, file_event_repository
from app.db import api_key_repository as repo
from app.services import admin_metrics_service, status_service
from app.services.llm.providers import PRESETS, presets_as_dicts

PREFIX = "/admin"
LEGACY_PREFIX = "/adminv2"

router = APIRouter(include_in_schema=False)
# /adminv2 → /admin: los enlaces guardados mientras se validaba el panel siguen funcionando
legacy_router = APIRouter(include_in_schema=False)

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# Archivos estáticos del panel (públicos: los necesita el login). Solo los de esta lista.
_static_dir = Path(__file__).parent / "static"
STATIC_FILES = {"admin.css": "text/css", "admin.js": "text/javascript"}

# Diagramas de arquitectura generados con Archify (fuentes en docs/diagramas/).
# El slug de la URL solo se busca en este diccionario: nunca se arma una ruta
# de archivo con input del usuario.
_diagrams_dir = Path(__file__).parent / "diagrams"
DIAGRAMS: dict[str, tuple[str, str]] = {
    "arquitectura": ("Arquitectura en runtime", "Front, Railway, FastAPI, servicios, proveedores de IA y SQLite."),
    "secuencia-analisis-pdf": ("Secuencia de POST /api/v1/analysis/pdf y /file", "Extracción (PDF, Word o PowerPoint), reglas, IA con rotación y respuesta."),
    "rotacion-keys": ("Rotación de conexiones de IA", "Cómo se elige, usa y excluye cada conexión."),
    "ciclo-vida-conexion": ("Ciclo de vida de una conexión", "Estados standby, active, near-limit, exhausted y disabled."),
}


def _parse_extra_params(raw: str) -> dict:
    if not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"Parámetros extra: JSON inválido ({e.msg}).")
    if not isinstance(value, dict):
        raise HTTPException(status_code=400, detail="Parámetros extra: debe ser un objeto JSON {...}.")
    return value


def _clean_provider(provider: str) -> str:
    provider = provider.strip().lower()
    if not provider:
        raise HTTPException(status_code=400, detail="El proveedor es obligatorio.")
    return provider


def _test_web_link(request: Request) -> str | None:
    """Enlace a la web de pruebas apuntando a este mismo backend (?api=<origen>)."""
    if not settings.TEST_WEB_URL:
        return None
    api_origin = str(request.base_url).rstrip("/")
    separator = "&" if "?" in settings.TEST_WEB_URL else "?"
    return f"{settings.TEST_WEB_URL.strip()}{separator}{urlencode({'api': api_origin})}"


def _asset_version() -> str:
    """Hash corto del contenido de los estáticos: cambia la URL (?v=) en cada cambio y evita la caché vieja."""
    digest = hashlib.sha256()
    for name in sorted(STATIC_FILES):
        digest.update((_static_dir / name).read_bytes())
    return digest.hexdigest()[:10]


def _back() -> RedirectResponse:
    return RedirectResponse(f"{PREFIX}#conexiones", status_code=303)


def _login_redirect(request: Request) -> RedirectResponse:
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    return RedirectResponse(f"{PREFIX}/login?next={quote(target)}", status_code=303)


# Destinos permitidos después del login: el panel y la documentación de la API
_NEXT_PREFIXES = (PREFIX, "/docs", "/redoc")


def _safe_next(value: str | None) -> str:
    """Solo redirige dentro del panel o a la documentación: evita usar el login como redirección abierta."""
    if (
        value
        and value.startswith(_NEXT_PREFIXES)
        and not value.startswith("//")
        and "\\" not in value
        and ".." not in value
    ):
        return value
    return PREFIX


def _page_context(request: Request, admin: session.Principal, active: str) -> dict:
    return {
        "admin": admin,
        "prefix": PREFIX,
        "asset_version": _asset_version(),
        "active": active,
        "test_web_url": _test_web_link(request),
        "environment": settings.ENVIRONMENT,
        "version": settings.VERSION,
    }


# ---------------------------------------------------------------------------
# Sesión
# ---------------------------------------------------------------------------

def _login_context(next_value: str | None, **extra) -> dict:
    return {
        "prefix": PREFIX,
        "asset_version": _asset_version(),
        "next": _safe_next(next_value),
        "bye": False,
        "error": None,
        "username": "",
        "local_enabled": settings.admin_local_login_enabled,
        "clerk_enabled": settings.admin_clerk_enabled,
        "clerk_publishable_key": settings.ADMIN_CLERK_PUBLISHABLE_KEY if settings.admin_clerk_enabled else None,
        "clerk_frontend_api": (
            clerk_auth.frontend_api(settings.ADMIN_CLERK_PUBLISHABLE_KEY) if settings.admin_clerk_enabled else None
        ),
        **extra,
    }


def _blocked_message(request: Request) -> str | None:
    wait = session.login_blocked_for(request)
    if not wait:
        return None
    return f"Demasiados intentos fallidos. Probá de nuevo en {max(1, round(wait / 60))} min."


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str | None = None, bye: bool = False):
    if session.current_admin(request):
        return RedirectResponse(_safe_next(next), status_code=303)
    return templates.TemplateResponse(request, "login.html", _login_context(next, bye=bye))


@router.post("/login", response_class=HTMLResponse)
def login(
    request: Request,
    username: str = Form(""),
    password: str = Form(""),
    next: str = Form(PREFIX),
    _origin_ok: None = Depends(verify_same_origin),
):
    """Login de respaldo con el usuario y la contraseña del .env."""
    if not settings.admin_local_login_enabled:
        raise HTTPException(status_code=404, detail="El login con usuario y contraseña está desactivado.")
    context = _login_context(next, username=username)
    blocked = _blocked_message(request)
    if blocked:
        context["error"] = blocked
        return templates.TemplateResponse(request, "login.html", context, status_code=429)
    if not session.credentials_ok(username, password):
        session.register_failed_login(request)
        context["error"] = "Usuario o contraseña incorrectos."
        return templates.TemplateResponse(request, "login.html", context, status_code=401)

    session.clear_failed_logins(request)
    response = RedirectResponse(_safe_next(next), status_code=303)
    session.set_session_cookie(response, session.local_subject())
    return response


class ClerkLogin(BaseModel):
    token: str
    next: str | None = None


@router.post("/auth/clerk")
def login_with_clerk(body: ClerkLogin, request: Request, _origin_ok: None = Depends(verify_same_origin)):
    """Canjea el token de sesión de Clerk por la cookie del panel, si la cuenta tiene acceso."""
    if not settings.admin_clerk_enabled:
        raise HTTPException(status_code=404, detail="El login con Clerk no está configurado.")
    blocked = _blocked_message(request)
    if blocked:
        raise HTTPException(status_code=429, detail=blocked)

    origin = str(request.base_url).rstrip("/")
    try:
        claims = clerk_auth.verify_session_token(body.token, origin)
        identity = clerk_auth.fetch_identity(claims["sub"])
    except TokenVerificationError:
        session.register_failed_login(request)
        raise HTTPException(status_code=401, detail="La sesión de Clerk no es válida. Volvé a iniciar sesión.")
    except clerk_auth.ClerkLoginError as e:
        session.register_failed_login(request)
        raise HTTPException(status_code=403, detail=str(e))
    except Exception:
        raise HTTPException(status_code=502, detail="No se pudo consultar Clerk. Probá de nuevo en unos minutos.")

    if identity.kind == "local":
        subject = session.local_subject()
    else:
        member = members.get_by_username(identity.username)
        if not member or not member["enabled"]:
            session.register_failed_login(request)
            raise HTTPException(
                status_code=403,
                detail=f"La cuenta de GitHub @{identity.username} no tiene acceso al panel. Pedile acceso a un admin.",
            )
        members.record_login(
            member["id"], display_name=identity.display_name, avatar_url=identity.avatar_url,
            clerk_user_id=identity.clerk_user_id,
        )
        subject = session.github_subject(member["github_username"])

    session.clear_failed_logins(request)
    response = JSONResponse({"redirect": _safe_next(body.next)})
    session.set_session_cookie(response, subject)
    return response


@router.post("/logout")
def logout(_origin_ok: None = Depends(verify_same_origin)):
    # bye=1 hace que la página de login cierre también la sesión de Clerk
    response = RedirectResponse(f"{PREFIX}/login?bye=1", status_code=303)
    session.clear_session_cookie(response)
    return response


# ---------------------------------------------------------------------------
# Páginas
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request):
    admin = session.current_admin(request)
    if admin is None:
        return _login_redirect(request)
    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            **_page_context(request, admin, "panel"),
            "presets_json": presets_as_dicts(),
            "provider_options": [(p.id, p.label) for p in PRESETS],
            "diagrams": DIAGRAMS,
            "retention_days": settings.AI_USAGE_RETENTION_DAYS,
            "analysis_ttl_hours": settings.ANALYSIS_TTL_HOURS,
            "clerk_enabled": settings.admin_clerk_enabled,
        },
    )


@router.get("/diagramas/{slug}", response_class=HTMLResponse)
def diagram_page(request: Request, slug: str):
    admin = session.current_admin(request)
    if admin is None:
        return _login_redirect(request)
    if slug not in DIAGRAMS:
        raise HTTPException(status_code=404, detail="Diagrama no encontrado.")
    title, description = DIAGRAMS[slug]
    return templates.TemplateResponse(
        request,
        "diagram.html",
        {
            **_page_context(request, admin, "diagramas"),
            "slug": slug,
            "title": title,
            "description": description,
            "diagrams": DIAGRAMS,
        },
    )


@router.get("/diagramas/{slug}/contenido")
def diagram_content(slug: str, admin: session.Principal = Depends(session.require_admin)):
    if slug not in DIAGRAMS:
        raise HTTPException(status_code=404, detail="Diagrama no encontrado.")
    return FileResponse(_diagrams_dir / f"{slug}.html", media_type="text/html")


@router.get("/static/{name}")
def static_file(name: str):
    if name not in STATIC_FILES:
        raise HTTPException(status_code=404, detail="Archivo no encontrado.")
    return FileResponse(_static_dir / name, media_type=STATIC_FILES[name])


# ---------------------------------------------------------------------------
# Datos y acciones (JSON / formularios)
# ---------------------------------------------------------------------------

@router.get("/api/metrics")
def metrics(
    range_key: str = Query("24h", alias="range"),
    tz: int = Query(0, ge=-14 * 60, le=14 * 60, description="Minutos de getTimezoneOffset() del navegador."),
    admin: session.Principal = Depends(session.require_admin),
):
    if range_key not in admin_metrics_service.RANGES:
        raise HTTPException(status_code=400, detail="Rango inválido. Opciones: 24h, 7d, 30d.")
    ai_usage_repository.delete_expired()
    file_event_repository.delete_expired()
    return admin_metrics_service.build_metrics(range_key, tz)


@router.get("/api/status")
async def services_status(request: Request, admin: session.Principal = Depends(session.require_admin)):
    """Estado en vivo de los servicios conectados (con caché corta) y las URLs del proyecto."""
    return {
        **await status_service.get_status(),
        "links": status_service.links(str(request.base_url), PREFIX),
    }


@router.post("/keys")
def create_key(
    label: str = Form(...),
    key_value: str = Form(...),
    provider: str = Form(...),
    model: str = Form(...),
    base_url: str = Form(""),
    priority: int = Form(100),
    extra_params: str = Form(""),
    rpm_threshold: int | None = Form(None),
    tpm_threshold: int | None = Form(None),
    rpd_threshold: int | None = Form(None),
    tpd_threshold: int | None = Form(None),
    admin: session.Principal = Depends(session.require_editor),
    _origin_ok: None = Depends(verify_same_origin),
):
    repo.create(
        label=label.strip(),
        raw_key=key_value,
        provider=_clean_provider(provider),
        base_url=base_url.strip() or None,
        model=model.strip(),
        priority=priority,
        extra_params=_parse_extra_params(extra_params),
        rpm_threshold=rpm_threshold,
        tpm_threshold=tpm_threshold,
        rpd_threshold=rpd_threshold,
        tpd_threshold=tpd_threshold,
    )
    return _back()


@router.post("/keys/{key_id}/update")
def update_key(
    key_id: int,
    label: str = Form(...),
    provider: str = Form(...),
    model: str = Form(...),
    base_url: str = Form(""),
    priority: int = Form(100),
    extra_params: str = Form(""),
    key_value: str = Form(""),
    rpm_threshold: int | None = Form(None),
    tpm_threshold: int | None = Form(None),
    rpd_threshold: int | None = Form(None),
    tpd_threshold: int | None = Form(None),
    enabled: bool = Form(False),
    admin: session.Principal = Depends(session.require_editor),
    _origin_ok: None = Depends(verify_same_origin),
):
    repo.update(
        key_id,
        label=label.strip(),
        provider=_clean_provider(provider),
        base_url=base_url.strip() or None,
        model=model.strip(),
        priority=priority,
        extra_params=_parse_extra_params(extra_params),
        rpm_threshold=rpm_threshold,
        tpm_threshold=tpm_threshold,
        rpd_threshold=rpd_threshold,
        tpd_threshold=tpd_threshold,
        enabled=enabled,
        raw_key=key_value or None,
    )
    return _back()


@router.post("/keys/{key_id}/enabled")
def set_key_enabled(
    key_id: int,
    enabled: bool = Form(False),
    admin: session.Principal = Depends(session.require_editor),
    _origin_ok: None = Depends(verify_same_origin),
):
    repo.set_enabled(key_id, enabled)
    return {"id": key_id, "enabled": enabled}


@router.post("/keys/{key_id}/delete")
def delete_key(
    key_id: int,
    admin: session.Principal = Depends(session.require_editor),
    _origin_ok: None = Depends(verify_same_origin),
):
    repo.delete(key_id)
    return _back()


# ---------------------------------------------------------------------------
# Usuarios del panel (solo admins)
# ---------------------------------------------------------------------------

class MemberCreate(BaseModel):
    github_username: str
    role: Literal["admin", "viewer"]


class MemberUpdate(BaseModel):
    role: Literal["admin", "viewer"] | None = None
    enabled: bool | None = None


def _members_payload() -> dict:
    return {
        "local_account": {
            "username": settings.ADMIN_USERNAME,
            "role": "admin",
            "local_login": settings.admin_local_login_enabled,
            "clerk": settings.admin_clerk_enabled,
        },
        "members": members.list_all(),
    }


def _member_or_404(member_id: int) -> dict:
    member = members.get(member_id)
    if member is None:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    return member


def _not_yourself(member: dict, admin: session.Principal) -> None:
    if admin.kind == "github" and member["github_username"].lower() == admin.username.lower():
        raise HTTPException(status_code=409, detail="No podés cambiar tu propio acceso: pedíselo a otro admin.")


@router.get("/api/members")
def list_members(admin: session.Principal = Depends(session.require_editor)):
    return _members_payload()


@router.post("/api/members", status_code=201)
def add_member(
    body: MemberCreate,
    admin: session.Principal = Depends(session.require_editor),
    _origin_ok: None = Depends(verify_same_origin),
):
    try:
        members.add(body.github_username, body.role, added_by=admin.username)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return _members_payload()


@router.patch("/api/members/{member_id}")
def update_member(
    member_id: int,
    body: MemberUpdate,
    admin: session.Principal = Depends(session.require_editor),
    _origin_ok: None = Depends(verify_same_origin),
):
    member = _member_or_404(member_id)
    _not_yourself(member, admin)
    if body.role is not None:
        members.set_role(member_id, body.role)
    if body.enabled is not None:
        members.set_enabled(member_id, body.enabled)
    return _members_payload()


@router.delete("/api/members/{member_id}")
def delete_member(
    member_id: int,
    admin: session.Principal = Depends(session.require_editor),
    _origin_ok: None = Depends(verify_same_origin),
):
    member = _member_or_404(member_id)
    _not_yourself(member, admin)
    members.delete(member_id)
    return _members_payload()


@legacy_router.api_route("", methods=["GET", "HEAD"])
@legacy_router.api_route("/{rest:path}", methods=["GET", "HEAD"])
def legacy_redirect(request: Request, rest: str = ""):
    """/adminv2/... → /admin/... (permanente), conservando la consulta."""
    target = f"{PREFIX}/{rest}" if rest else f"{PREFIX}/"
    if request.url.query:
        target += f"?{request.url.query}"
    return RedirectResponse(target, status_code=308)
