"""
Transformaciones de material (Issue #116). La fila guarda la respuesta completa en payload_json
(como las propuestas de adaptación) y, aparte, las columnas por las que se filtra. Nunca el texto
del material: solo el resultado estructurado y la clave en R2 del archivo generado.
"""
import json
from datetime import datetime, timezone

from app.db.database import get_connection

_COLUMNS = "id, user_id, format, status, review_status, object_key, payload_json, created_at, updated_at, expires_at"


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def create(record: dict) -> None:
    with get_connection() as conn:
        conn.execute(
            f"INSERT INTO transformations ({_COLUMNS}) VALUES "
            "(:id, :user_id, :format, :status, :review_status, :object_key, :payload_json, "
            ":created_at, :updated_at, :expires_at)",
            record,
        )


def get(transformation_id: str) -> dict | None:
    """Sin verificar dueño ni vencimiento (lo hace el servicio)."""
    with get_connection() as conn:
        row = conn.execute(f"SELECT {_COLUMNS} FROM transformations WHERE id = ?", (transformation_id,)).fetchone()
    return dict(row) if row else None


def list_for_user(user_id: str, limit: int = 50) -> list[dict]:
    """Transformaciones vigentes del usuario, de la más reciente a la más antigua."""
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT {_COLUMNS} FROM transformations WHERE user_id = ? AND expires_at > ? "
            "ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (user_id, now_iso(), limit),
        ).fetchall()
    return [dict(r) for r in rows]


def update(
    transformation_id: str,
    *,
    expected_status: str,
    expected_review: str,
    status: str,
    review_status: str,
    object_key: str | None,
    payload_json: str,
) -> bool:
    """
    Guarda el nuevo estado solo si nadie lo cambió en el medio (otra decisión, o la tarea que
    genera): devuelve False y no toca nada si el estado ya no es el esperado.
    """
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE transformations SET status = ?, review_status = ?, object_key = ?, payload_json = ?, updated_at = ? "
            "WHERE id = ? AND status = ? AND review_status = ?",
            (status, review_status, object_key, payload_json, now_iso(), transformation_id, expected_status, expected_review),
        )
        return cur.rowcount > 0


def fail_interrupted(message: str) -> int:
    """
    Al arrancar: las que estaban generándose cuando se reinició el proceso no van a terminar
    (la tarea vivía en memoria). Pasan a 'failed' para que el docente pida otra.
    """
    now = now_iso()
    error = json.dumps({"code": "interrupted", "message": message})
    with get_connection() as conn:
        cur = conn.execute(
            "UPDATE transformations SET status = 'failed', updated_at = ?, "
            "payload_json = json_set(payload_json, '$.status', 'failed', '$.updated_at', ?, '$.error', json(?)) "
            "WHERE status IN ('pending', 'processing')",
            (now, now, error),
        )
        return cur.rowcount


def list_expired(now: str, limit: int = 200) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, object_key FROM transformations WHERE expires_at <= ? ORDER BY expires_at LIMIT ?",
            (now, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def delete(transformation_id: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM transformations WHERE id = ?", (transformation_id,))
