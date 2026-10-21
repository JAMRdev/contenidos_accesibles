"""
Enlaces para compartir archivos (Issue #65). Se guarda el hash del token, nunca el token:
el enlace completo solo se conoce al crearlo.
"""
import hashlib
import secrets
from datetime import datetime, timezone

from app.db.database import get_connection

_COLUMNS = "id, file_id, created_by, created_at, expires_at, revoked_at, access_count, last_access_at"
# Largo de los tokens que se aceptan en /s/{token} (token_urlsafe(32) da 43 caracteres)
TOKEN_MIN_CHARS, TOKEN_MAX_CHARS = 40, 64


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create(*, file_id: str, created_by: str, expires_at: str) -> tuple[str, dict]:
    """Crea el enlace. Devuelve (token, registro): el token no se guarda y no se puede recuperar."""
    token = secrets.token_urlsafe(32)
    record = {
        "id": hash_token(token),
        "file_id": file_id,
        "created_by": created_by,
        "created_at": _now(),
        "expires_at": expires_at,
        "revoked_at": None,
        "access_count": 0,
        "last_access_at": None,
    }
    with get_connection() as conn:
        conn.execute(
            f"INSERT INTO file_shares ({_COLUMNS}) VALUES "
            "(:id, :file_id, :created_by, :created_at, :expires_at, :revoked_at, :access_count, :last_access_at)",
            record,
        )
    return token, record


def resolve(token: str) -> dict | None:
    """
    Enlace vigente para el token, con los datos del archivo para firmar la descarga; None si no
    existe, se revocó o venció (el enlace o el archivo), o si el archivo no terminó de subirse.
    """
    if not (TOKEN_MIN_CHARS <= len(token) <= TOKEN_MAX_CHARS):
        return None
    now = _now()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT s.id AS share_id, f.object_key, f.filename, f.content_type, f.kind, f.user_id "
            "FROM file_shares s JOIN files f ON f.id = s.file_id "
            "WHERE s.id = ? AND s.revoked_at IS NULL AND s.expires_at > ? "
            "AND f.expires_at > ? AND f.status = 'uploaded'",
            (hash_token(token), now, now),
        ).fetchone()
    return dict(row) if row else None


def record_access(share_id: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE file_shares SET access_count = access_count + 1, last_access_at = ? WHERE id = ?",
            (_now(), share_id),
        )


def list_active(file_id: str) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM file_shares WHERE file_id = ? AND revoked_at IS NULL AND expires_at > ? "
            "ORDER BY created_at",
            (file_id, _now()),
        ).fetchall()
    return [dict(r) for r in rows]


def revoke(file_id: str, share_id: str) -> bool:
    """Revoca el enlace (deja de funcionar de inmediato). False si no existe o ya estaba revocado."""
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE file_shares SET revoked_at = ? WHERE id = ? AND file_id = ? AND revoked_at IS NULL",
            (_now(), share_id, file_id),
        )
        return cur.rowcount > 0
