"""Configuración editable desde /admin (Issue #64). Una fila por clave; sin fila, vale el default."""
from datetime import datetime, timezone

from app.db.database import get_connection


def get(key: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT key, value, updated_at, updated_by FROM app_settings WHERE key = ?", (key,)).fetchone()
    return dict(row) if row else None


def set(key: str, value: str, *, updated_by: str | None) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO app_settings (key, value, updated_at, updated_by) VALUES (?, ?, ?, ?)",
            (key, value, datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), updated_by),
        )


def delete(key: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (key,))
