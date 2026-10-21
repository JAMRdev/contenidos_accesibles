"""
Historial de uso por usuario y usuarios de la API para las estadísticas de /admin (Issue #104).

- user_activity: una fila por acción (diagnóstico, propuesta, decisión, archivo, enlace, llamada a
  la IA o 429 del rate limit) con sus metadatos: formato, tipo, tokens, proveedor. Nunca el
  material, los textos propuestos ni nombres de archivo.
- api_users: primer y último uso y rol de cada usuario, más el nombre y el email de Clerk
  cacheados (app/services/user_profile_service.py).

Las dos tablas son permanentes: no dependen de los diagnósticos ni de los archivos, que vencen, y
ninguna limpieza las borra. Registrar una acción nunca hace fallar la operación que la origina.
"""
import logging
from datetime import datetime, timezone

from app.core.permissions import ANONYMOUS_OWNER
from app.core.teacher_auth import caller_in_context
from app.db.database import get_connection

ACTIONS = (
    "analysis", "proposal", "decision",
    "upload", "upload_rejected", "export", "download", "share_created", "share_opened", "share_revoked",
    "ai_call", "rate_limited",
)
SOURCES = ("frontend", "test_web", "anon")
TEST_WEB_PREFIX = "test-web_"

_COLUMNS = (
    "format", "detail", "adaptation_type", "mock", "size_bytes", "score", "barriers",
    "provider", "model", "latency_ms", "tokens",
)
_SELECT = "created_at, user_id, action, " + ", ".join(_COLUMNS)
_USER_COLUMNS = (
    "user_id, source, role, name, email, avatar_url, first_seen_at, last_seen_at, profile_fetched_at"
)

logger = logging.getLogger(__name__)


def to_timestamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def source_of(user_id: str) -> str:
    """frontend (id de Clerk), test_web (test-web_<tipo>_<usuario>) o anon."""
    if user_id == ANONYMOUS_OWNER:
        return "anon"
    return "test_web" if user_id.startswith(TEST_WEB_PREFIX) else "frontend"


def record(
    action: str,
    user_id: str,
    *,
    format: str | None = None,
    detail: str | None = None,
    adaptation_type: str | None = None,
    mock: bool = False,
    size_bytes: int | None = None,
    score: int | None = None,
    barriers: int | None = None,
    provider: str | None = None,
    model: str | None = None,
    latency_ms: int | None = None,
    tokens: int = 0,
    created_at: datetime | None = None,
) -> None:
    """
    Registra una acción de user_id. Si la hace quien está logueado en este request (no, por ejemplo,
    alguien que abre su enlace compartido), también actualiza su último uso y su rol en api_users.
    """
    if action not in ACTIONS:
        raise ValueError(f"Acción inválida: {action!r}")
    now = to_timestamp(created_at or datetime.now(timezone.utc))
    values = (format, detail, adaptation_type, int(mock), size_bytes, score, barriers, provider, model, latency_ms, tokens)
    caller = caller_in_context()
    try:
        with get_connection() as conn:
            conn.execute(
                f"INSERT INTO user_activity (created_at, user_id, action, {', '.join(_COLUMNS)}) "
                f"VALUES (?, ?, ?, {', '.join('?' * len(_COLUMNS))})",
                (now, user_id, action, *values),
            )
            if caller is not None and caller.user_id == user_id:
                _touch(conn, user_id, caller.role, now)
    except Exception:  # noqa: BLE001 - una estadística no puede romper el diagnóstico, la subida ni la IA
        logger.warning("No se pudo registrar la acción %s del usuario", action, exc_info=True)


def record_for_caller(action: str, **fields) -> None:
    """Registra la acción a nombre de quien hace el request; nada fuera de un request de la API."""
    caller = caller_in_context()
    if caller is not None:
        record(action, caller.user_id, **fields)


def _touch(conn, user_id: str, role: str, now: str) -> None:
    conn.execute(
        """
        INSERT INTO api_users (user_id, source, role, first_seen_at, last_seen_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            role = excluded.role,
            last_seen_at = MAX(api_users.last_seen_at, excluded.last_seen_at)
        """,
        (user_id, source_of(user_id), role, now, now),
    )


def list_between(start: datetime, end: datetime, user_id: str | None = None) -> list[dict]:
    query = f"SELECT {_SELECT} FROM user_activity WHERE created_at >= ? AND created_at < ?"
    params: list = [to_timestamp(start), to_timestamp(end)]
    if user_id is not None:
        query += " AND user_id = ?"
        params.append(user_id)
    with get_connection() as conn:
        rows = conn.execute(query + " ORDER BY created_at, id", params).fetchall()
    return [dict(r) for r in rows]


def list_recent(user_id: str, limit: int = 50) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {_SELECT} FROM user_activity WHERE user_id = ? ORDER BY created_at DESC, id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def totals_by_user(user_id: str | None = None) -> list[dict]:
    """Totales históricos por usuario y acción: cantidad, de ellas en modo mock, y tokens."""
    query = (
        "SELECT user_id, action, detail, COUNT(*) AS n, SUM(mock) AS mock, SUM(tokens) AS tokens, "
        "MIN(created_at) AS first_at, MAX(created_at) AS last_at FROM user_activity"
    )
    params: tuple = ()
    if user_id is not None:
        query += " WHERE user_id = ?"
        params = (user_id,)
    with get_connection() as conn:
        rows = conn.execute(query + " GROUP BY user_id, action, detail", params).fetchall()
    return [dict(r) for r in rows]


def list_users() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(f"SELECT {_USER_COLUMNS} FROM api_users ORDER BY last_seen_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_user(user_id: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(f"SELECT {_USER_COLUMNS} FROM api_users WHERE user_id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def users_without_profile(fetched_before: datetime, limit: int) -> list[str]:
    """Usuarios de Clerk sin nombre ni email cacheados, o con el caché más viejo que fetched_before."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT user_id FROM api_users WHERE source = 'frontend' "
            "AND (profile_fetched_at IS NULL OR profile_fetched_at < ?) "
            "ORDER BY last_seen_at DESC LIMIT ?",
            (to_timestamp(fetched_before), limit),
        ).fetchall()
    return [r["user_id"] for r in rows]


def save_profile(user_id: str, *, name: str | None, email: str | None, avatar_url: str | None,
                 fetched_at: datetime | None = None) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE api_users SET name = ?, email = ?, avatar_url = ?, profile_fetched_at = ? WHERE user_id = ?",
            (name, email, avatar_url, to_timestamp(fetched_at or datetime.now(timezone.utc)), user_id),
        )
