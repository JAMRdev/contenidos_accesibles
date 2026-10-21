"""
Nombre y email de los docentes de Clerk para el panel /admin (Issue #104).

El token de sesión solo trae el id del usuario. Para mostrar quién es, el panel lee el usuario en
la API de Clerk de la aplicación de docentes (USERS_CLERK_SECRET_KEY) y lo cachea en api_users:
nunca se consulta en los requests de la API, solo después de que el panel pide las métricas
(en segundo plano) y para los usuarios sin datos o con el caché vencido (PROFILE_TTL).
"""
import logging
from datetime import datetime, timedelta, timezone

from clerk_backend_api import Clerk

from app.core.config import settings
from app.db import user_activity_repository as user_activity

PROFILE_TTL = timedelta(days=7)
# Tope por ronda: el panel pide métricas cada pocos segundos y no hace falta traer todos de una
MAX_PER_REFRESH = 20
API_TIMEOUT_MS = 5000

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return settings.USERS_CLERK_SECRET_KEY is not None and bool(settings.USERS_CLERK_SECRET_KEY.get_secret_value())


def _client() -> Clerk:
    return Clerk(bearer_auth=settings.USERS_CLERK_SECRET_KEY.get_secret_value())


def _profile(user) -> dict:
    name = " ".join(p for p in (user.first_name, user.last_name) if p) or user.username or None
    emails = user.email_addresses or []
    primary = next((e for e in emails if e.id == user.primary_email_address_id), emails[0] if emails else None)
    return {"name": name, "email": primary.email_address if primary else None, "avatar_url": user.image_url}


def refresh_stale_profiles(now: datetime | None = None) -> int:
    """Trae de Clerk los perfiles faltantes o vencidos. Devuelve cuántos actualizó; nunca lanza."""
    if not enabled():
        return 0
    now = now or datetime.now(timezone.utc)
    try:
        pending = user_activity.users_without_profile(now - PROFILE_TTL, MAX_PER_REFRESH)
        if not pending:
            return 0
        client = _client()
    except Exception:  # noqa: BLE001
        logger.warning("No se pudieron buscar los perfiles de docentes a actualizar", exc_info=True)
        return 0
    updated = 0
    for user_id in pending:
        try:
            user = client.users.get(user_id=user_id, timeout_ms=API_TIMEOUT_MS)
        except Exception as e:  # noqa: BLE001 - red, key o usuario borrado en Clerk
            if getattr(e, "status_code", None) == 404:
                # Ya no existe en Clerk: se marca para no volver a pedirlo hasta que venza el caché
                user_activity.save_profile(user_id, name=None, email=None, avatar_url=None, fetched_at=now)
                continue
            logger.warning("No se pudo leer el perfil de %s en Clerk: %s", user_id, e)
            break  # Clerk no responde: se reintenta en la próxima ronda
        user_activity.save_profile(user_id, **_profile(user), fetched_at=now)
        updated += 1
    return updated
