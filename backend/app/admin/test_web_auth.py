"""
Login de la web de pruebas (Render): mismo acceso que el panel (Clerk o el usuario del .env).

La web de pruebas es otro sitio, así que no puede usar la cookie del panel. Canjea el login
por un token Bearer de 8 h (propósito "test-web") y lo manda en cada llamada.

El backend lo hace cumplir: toda llamada a /api/v1 cuyo Origin sea el de TEST_WEB_URL exige
ese token (el navegador no deja falsear Origin, así que la web de Render no se puede usar sin
iniciar sesión). Las llamadas desde otros orígenes siguen como antes: la API es pública hasta
que se implemente el login de docentes (#60). Con rol de solo lectura, la web solo puede usar
la IA en modo mock (ver limit_ai_usage).
"""
from clerk_backend_api.security.types import TokenVerificationError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.admin import clerk_auth, session
from app.core.config import settings
from app.db import admin_member_repository as members

PREFIX = "/test-web/auth"

router = APIRouter(prefix=PREFIX, include_in_schema=False)

# Rutas que la web de pruebas puede llamar sin token (antes de iniciar sesión)
_PUBLIC_PATHS = (f"{settings.API_V1_STR}{PREFIX}/", f"{settings.API_V1_STR}/health")


def principal_from_request(request: Request) -> session.Principal | None:
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return session.resolve(session.read_token(token.strip(), purpose=session.TEST_WEB))


async def require_test_web_session(request: Request, call_next):
    """Middleware: las llamadas desde la web de pruebas necesitan un token válido."""
    web = settings.test_web_origin
    path = request.url.path
    if (
        web
        and request.headers.get("origin") == web
        and request.method != "OPTIONS"
        and path.startswith(settings.API_V1_STR)
        and not path.startswith(_PUBLIC_PATHS)
    ):
        principal = principal_from_request(request)
        if principal is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Iniciá sesión en la web de pruebas para usar la API.", "code": "test_web_login_required"},
            )
        request.state.test_web_principal = principal
    return await call_next(request)


def _session_payload(subject: str) -> dict:
    principal = session.resolve(subject)
    token = session.create_token(subject, purpose=session.TEST_WEB)
    return {"token": token, "expires_at": session.token_expires_at(token), "user": principal.as_public_dict()}


def _blocked(request: Request) -> None:
    wait = session.login_blocked_for(request)
    if wait:
        raise HTTPException(status_code=429, detail=f"Demasiados intentos fallidos. Probá de nuevo en {max(1, round(wait / 60))} min.")


@router.get("/config")
def auth_config():
    """Qué opciones de login mostrar. `required` es False si el backend no tiene TEST_WEB_URL."""
    clerk = None
    if settings.admin_clerk_enabled:
        clerk = {
            "publishable_key": settings.ADMIN_CLERK_PUBLISHABLE_KEY,
            "frontend_api": clerk_auth.frontend_api(settings.ADMIN_CLERK_PUBLISHABLE_KEY),
        }
    return {"required": bool(settings.test_web_origin), "clerk": clerk, "local": settings.admin_local_login_enabled}


class LocalLogin(BaseModel):
    username: str
    password: str


@router.post("/login")
def login(body: LocalLogin, request: Request):
    if not settings.admin_local_login_enabled:
        raise HTTPException(status_code=404, detail="El login con usuario y contraseña está desactivado.")
    _blocked(request)
    if not session.credentials_ok(body.username, body.password):
        session.register_failed_login(request)
        raise HTTPException(status_code=401, detail="Usuario o contraseña incorrectos.")
    session.clear_failed_logins(request)
    return _session_payload(session.local_subject())


class ClerkLogin(BaseModel):
    token: str


@router.post("/clerk")
def login_with_clerk(body: ClerkLogin, request: Request):
    if not settings.admin_clerk_enabled or not settings.test_web_origin:
        raise HTTPException(status_code=404, detail="El login con Clerk no está configurado para la web de pruebas.")
    _blocked(request)
    try:
        # El token lo emite Clerk para la página de la web de pruebas: su azp es ese origen
        claims = clerk_auth.verify_session_token(body.token, settings.test_web_origin)
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
                detail=f"La cuenta de GitHub @{identity.username} no tiene acceso. Pedile acceso a un admin del panel.",
            )
        members.record_login(
            member["id"], display_name=identity.display_name, avatar_url=identity.avatar_url,
            clerk_user_id=identity.clerk_user_id,
        )
        subject = session.github_subject(member["github_username"])
    session.clear_failed_logins(request)
    return _session_payload(subject)


@router.get("/me")
def me(request: Request):
    principal = principal_from_request(request)
    if principal is None:
        raise HTTPException(status_code=401, detail="La sesión venció. Volvé a iniciar sesión.")
    token = request.headers["authorization"].partition(" ")[2].strip()
    return {"user": principal.as_public_dict(), "expires_at": session.token_expires_at(token)}
