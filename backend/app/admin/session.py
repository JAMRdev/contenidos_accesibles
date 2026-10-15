"""
Sesión del panel /admin (Clerk o el usuario del .env, en lugar del Basic Auth del navegador).

- La cookie identifica a la persona ("local:<usuario>" o "github:<usuario>"); el rol se
  consulta en cada request, así quitar o bajar el rol a alguien tiene efecto inmediato.
- Roles: 'admin' modifica todo y administra usuarios; 'viewer' solo ve.
- Cookie firmada con HMAC-SHA256, HttpOnly, SameSite=Strict y Secure fuera de development.
  Vale para todo el sitio: también protege /docs, /redoc y openapi.json.
- La web de pruebas (otro sitio) usa el mismo login pero recibe un token Bearer con
  propósito "test-web": un token de un propósito no sirve para el otro.
- La clave de firma se deriva de DB_ENCRYPTION_KEY y de las credenciales de admin:
  cambiar la contraseña cierra todas las sesiones abiertas.
- Límite de intentos fallidos por IP para frenar ataques de fuerza bruta.
"""
import base64
import hashlib
import hmac
import secrets
import time
from collections import deque
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from fastapi.responses import Response

from app.core.config import settings
from app.db import admin_member_repository as members

COOKIE_NAME = "admin_session"
# Cookie usada mientras el panel vivía en /adminv2: se borra al cerrar sesión
LEGACY_COOKIE_NAME = "adminv2_session"
SESSION_TTL_S = 8 * 60 * 60
MAX_FAILED_LOGINS = 5
# Tope para todos los clientes juntos: detrás del proxy de Railway la IP sale de
# X-Forwarded-For, que el cliente puede falsear para esquivar el límite por IP.
MAX_FAILED_LOGINS_GLOBAL = 30
FAILED_LOGIN_WINDOW_S = 10 * 60
_GLOBAL_KEY = "*"

_failed_logins: dict[str, deque[float]] = {}


def _signing_key() -> bytes:
    material = "|".join(
        (
            settings.DB_ENCRYPTION_KEY.get_secret_value(),
            settings.ADMIN_USERNAME,
            settings.ADMIN_PASSWORD.get_secret_value(),
        )
    )
    return hashlib.sha256(f"admin-session|{material}".encode()).digest()


def _sign(payload: str) -> str:
    return hmac.new(_signing_key(), payload.encode(), hashlib.sha256).hexdigest()


def _b64(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode()).decode().rstrip("=")


def _unb64(value: str) -> str:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode()


@dataclass(frozen=True)
class Principal:
    """Persona con sesión en el panel."""

    subject: str
    kind: str  # "local" o "github"
    username: str
    role: str  # "admin" o "viewer"
    display_name: str
    avatar_url: str | None = None

    @property
    def can_edit(self) -> bool:
        return self.role == "admin"

    def as_public_dict(self) -> dict:
        return {
            "kind": self.kind,
            "username": self.username,
            "role": self.role,
            "display_name": self.display_name,
            "avatar_url": self.avatar_url,
            "can_edit": self.can_edit,
        }


def local_subject() -> str:
    return f"local:{settings.ADMIN_USERNAME}"


def github_subject(username: str) -> str:
    return f"github:{username}"


PANEL = "panel"
TEST_WEB = "test-web"


def create_token(subject: str, now: float | None = None, purpose: str = PANEL) -> str:
    expires = int((time.time() if now is None else now) + SESSION_TTL_S)
    payload = _b64(f"{subject}|{expires}|{secrets.token_hex(8)}|{purpose}")
    return f"{payload}.{_sign(payload)}"


def token_expires_at(token: str) -> int | None:
    try:
        return int(_unb64(token.rsplit(".", 1)[0]).split("|")[1])
    except (ValueError, IndexError, UnicodeDecodeError):
        return None


def read_token(token: str | None, now: float | None = None, purpose: str = PANEL) -> str | None:
    """Identidad de la sesión ("local:..." o "github:..."), o None si falta, está alterada, venció o es de otro propósito."""
    if not token or "." not in token:
        return None
    payload, signature = token.rsplit(".", 1)
    if not hmac.compare_digest(signature, _sign(payload)):
        return None
    try:
        subject, expires, _nonce, token_purpose = _unb64(payload).split("|")
        expires_at = int(expires)
    except (ValueError, UnicodeDecodeError):
        return None
    if token_purpose != purpose or expires_at < (time.time() if now is None else now):
        return None
    return subject


def resolve(subject: str | None) -> Principal | None:
    """Rol vigente de la identidad: la cuenta del .env es admin; GitHub según la tabla de usuarios."""
    if not subject or ":" not in subject:
        return None
    kind, username = subject.split(":", 1)
    if kind == "local":
        if username != settings.ADMIN_USERNAME:
            return None
        return Principal(subject, "local", username, "admin", username)
    if kind == "github":
        member = members.get_by_username(username)
        if not member or not member["enabled"]:
            return None
        return Principal(
            subject, "github", member["github_username"], member["role"],
            member["display_name"] or member["github_username"], member["avatar_url"],
        )
    return None


def current_admin(request: Request) -> Principal | None:
    return resolve(read_token(request.cookies.get(COOKIE_NAME)))


def require_admin(request: Request) -> Principal:
    """Cualquier persona con sesión (admin o solo lectura). Sin sesión responde 401."""
    principal = current_admin(request)
    if principal is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Iniciá sesión en el panel.")
    return principal


def require_editor(request: Request) -> Principal:
    """Solo rol admin: crear, editar o borrar conexiones y administrar usuarios."""
    principal = require_admin(request)
    if not principal.can_edit:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Tu usuario tiene acceso de solo lectura.")
    return principal


def credentials_ok(username: str, password: str) -> bool:
    user_ok = secrets.compare_digest(username.encode(), settings.ADMIN_USERNAME.encode())
    pass_ok = secrets.compare_digest(password.encode(), settings.ADMIN_PASSWORD.get_secret_value().encode())
    return user_ok and pass_ok


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "desconocida"


def _wait(key: str, limit: int, now: float) -> int:
    attempts = _failed_logins.get(key)
    if not attempts:
        return 0
    while attempts and attempts[0] <= now - FAILED_LOGIN_WINDOW_S:
        attempts.popleft()
    if len(attempts) < limit:
        return 0
    return max(1, int(attempts[-limit] + FAILED_LOGIN_WINDOW_S - now))


def login_blocked_for(request: Request, now: float | None = None) -> int:
    """Segundos que faltan para poder reintentar (0 si no está bloqueado), por IP o en total."""
    now = time.time() if now is None else now
    return max(
        _wait(_client_ip(request), MAX_FAILED_LOGINS, now),
        _wait(_GLOBAL_KEY, MAX_FAILED_LOGINS_GLOBAL, now),
    )


def register_failed_login(request: Request, now: float | None = None) -> None:
    now = time.time() if now is None else now
    for key in (_client_ip(request), _GLOBAL_KEY):
        _failed_logins.setdefault(key, deque()).append(now)


def clear_failed_logins(request: Request) -> None:
    _failed_logins.pop(_client_ip(request), None)


def reset() -> None:
    """Solo para tests."""
    _failed_logins.clear()


def set_session_cookie(response: Response, subject: str) -> None:
    response.set_cookie(
        COOKIE_NAME,
        create_token(subject),
        max_age=SESSION_TTL_S,
        path="/",
        httponly=True,
        samesite="strict",
        secure=settings.ENVIRONMENT != "development",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE_NAME, path="/", httponly=True, samesite="strict")
    # Cookies de cuando el panel vivía en /adminv2
    for path in ("/", "/adminv2"):
        response.delete_cookie(LEGACY_COOKIE_NAME, path=path, httponly=True, samesite="strict")
