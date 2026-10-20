"""Configuración de la API editable por un admin (Issue #64): retención de archivos."""
from fastapi import APIRouter, Depends, HTTPException

from app.core.permissions import Caller
from app.core.teacher_auth import require_permission
from app.schemas.settings import RetentionInfo, RetentionUpdate
from app.services import retention_service

router = APIRouter()

_require_retention = require_permission("settings:retention")


@router.get("/retention", response_model=RetentionInfo, summary="Consultar la retención de archivos")
def get_retention(caller: Caller = Depends(_require_retention)):
    """Retención vigente, valor por defecto, máximo permitido y última limpieza automática."""
    return retention_service.retention_info()


@router.put(
    "/retention",
    response_model=RetentionInfo,
    summary="Cambiar la retención de archivos",
    responses={422: {"description": "Fuera de 1 hora al máximo permitido."}},
)
def update_retention(body: RetentionUpdate, caller: Caller = Depends(_require_retention)):
    """
    ## Retención de archivos (Issue #64)

    Cambia cuántas horas se conservan los archivos **nuevos** (subidos y exportados). Los que ya
    existen mantienen su vencimiento. Al vencer, el archivo deja de aparecer en la API y la limpieza
    automática lo borra del almacenamiento. Solo para el rol **admin**.
    """
    try:
        return retention_service.set_file_retention_hours(body.hours, updated_by=caller.user_id)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
