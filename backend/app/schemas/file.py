from typing import Literal

from pydantic import BaseModel, Field


class FileUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255, description="Nombre original del archivo, con su extensión (.pdf, .docx o .pptx).")
    content_type: str = Field(
        min_length=1, max_length=200,
        description="Tipo MIME que el navegador va a mandar al subir (el de `File.type`). Va firmado en la URL.",
    )
    size_bytes: int = Field(gt=0, description="Tamaño exacto del archivo en bytes (máximo 10 MB).")


class PresignedRequest(BaseModel):
    url: str = Field(description="URL firmada. Vence a los `expires_in_seconds`.")
    method: Literal["PUT", "GET"]
    headers: dict[str, str] = Field(description="Headers que hay que mandar tal cual (van firmados).")
    expires_in_seconds: int


class FileInfo(BaseModel):
    file_id: str
    filename: str
    content_type: str
    size_bytes: int
    kind: Literal["upload", "export"]
    status: Literal["pending", "uploaded"] = Field(description="'pending' hasta confirmar la subida con /complete.")
    created_at: str
    uploaded_at: str | None
    expires_at: str = Field(description="Después de esta fecha el archivo se borra (Issue #64).")


class FileUploadResponse(BaseModel):
    file: FileInfo
    upload: PresignedRequest


class FileDownloadResponse(BaseModel):
    file: FileInfo
    download: PresignedRequest


class ShareCreateRequest(BaseModel):
    expires_in_hours: int | None = Field(
        default=None, ge=1,
        description="Vigencia del enlace en horas. Por defecto, y como máximo, hasta que vence el archivo.",
    )


class ShareInfo(BaseModel):
    share_id: str = Field(description="Id del enlace (para revocarlo). No sirve para abrirlo.")
    created_at: str
    expires_at: str
    access_count: int = Field(description="Veces que se abrió el enlace.")
    last_access_at: str | None


class ShareCreated(ShareInfo):
    url: str = Field(description="Enlace para copiar y compartir. Se muestra solo ahora: no se puede volver a consultar.")
