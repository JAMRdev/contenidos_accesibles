"""
Login del panel /admin con Clerk (aplicación de Clerk propia del panel, separada de la de docentes).

Clerk autentica (GitHub o usuario/contraseña) y el backend autoriza:
- El token de sesión se valida sin llamadas de red con la clave pública (ADMIN_CLERK_JWT_KEY)
  y solo si lo emitió el mismo origen del panel (authorized_parties).
- Con el id del token se lee el usuario en Clerk (una vez por login) para saber quién es:
  · Cuenta de GitHub verificada por OAuth → su rol sale de la tabla admin_members.
  · Usuario/contraseña → solo vale la cuenta del .env que crea este backend (external_id propio).
"""
import base64
import logging
from dataclasses import dataclass

from clerk_backend_api import Clerk
from clerk_backend_api.security.types import (
    TokenType,
    TokenVerificationError,
    TokenVerificationErrorReason,
    VerifyTokenOptions,
)
from clerk_backend_api.security.verifytoken import get_token_type, verify_token

from app.core.config import settings

logger = logging.getLogger(__name__)

# Marca la cuenta del .env creada por este backend: nadie puede ponerla desde el registro público.
ENV_ADMIN_EXTERNAL_ID = "contenidos-accesibles-env-admin"
GITHUB_PROVIDERS = {"oauth_github", "github"}
API_TIMEOUT_MS = 8000


class ClerkLoginError(Exception):
    """El token es válido pero la cuenta no puede entrar al panel (mensaje apto para mostrar)."""


@dataclass(frozen=True)
class ClerkIdentity:
    kind: str  # "local" (cuenta del .env) o "github"
    username: str
    display_name: str | None
    avatar_url: str | None
    clerk_user_id: str


def frontend_api(publishable_key: str | None) -> str | None:
    """Dominio de la Frontend API, codificado en la publishable key (pk_test_<base64>$)."""
    if not publishable_key or not publishable_key.startswith(("pk_test_", "pk_live_")):
        return None
    encoded = publishable_key.split("_", 2)[2]
    try:
        decoded = base64.b64decode(encoded + "=" * (-len(encoded) % 4)).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    return decoded.rstrip("$") or None


def _client() -> Clerk:
    return Clerk(bearer_auth=settings.ADMIN_CLERK_SECRET_KEY.get_secret_value())


def verify_session_token(token: str, origin: str) -> dict:
    """Claims del token de sesión. Lanza TokenVerificationError si no es válido para este origen."""
    # Solo tokens de sesión: los de máquina se validan contra la API de Clerk y acá no aplican
    if get_token_type(token) != TokenType.SESSION_TOKEN:
        raise TokenVerificationError(TokenVerificationErrorReason.INVALID_TOKEN_TYPE)
    return verify_token(
        token,
        VerifyTokenOptions(jwt_key=settings.ADMIN_CLERK_JWT_KEY, authorized_parties=[origin]),
    )


def _verified(account) -> bool:
    verification = getattr(account, "verification", None)
    return getattr(verification, "status", None) == "verified"


def identify(user) -> ClerkIdentity:
    """Quién es el usuario de Clerk para el panel (o ClerkLoginError si no puede entrar)."""
    name = " ".join(p for p in (user.first_name, user.last_name) if p) or None
    if user.external_id == ENV_ADMIN_EXTERNAL_ID and user.username == settings.ADMIN_USERNAME:
        return ClerkIdentity("local", settings.ADMIN_USERNAME, name, user.image_url, user.id)

    for account in user.external_accounts or []:
        if account.provider in GITHUB_PROVIDERS and account.username and _verified(account):
            return ClerkIdentity(
                "github",
                account.username,
                name or account.first_name or account.username,
                account.image_url or account.avatar_url or user.image_url,
                user.id,
            )
    raise ClerkLoginError("Entrá con tu cuenta de GitHub o con el usuario de administración del backend.")


def fetch_identity(user_id: str) -> ClerkIdentity:
    user = _client().users.get(user_id=user_id, timeout_ms=API_TIMEOUT_MS)
    return identify(user)


def sync_env_admin() -> str:
    """
    Crea o actualiza en Clerk la cuenta del .env (ADMIN_USERNAME / ADMIN_PASSWORD) para entrar
    con usuario y contraseña. No frena el arranque: devuelve un texto con el resultado para el log.
    """
    if not settings.admin_clerk_enabled:
        return "Clerk no configurado: se omite la sincronización del admin."
    client = _client()
    username = settings.ADMIN_USERNAME
    password = settings.ADMIN_PASSWORD.get_secret_value()
    try:
        found = client.users.list(request={"username": [username]}, timeout_ms=API_TIMEOUT_MS)
        if not found:
            client.users.create(
                username=username, password=password, external_id=ENV_ADMIN_EXTERNAL_ID,
                first_name="Administración", timeout_ms=API_TIMEOUT_MS,
            )
            return f"Clerk: se creó la cuenta '{username}' del .env."
        user = found[0]
        if user.external_id != ENV_ADMIN_EXTERNAL_ID:
            return (
                f"Clerk: el usuario '{username}' ya existe y no fue creado por este backend; "
                "no se usa para entrar al panel. Borralo en Clerk o cambiá ADMIN_USERNAME."
            )
        try:
            same = client.users.verify_password(user_id=user.id, password=password, timeout_ms=API_TIMEOUT_MS).verified
        except Exception:  # Clerk responde error si la contraseña no coincide
            same = False
        if same:
            return f"Clerk: la cuenta '{username}' del .env está al día."
        client.users.update(user_id=user.id, password=password, timeout_ms=API_TIMEOUT_MS)
        return f"Clerk: se actualizó la contraseña de '{username}' con la del .env."
    except Exception as e:  # red, credenciales o contraseña rechazada por Clerk (corta o filtrada)
        return f"Clerk: no se pudo sincronizar la cuenta '{username}' del .env ({e})."
