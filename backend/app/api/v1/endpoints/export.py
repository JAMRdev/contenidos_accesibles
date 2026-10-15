"""
Endpoints de exportación de material adaptado e informe (Issue #40).

Genera los archivos en memoria (export_service.py) y los entrega de dos maneras:

- `delivery=file` (por defecto): los bytes en la respuesta, con el Content-Disposition
  para descarga directa.
- `delivery=link` (Issue #62): guarda el archivo en el almacenamiento (R2 en producción,
  prefijo users/<usuario>/exports/) y devuelve una URL firmada de descarga de pocos minutos.
  El archivo no vuelve a pasar por la API. Es el modo pensado para el frontend.
"""

import io
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.core.config import settings
from app.core.owner import current_owner
from app.db import file_repository as files
from app.schemas.file import FileDownloadResponse, FileInfo, PresignedRequest
from app.services import adaptation_service, export_service, storage_service
from app.services.adaptation_service import AdaptationNotFoundError
from app.services.storage_service import StorageError, new_object_key

router = APIRouter()

Delivery = Literal["file", "link"]
_DELIVERY_DESCRIPTION = (
    "'file' (por defecto): el archivo en la respuesta. 'link': JSON con una URL firmada de "
    "descarga de pocos minutos; el archivo queda guardado hasta su vencimiento."
)
_RESPONSES = {
    200: {
        "description": (
            "Con `delivery=file`, el archivo descargable. Con `delivery=link`, JSON como el de "
            "`GET /files/{file_id}/download-url`: `file` (metadatos) y `download` (URL firmada)."
        ),
    },
    400: {"description": "Formato no soportado."},
    404: {"description": "Diagnóstico no encontrado o expirado."},
    502: {"description": "Con `delivery=link`: el almacenamiento no respondió."},
}


def _deliver(request: Request, data: bytes, content_type: str, filename: str, delivery: Delivery, owner: str):
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
        ttl_hours=settings.FILE_TTL_HOURS, kind="export", status="uploaded",
    )
    if url.startswith("/"):
        url = f"{str(request.base_url).rstrip('/')}{url}"
    return FileDownloadResponse(
        file=FileInfo(file_id=record["id"], **{k: record[k] for k in FileInfo.model_fields if k != "file_id"}),
        download=PresignedRequest(url=url, method="GET", headers={}, expires_in_seconds=ttl),
    )


@router.get(
    "/content",
    summary="Exportar material adaptado (Issue #40)",
    response_model=None,
    responses=_RESPONSES,
)
def export_content(
    request: Request,
    analysis_id: str = Query(description="ID del diagnóstico cuyo material adaptado se desea exportar."),
    format: str = Query(default="docx", description="Formato de salida: 'docx', 'pdf', 'md' o 'txt'."),
    delivery: Delivery = Query(default="file", description=_DELIVERY_DESCRIPTION),
    owner: str = Depends(current_owner),
):
    """
    ## Exportación del material adaptado (Issue #40)

    Genera el material educativo con **solo los cambios aceptados o editados**
    por el docente y lo entrega como archivo descargable:

    - **`docx`** (por defecto): documento Word con tipografía accesible (Arial, interlineado 1.5).
    - **`pdf`**: documento PDF accesible con diseño maquetado en memoria (márgenes 20mm, tipografía clara).
    - **`md`**: Markdown con metadatos de análisis y el texto adaptado.
    - **`txt`**: texto plano UTF-8.

    El documento original **no se modifica**. Con `delivery=link` (Issue #62) el archivo se
    guarda en el almacenamiento y la respuesta trae una **URL firmada de descarga**.
    """
    try:
        adapted = adaptation_service.build_adapted_document(analysis_id)
    except AdaptationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        data, content_type = export_service.export_content(adapted, format)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    filename = export_service.build_filename("material_adaptado", analysis_id, format)
    return _deliver(request, data, content_type, filename, delivery, owner)


@router.get(
    "/report",
    summary="Exportar informe de accesibilidad (Issue #40)",
    response_model=None,
    responses=_RESPONSES,
)
def export_report(
    request: Request,
    analysis_id: str = Query(description="ID del diagnóstico cuyo informe se desea exportar."),
    format: str = Query(default="pdf", description="Formato de salida: 'pdf', 'md' o 'txt'."),
    delivery: Delivery = Query(default="file", description=_DELIVERY_DESCRIPTION),
    owner: str = Depends(current_owner),
):
    """
    ## Exportación del informe ejecutivo de accesibilidad (Issue #40)

    Genera el informe de cierre del proceso de adaptación e incluye:

    - **`pdf`** (por defecto): informe maquetado con tablas comparativas, métricas y disclaimer.
    - **`md`**: Markdown estructurado con tablas GitHub Flavored.
    - **`txt`**: texto plano legible UTF-8.

    Contenido del informe:
    - **Resumen ejecutivo** en lenguaje natural.
    - **Puntaje antes y después** de la adaptación (el puntaje posterior es
      calculado con el motor de reglas determinístico, sin consumo de tokens de IA).
    - **Detalle por barrera**: categoría, severidad y decisión docente.
    - **Métricas consolidadas** de barreras y propuestas.
    - **Disclaimer** de carácter orientativo del puntaje (WCAG).

    Con `delivery=link` (Issue #62) el archivo se guarda en el almacenamiento y la respuesta
    trae una **URL firmada de descarga**.
    """
    try:
        report = adaptation_service.build_final_report(analysis_id)
    except AdaptationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        data, content_type = export_service.export_report(report, format)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    filename = export_service.build_filename("informe_accesibilidad", analysis_id, format)
    return _deliver(request, data, content_type, filename, delivery, owner)
