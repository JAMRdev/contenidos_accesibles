"""
Lectura y validación de archivos subidos en PDF, Word (.docx) o PowerPoint (.pptx) (Issue #47).

Valida extensión, Content-Type, tamaño y firma binaria antes de procesar. Que el contenido
corresponda de verdad a la extensión lo termina de verificar DocumentService.process_file.
"""
import asyncio

from fastapi import HTTPException, UploadFile, status

MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024  # 1 MB por lectura

SUPPORTED_EXTENSIONS = (".pdf", ".docx", ".pptx")

ALLOWED_CONTENT_TYPES = {
    "application/pdf",
    "application/x-pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    # Algunos clientes HTTP genéricos o navegadores envían tipos genéricos para Office.
    "application/octet-stream",
    "application/zip",
    "application/x-zip-compressed",
}

_PDF_MAGIC = b"%PDF-"
# ZIP (formato de .docx/.pptx) u OLE (.doc/.ppt antiguos o Office protegido con contraseña:
# se deja pasar para que process_file explique el motivo).
_OFFICE_MAGICS = (b"PK\x03\x04", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1")


async def read_supported_upload(file: UploadFile) -> tuple[bytes, str]:
    """Devuelve (contenido, nombre) o lanza HTTPException 400/413 con el motivo para el docente."""
    filename = file.filename or ""
    extension = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension not in SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Formato no admitido. Se aceptan archivos .pdf, .docx (Word) y .pptx (PowerPoint).",
        )

    if file.content_type and file.content_type.lower() not in ALLOWED_CONTENT_TYPES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Content-Type '{file.content_type}' no válido para un archivo {extension}.",
        )

    content = bytearray()
    while chunk := await file.read(CHUNK_SIZE):
        if not content:
            check_signature(chunk, extension)
        content.extend(chunk)
        if len(content) > MAX_UPLOAD_SIZE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="El archivo supera el límite máximo permitido de 10 MB.",
            )

    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El archivo recibido está vacío (0 bytes).",
        )
    return bytes(content), filename


def check_signature(first_chunk: bytes, extension: str) -> None:
    if extension == ".pdf":
        if not first_chunk.lstrip().startswith(_PDF_MAGIC):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="El contenido del archivo no corresponde a un PDF válido (firma '%PDF-' ausente).",
            )
    elif not first_chunk.startswith(_OFFICE_MAGICS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="El contenido del archivo no corresponde a un documento de Word o PowerPoint válido.",
        )


_SIGNATURE_PREFIX_BYTES = 1024


async def read_stored_file(file_id: str, owner: str) -> tuple[bytes, str]:
    """
    Archivo subido con la subida directa (Issue #62), leído por su file_id en memoria y del lado
    del servidor (Issue #63): nunca pasa por el cliente. Mismas validaciones que la subida
    multipart (10 MB y firma binaria). Devuelve (contenido, nombre original).
    """
    from app.db import file_repository as files
    from app.services import storage_service
    from app.services.storage_service import StorageError

    record = await asyncio.to_thread(files.get_for_user, file_id, owner)
    if record is None or record["kind"] != "upload":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Archivo no encontrado o vencido.")
    if record["status"] != "uploaded":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El archivo todavía no terminó de subirse: confirmalo con POST /files/{file_id}/complete.",
        )
    too_large = HTTPException(
        status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail="El archivo supera el límite máximo permitido de 10 MB.",
    )
    if record["size_bytes"] > MAX_UPLOAD_SIZE_BYTES:
        raise too_large
    try:
        content = await asyncio.to_thread(storage_service.get_storage().read, record["object_key"], MAX_UPLOAD_SIZE_BYTES)
    except FileNotFoundError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="El archivo ya no está disponible en el almacenamiento.")
    except StorageError:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="El almacenamiento de archivos no respondió. Probá de nuevo en unos minutos.",
        )
    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise too_large
    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="El archivo está vacío (0 bytes).")
    filename = record["filename"]
    check_signature(content[:_SIGNATURE_PREFIX_BYTES], "." + filename.rsplit(".", 1)[-1].lower())
    return content, filename
