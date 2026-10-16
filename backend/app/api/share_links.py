"""
Enlaces para compartir archivos (Issue #65): GET /s/{token}.

Público y fuera de /api/v1, para que el enlace sea corto. Valida el token y responde 302 a una
URL firmada GET de pocos minutos: el archivo se descarga directo del almacenamiento (R2) y nunca
pasa por la API. No se cachea (Cache-Control: no-store), así que revocar surte efecto al instante.
"""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.core.config import settings
from app.core.rate_limit import limit_share_access
from app.db import share_repository as shares
from app.services import storage_service
from app.services.storage_service import StorageError

router = APIRouter()

_NO_STORE = {"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}


@router.get(
    "/s/{token}",
    tags=["Files"],
    summary="Abrir un enlace compartido",
    response_class=RedirectResponse,
    status_code=302,
    dependencies=[Depends(limit_share_access)],
    responses={
        302: {"description": "Redirección a la URL firmada de descarga (vence en minutos)."},
        404: {"description": "El enlace no existe, se revocó o venció (o venció el archivo)."},
        429: {"description": "Demasiados accesos desde esta IP."},
    },
)
async def open_shared_link(token: str, request: Request):
    """
    ## Enlace compartido (Issue #65)

    Redirige (`302`) a una URL firmada de descarga de pocos minutos, con el nombre original del
    archivo. Responde `404` si el enlace no existe, se revocó o venció, sin distinguir el motivo.
    Cada apertura se registra (cantidad y fecha) para el docente.
    """
    share = await asyncio.to_thread(shares.resolve, token)
    if share is None:
        raise HTTPException(status_code=404, detail="El enlace no existe, se revocó o venció.", headers=_NO_STORE)
    try:
        url = await asyncio.to_thread(
            storage_service.get_storage().presign_get,
            share["object_key"], share["filename"], share["content_type"], settings.FILE_DOWNLOAD_URL_TTL_S,
        )
    except StorageError:
        raise HTTPException(status_code=502, detail="El almacenamiento de archivos no respondió. Probá de nuevo en unos minutos.")
    await asyncio.to_thread(shares.record_access, share["share_id"])
    if url.startswith("/"):  # almacenamiento local (desarrollo): ruta del propio backend
        url = f"{str(request.base_url).rstrip('/')}{url}"
    return RedirectResponse(url, status_code=302, headers=_NO_STORE)
