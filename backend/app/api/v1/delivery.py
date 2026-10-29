"""
Entrega de archivos exportados (Issues #40 y #62), compartida por /export y /transformations:

- `delivery=file`: los bytes en la respuesta, con el Content-Disposition para descarga directa.
- `delivery=link`: guarda el archivo en el almacenamiento (prefijo users/<usuario>/exports/),
  lo registra en /files y devuelve una URL firmada de descarga de pocos minutos.
"""
import io
from typing import Literal

from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.db import file_event_repository as file_events
from app.db import file_repository as files
from app.schemas.file import FileDownloadResponse, FileInfo, PresignedRequest
from app.services import retention_service, storage_service
from app.services.storage_service import StorageError, new_object_key

Delivery = Literal["file", "link"]


def deliver(request: Request, data: bytes, content_type: str, filename: str, delivery: Delivery, owner: str,
             document: str, analysis_expires_at: str | None):
    file_events.record("export", format=file_events.file_format(filename), size_bytes=len(data), detail=f"{document}:{delivery}",
                       user_id=owner)
    if delivery == "file":
        return StreamingResponse(
            io.BytesIO(data),
            media_type=content_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    storage = storage_service.get_storage()
    key = new_object_key(owner, "exports", "." + filename.rsplit(".", 1)[-1])
    ttl = settings.FILE_DOWNLOAD_URL_TTL_S
    try:
        storage.put(key, data, content_type)
        url = storage.presign_get(key, filename, content_type, ttl)
    except StorageError:
        raise HTTPException(status_code=502, detail="El almacenamiento de archivos no respondió. Probá de nuevo en unos minutos.")
    record = files.create(
        user_id=owner, object_key=key, filename=filename, content_type=content_type, size_bytes=len(data),
        # Retención vigente (Issue #64), pero nunca después que su diagnóstico
        ttl_hours=retention_service.file_retention_hours(), kind="export", status="uploaded",
        not_after=analysis_expires_at,
    )
    if url.startswith("/"):
        url = f"{str(request.base_url).rstrip('/')}{url}"
    return FileDownloadResponse(
        file=FileInfo(file_id=record["id"], **{k: record[k] for k in FileInfo.model_fields if k != "file_id"}),
        download=PresignedRequest(url=url, method="GET", headers={}, expires_in_seconds=ttl),
    )
