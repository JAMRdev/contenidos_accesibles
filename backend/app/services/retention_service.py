"""
Retención de los archivos de R2 y limpieza automática (Issue #64).

- La retención vale FILE_RETENTION_HOURS hasta que un admin la cambia desde /admin o con el
  permiso settings:retention. Se guarda en app_settings y nunca supera FILE_RETENTION_MAX_HOURS.
- Cada archivo calcula su expires_at al crearse: cambiar la retención solo afecta a los archivos
  nuevos. Los exportados además vencen, como mucho, con su diagnóstico.
- La API trata un archivo vencido como inexistente aunque todavía no se haya borrado
  (file_repository.get_for_user y share_repository.resolve filtran por expires_at).
- cleanup_files() borra del almacenamiento y de la base los archivos vencidos, las subidas que
  nunca se confirmaron y las transformaciones vencidas con su archivo generado (Issue #116). Corre cada FILE_CLEANUP_INTERVAL_MINUTES en el proceso del backend
  (app/main.py). La regla de lifecycle del bucket, con el máximo permitido, queda de respaldo.
"""
import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.db import file_repository, settings_repository
from app.services import storage_service
from app.services.storage_service import StorageError

RETENTION_KEY = "file_retention_hours"
LAST_CLEANUP_KEY = "file_cleanup_last_run"
_CLEANUP_BATCH = 200

logger = logging.getLogger(__name__)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stored_hours() -> dict | None:
    row = settings_repository.get(RETENTION_KEY)
    if not row:
        return None
    try:
        hours = int(row["value"])
    except ValueError:
        return None
    return {**row, "hours": hours} if hours >= 1 else None


def file_retention_hours() -> int:
    """Horas de vida de un archivo nuevo: la configurada en el panel o FILE_RETENTION_HOURS, con tope en el máximo."""
    stored = _stored_hours()
    hours = stored["hours"] if stored else settings.FILE_RETENTION_HOURS
    return min(hours, settings.FILE_RETENTION_MAX_HOURS)


def retention_info() -> dict:
    stored = _stored_hours()
    last_cleanup = settings_repository.get(LAST_CLEANUP_KEY)
    return {
        "hours": min(stored["hours"] if stored else settings.FILE_RETENTION_HOURS, settings.FILE_RETENTION_MAX_HOURS),
        "default_hours": settings.FILE_RETENTION_HOURS,
        "max_hours": settings.FILE_RETENTION_MAX_HOURS,
        "source": "panel" if stored else "env",
        "updated_at": stored["updated_at"] if stored else None,
        "updated_by": stored["updated_by"] if stored else None,
        "cleanup_interval_minutes": settings.FILE_CLEANUP_INTERVAL_MINUTES,
        "last_cleanup": json.loads(last_cleanup["value"]) if last_cleanup else None,
    }


def set_file_retention_hours(hours: int, *, updated_by: str) -> dict:
    """Cambia la retención de los archivos nuevos. ValueError si está fuera de 1..FILE_RETENTION_MAX_HOURS."""
    if not 1 <= hours <= settings.FILE_RETENTION_MAX_HOURS:
        raise ValueError(f"La retención tiene que estar entre 1 y {settings.FILE_RETENTION_MAX_HOURS} horas.")
    settings_repository.set(RETENTION_KEY, str(hours), updated_by=updated_by)
    return retention_info()


def reset_file_retention(*, updated_by: str) -> dict:
    """Vuelve al valor por defecto (FILE_RETENTION_HOURS)."""
    settings_repository.delete(RETENTION_KEY)
    logger.info("Retención de archivos restablecida al valor por defecto por %s", updated_by)
    return retention_info()


def cleanup_files(now: datetime | None = None) -> dict:
    """
    Borra del almacenamiento y de la base los archivos vencidos y las subidas nunca confirmadas
    (pendientes desde hace más que la vigencia de su URL de subida, con margen).
    Si el almacenamiento falla con un archivo, su registro queda para el próximo intento.
    """
    now = now or datetime.now(timezone.utc)
    pending_before = now - timedelta(seconds=settings.FILE_UPLOAD_URL_TTL_S) - timedelta(minutes=10)
    storage = storage_service.get_storage()
    result = {"ran_at": _iso(now), "expired": 0, "abandoned": 0, "errors": 0}
    while True:
        batch = file_repository.list_to_clean(_iso(now), _iso(pending_before), limit=_CLEANUP_BATCH)
        deleted_in_batch = 0
        for record in batch:
            try:
                storage.delete(record["object_key"])
            except StorageError as e:
                result["errors"] += 1
                logger.warning("No se pudo borrar %s del almacenamiento: %s", record["object_key"], e)
                continue
            file_repository.delete_with_shares(record["id"])
            deleted_in_batch += 1
            result["expired" if record["expires_at"] <= _iso(now) else "abandoned"] += 1
        # Lote incompleto o nada borrado (solo errores): no hay más que limpiar por ahora
        if len(batch) < _CLEANUP_BATCH or deleted_in_batch == 0:
            break
    # Transformaciones vencidas y su archivo generado (Issue #116)
    from app.services import transformation_service

    transformations = transformation_service.cleanup_expired(_iso(now))
    result["transformations"] = transformations["deleted"]
    result["errors"] += transformations["errors"]
    settings_repository.set(LAST_CLEANUP_KEY, json.dumps(result), updated_by=None)
    return result


async def cleanup_loop(interval_minutes: int) -> None:
    """Limpieza programada en el proceso del backend: al arrancar y cada interval_minutes."""
    while True:
        try:
            result = await asyncio.to_thread(cleanup_files)
            if result["expired"] or result["abandoned"] or result["errors"]:
                logger.info("Limpieza de archivos: %s", result)
        except Exception:
            logger.exception("Falló la limpieza de archivos; se reintenta en %s min", interval_minutes)
        await asyncio.sleep(interval_minutes * 60)
