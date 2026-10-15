"""
Archivos de los docentes con subida directa al almacenamiento (Issue #62).

La API no transporta archivos: firma una URL de subida, el navegador sube directo a R2 y
después confirma. El backend verifica con HEAD que el objeto llegó con el tamaño y el tipo
declarados, y que la firma del archivo corresponde a su extensión.

    1. POST /files/upload-url        → registro 'pending' + URL firmada PUT (pocos minutos)
    2. PUT <url> (navegador → R2)    → con el header Content-Type que devolvió el paso 1
    3. POST /files/{id}/complete     → verifica y pasa a 'uploaded'

En desarrollo (STORAGE_BACKEND=local) la URL firmada apunta a /files/local/..., que guarda
en disco; ese camino responde 404 con R2.
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import FileResponse

from app.api.v1.uploads import ALLOWED_CONTENT_TYPES, MAX_UPLOAD_SIZE_BYTES, SUPPORTED_EXTENSIONS, check_signature
from app.core.config import settings
from app.core.owner import current_owner
from app.db import file_repository as files
from app.schemas.file import FileDownloadResponse, FileInfo, FileUploadRequest, FileUploadResponse, PresignedRequest
from app.services import storage_service
from app.services.storage_service import LocalStorage, StorageError, content_disposition, new_object_key

router = APIRouter()

_SIGNATURE_PREFIX_BYTES = 1024
_NOT_FOUND = "Archivo no encontrado o vencido."
_STORAGE_DOWN = "El almacenamiento de archivos no respondió. Probá de nuevo en unos minutos."


def _extension(filename: str) -> str:
    return "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def _absolute(request: Request, url: str) -> str:
    """Las URLs del almacenamiento local son rutas del backend: se completan con su origen."""
    return f"{str(request.base_url).rstrip('/')}{url}" if url.startswith("/") else url


def _info(record: dict) -> FileInfo:
    return FileInfo(file_id=record["id"], **{k: record[k] for k in FileInfo.model_fields if k != "file_id"})


def _owned_or_404(file_id: str, owner: str) -> dict:
    record = files.get_for_user(file_id, owner)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
    return record


def _discard(record: dict, detail: str) -> HTTPException:
    """Borra el objeto y el registro de una subida que no pasó la verificación."""
    try:
        storage_service.get_storage().delete(record["object_key"])
    except StorageError:
        pass  # queda para la limpieza por vencimiento (#64)
    files.delete(record["id"])
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


@router.post(
    "/upload-url",
    status_code=status.HTTP_201_CREATED,
    response_model=FileUploadResponse,
    summary="Pedir una URL firmada para subir un archivo",
    responses={400: {"description": "Formato o tipo no admitido."}, 413: {"description": "Más de 10 MB."}},
)
def create_upload_url(body: FileUploadRequest, request: Request, owner: str = Depends(current_owner)):
    """
    ## Subida directa de un archivo (Issue #62)

    Valida el pedido (formato `.pdf`, `.docx` o `.pptx`, tipo y tamaño máximo de 10 MB), crea el
    registro del archivo y devuelve una **URL firmada `PUT`** que vence en pocos minutos.

    El navegador sube el archivo **directo al almacenamiento** con esa URL, mandando los
    `headers` indicados (el `Content-Type` va firmado), y después confirma con
    `POST /files/{file_id}/complete`. El archivo nunca pasa por esta API.
    """
    extension = _extension(body.filename)
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formato no admitido. Se aceptan archivos .pdf, .docx (Word) y .pptx (PowerPoint).",
        )
    content_type = body.content_type.strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Content-Type '{body.content_type}' no válido para un archivo {extension}.",
        )
    if body.size_bytes > MAX_UPLOAD_SIZE_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="El archivo supera el límite máximo permitido de 10 MB.",
        )

    storage = storage_service.get_storage()
    key = new_object_key(owner, "uploads", extension)
    ttl = settings.FILE_UPLOAD_URL_TTL_S
    try:
        presigned = storage.presign_put(key, content_type, body.size_bytes, ttl)
    except StorageError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_STORAGE_DOWN)
    record = files.create(
        user_id=owner, object_key=key, filename=body.filename.strip(), content_type=content_type,
        size_bytes=body.size_bytes, ttl_hours=settings.FILE_TTL_HOURS,
    )
    return FileUploadResponse(
        file=_info(record),
        upload=PresignedRequest(url=_absolute(request, presigned.url), method="PUT",
                                headers=presigned.headers, expires_in_seconds=ttl),
    )


@router.post(
    "/{file_id}/complete",
    response_model=FileInfo,
    summary="Confirmar que el archivo se subió",
    responses={
        404: {"description": "No existe, venció o es de otro usuario."},
        409: {"description": "Todavía no llegó al almacenamiento."},
        422: {"description": "No coincide con lo declarado: se descarta y hay que pedir otra URL."},
    },
)
def complete_upload(file_id: str, owner: str = Depends(current_owner)):
    """
    Verifica con `HEAD` que el objeto existe y coincide en **tamaño** y **tipo** con lo declarado,
    y que su firma binaria corresponde a la extensión (`%PDF-` o ZIP de Office). Si no coincide,
    el archivo se borra y hay que pedir una nueva URL. Repetir la confirmación no tiene efecto.
    """
    record = _owned_or_404(file_id, owner)
    if record["status"] == "uploaded":
        return _info(record)

    storage = storage_service.get_storage()
    try:
        stored = storage.head(record["object_key"])
        if stored is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Todavía no se recibió el archivo. Subilo con la URL firmada y volvé a confirmar.",
            )
        stored_type = (stored.content_type or "").split(";")[0].strip().lower()
        if stored.size != record["size_bytes"] or stored_type != record["content_type"]:
            raise _discard(record, "El archivo subido no coincide con el declarado (tamaño o tipo). Pedí una nueva URL de subida.")
        prefix = storage.read_prefix(record["object_key"], _SIGNATURE_PREFIX_BYTES)
    except StorageError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_STORAGE_DOWN)
    try:
        check_signature(prefix, _extension(record["filename"]))
    except HTTPException as e:
        raise _discard(record, e.detail)

    files.mark_uploaded(record["id"])
    return _info(files.get_for_user(record["id"], owner))


@router.get("/{file_id}", response_model=FileInfo, summary="Consultar un archivo",
            responses={404: {"description": "No existe, venció o es de otro usuario."}})
def get_file(file_id: str, owner: str = Depends(current_owner)):
    return _info(_owned_or_404(file_id, owner))


@router.get(
    "/{file_id}/download-url",
    response_model=FileDownloadResponse,
    summary="Pedir una URL firmada de descarga",
    responses={404: {"description": "No existe, venció, es de otro usuario o no terminó de subirse."}},
)
def create_download_url(file_id: str, request: Request, owner: str = Depends(current_owner)):
    """URL firmada `GET` de pocos minutos, que descarga el archivo con su nombre original."""
    record = _owned_or_404(file_id, owner)
    if record["status"] != "uploaded":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El archivo todavía no terminó de subirse.")
    ttl = settings.FILE_DOWNLOAD_URL_TTL_S
    try:
        url = storage_service.get_storage().presign_get(record["object_key"], record["filename"], record["content_type"], ttl)
    except StorageError:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=_STORAGE_DOWN)
    return FileDownloadResponse(
        file=_info(record),
        download=PresignedRequest(url=_absolute(request, url), method="GET", headers={}, expires_in_seconds=ttl),
    )


# ---------------------------------------------------------------------------
# Almacenamiento local (solo STORAGE_BACKEND=local): hace de "R2" en desarrollo
# ---------------------------------------------------------------------------

def _local_storage_or_404() -> LocalStorage:
    storage = storage_service.get_storage()
    if not isinstance(storage, LocalStorage):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return storage


def _forbidden() -> HTTPException:
    return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Firma inválida o vencida.")


@router.put("/local/{key:path}", include_in_schema=False)
async def local_put(key: str, request: Request, exp: int = Query(...), sig: str = Query(...)):
    storage = _local_storage_or_404()
    content_type = request.headers.get("content-type", "")
    if not storage.verify("PUT", key, exp, content_type, sig):
        raise _forbidden()
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="Más de 10 MB.")
    try:
        await asyncio.to_thread(storage.put, key, bytes(body), content_type)
    except StorageError:
        raise _forbidden()
    return Response(status_code=status.HTTP_200_OK)


@router.get("/local/{key:path}", include_in_schema=False)
def local_get(key: str, exp: int = Query(...), sig: str = Query(...), name: str = Query(...), type: str = Query(...)):
    storage = _local_storage_or_404()
    if not storage.verify("GET", key, exp, type, sig, name):
        raise _forbidden()
    try:
        path = storage.path_for(key)
    except StorageError:
        raise _forbidden()
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not Found")
    return FileResponse(path, media_type=type, headers={
        "Content-Disposition": content_disposition(name), "Cache-Control": "no-store",
    })
