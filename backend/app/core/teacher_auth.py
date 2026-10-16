"""
Login de docentes con Clerk (Issue #60).

El Frontend inicia sesión con Clerk (Google o Microsoft) y manda el token de sesión en cada
request (`Authorization: Bearer <token>`). Esta dependencia lo valida sin llamadas de red, con
la clave pública de la aplicación de docentes (CLERK_JWT_KEY), igual que el panel valida el suyo
en app/admin/clerk_auth.py:

- Solo se aceptan tokens de sesión (los de máquina no aplican).
- El token tiene que haber sido emitido para uno de los orígenes del Frontend (authorized_parties
  = orígenes de CORS): un token robado no sirve desde otro sitio.

Sin CLERK_JWT_KEY no se exige nada (local y hasta que el Frontend esté listo). Las llamadas de la
web de pruebas ya llegan validadas por su propio login (app/admin/test_web_auth.py) y pasan.
"""
from clerk_backend_api.security.types import (
    TokenType,
    TokenVerificationError,
    TokenVerificationErrorReason,
    VerifyTokenOptions,
)
from clerk_backend_api.security.verifytoken import get_token_type, verify_token
from fastapi import HTTPException, Request, status

from app.core.config import settings


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _bearer_token(request: Request) -> str | None:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


def require_teacher(request: Request) -> None:
    """Dependencia: 401 si el login de docentes está activo y el request no trae un token válido."""
    if not settings.teacher_auth_enabled:
        return
    if getattr(request.state, "test_web_principal", None) is not None:
        return

    token = _bearer_token(request)
    if token is None:
        raise _unauthorized("Iniciá sesión para usar la API.")
    try:
        if get_token_type(token) != TokenType.SESSION_TOKEN:
            raise TokenVerificationError(TokenVerificationErrorReason.INVALID_TOKEN_TYPE)
        claims = verify_token(
            token,
            VerifyTokenOptions(jwt_key=settings.CLERK_JWT_KEY, authorized_parties=settings.cors_origins),
        )
    except TokenVerificationError:
        raise _unauthorized("La sesión no es válida o venció. Volvé a iniciar sesión.")

    # Id de usuario de Clerk, para cuando los recursos pasen a ser por docente (#61, app/core/owner.py)
    request.state.teacher_user_id = claims["sub"]
