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
