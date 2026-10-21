"""
Eventos de archivos y enlaces compartidos para las métricas del panel (Issue #98).

Una fila por evento con la fecha, el tipo, el formato y el tamaño: nunca el nombre del archivo,
su contenido ni ids. Registrar un evento nunca hace fallar la operación que lo origina. Las filas
se borran al superar AI_USAGE_RETENTION_DAYS, igual que el historial de la IA.
"""
import logging
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.db import user_activity_repository as user_activity
from app.db.database import get_connection

EVENTS = ("upload", "upload_rejected", "export", "download", "share_created", "share_opened", "share_revoked")

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"
logger = logging.getLogger(__name__)


def _to_timestamp(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime(_TIMESTAMP_FORMAT)


def file_format(filename: str | None) -> str | None:
    return filename.rsplit(".", 1)[-1].lower() if filename and "." in filename else None


def record(event: str, *, format: str | None = None, size_bytes: int | None = None, detail: str | None = None,
           user_id: str | None = None, created_at: datetime | None = None) -> None:
    """Registra el evento; con user_id (dueño del archivo) también va a su historial permanente (Issue #104)."""
    if event not in EVENTS:
        raise ValueError(f"Evento inválido: {event!r}")
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO file_events (created_at, event, format, size_bytes, detail) VALUES (?, ?, ?, ?, ?)",
                (_to_timestamp(created_at or datetime.now(timezone.utc)), event, format, size_bytes, detail),
            )
    except Exception:  # noqa: BLE001 - una métrica no puede romper la subida, el enlace ni la descarga
        logger.warning("No se pudo registrar el evento de archivo %s", event, exc_info=True)
    if user_id is not None:
        user_activity.record(event, user_id, format=format, size_bytes=size_bytes, detail=detail, created_at=created_at)


def delete_expired(retention_days: int | None = None) -> int:
    days = settings.AI_USAGE_RETENTION_DAYS if retention_days is None else retention_days
    cutoff = _to_timestamp(datetime.now(timezone.utc) - timedelta(days=days))
    with get_connection() as conn:
        return conn.execute("DELETE FROM file_events WHERE created_at < ?", (cutoff,)).rowcount


def list_between(start: datetime, end: datetime) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT created_at, event, format, size_bytes, detail FROM file_events "
            "WHERE created_at >= ? AND created_at < ? ORDER BY created_at",
            (_to_timestamp(start), _to_timestamp(end)),
        ).fetchall()
    return [dict(r) for r in rows]
