from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.v1.delivery import Delivery, deliver
from app.core.owner import current_owner
from app.core.rate_limit import limit_transformation
from app.schemas.transformation import TransformationDecisionRequest, TransformationRequest, TransformationResponse
from app.services import transformation_service
from app.services.adaptation_service import AdaptationNotFoundError
from app.services.sensitive_data_service import SensitiveDataError
from app.services.storage_service import StorageError
from app.services.transformation_service import (
    EmptySourceError,
    ExportFormatError,
    FormatNotAvailableError,
    InvalidEditError,
    TooManyActiveError,
    TransformationNotFoundError,
    TransformationStateError,
)

router = APIRouter()


def _absolute(request: Request, t: TransformationResponse) -> TransformationResponse:
    """Las URLs del almacenamiento local son rutas del backend: se completan con su origen."""
    if t.file and t.file.preview_url and t.file.preview_url.startswith("/"):
        url = f"{str(request.base_url).rstrip('/')}{t.file.preview_url}"
        return t.model_copy(update={"file": t.file.model_copy(update={"preview_url": url})})
    return t


@router.post(
    "",
    response_model=TransformationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Pedir una transformación del material (infografía, audio, mapa mental o sin imágenes)",
    response_description="La transformación en estado 'pending': consultá su estado con GET /transformations/{id}",
    responses={
        404: {"description": "El diagnóstico o el archivo no existe, venció o es de otro usuario."},
        409: {"description": "El archivo todavía no terminó de subirse."},
        422: {"description": "Pedido inválido, material sin texto o con datos personales (`sensitive_data_detected`)."},
        429: {"description": "Límite de uso (por formato, de IA o de transformaciones en curso)."},
        501: {"description": "El formato todavía no está disponible con IA real (`format_not_available`): usá ?mock=true."},
    },
)
async def create_transformation(
    payload: TransformationRequest,
    request: Request,
    mock: bool = Query(
        default=False, description="Si es True, genera un resultado fijo por formato (0 tokens), ideal para Frontend."
    ),
    dismissed: list[str] = Query(
        default=[],
        description=(
            "Con `file_id`: ids de hallazgos de datos personales que el docente indicó que no lo son "
            "(Issue #66). Repetir el parámetro por cada id."
        ),
    ),
    owner: str = Depends(current_owner),
):
    """
    ## Transformar material (Issue #116)

    Crea una nueva versión del material en otro formato, conservando sus ideas principales:

    | `format` | Resultado |
    |---|---|
    | `no_images` | Material completo con cada imagen descripta en su lugar (#117). Con `file_id`, la IA con visión describe las imágenes; con `analysis_id`, se usan los textos alternativos del diagnóstico, sin IA |
    | `mind_map` | Mapa mental: tema central e ideas principales (#118) |
    | `infographic` | Infografía con plantillas accesibles, sin generar imágenes con IA (#119) |
    | `audio` | Archivo de audio con guion revisable (#122) |

    - **Material**: `analysis_id` (el material del diagnóstico, con los cambios aceptados) o
      `file_id` (un archivo subido con `/files`, sin diagnosticar). Uno de los dos.
    - **Asíncrono**: responde `202` con `status = "pending"` sin esperar a la IA. Consultá
      `GET /transformations/{transformation_id}` hasta `ready` (con `result` y, si el formato genera
      un archivo, `file.preview_url`) o `failed` (con `error`).
    - **Revisión**: nada se guarda solo. Con `POST /transformations/{id}/decision` el docente acepta,
      edita o descarta; solo lo aceptado queda en `/files` para descargarlo y compartirlo.
    - **Límites**: un tope diario por formato (más bajo para el audio), los límites de IA y un
      máximo de transformaciones generándose a la vez por usuario.
    - **Modo Mock (`?mock=true`)**: mismo ciclo y mismos errores, con resultados fijos por formato.
      Los formatos sin generador con IA real todavía responden `501` sin mock.
    """
    try:
        # El límite se cuenta adentro de create, con el material ya validado
        return await transformation_service.create(
            payload, owner=owner, mock=mock, dismissed=dismissed,
            limit=lambda uses_ai: limit_transformation(request, payload.format, uses_ai=uses_ai),
        )
    except FormatNotAvailableError as e:
        raise HTTPException(status_code=501, detail={"code": e.CODE, "message": str(e)})
    except TooManyActiveError as e:
        raise HTTPException(status_code=429, detail=str(e), headers={"Retry-After": "10"})
    except SensitiveDataError as e:
        raise HTTPException(status_code=422, detail=e.to_detail())
    except AdaptationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except EmptySourceError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except ValueError as e:  # el archivo no se pudo extraer (extensión que no coincide, dañado)
        raise HTTPException(status_code=400, detail=str(e))


@router.get("", response_model=list[TransformationResponse], summary="Listar mis transformaciones vigentes")
def list_transformations(request: Request, owner: str = Depends(current_owner)):
    """Las últimas 50 transformaciones vigentes del usuario, de la más reciente a la más antigua."""
    return [_absolute(request, t) for t in transformation_service.list_for_user(owner=owner)]


@router.get(
    "/{transformation_id}",
    response_model=TransformationResponse,
    summary="Consultar el estado y el resultado de una transformación",
    responses={404: {"description": "No existe, venció o es de otro usuario."}},
)
def get_transformation(transformation_id: str, request: Request, owner: str = Depends(current_owner)):
    """Con `ready`, trae `result` y, si hay archivo, una URL firmada nueva de vista previa (`file.preview_url`)."""
    try:
        return _absolute(request, transformation_service.get(transformation_id, owner=owner))
    except TransformationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/{transformation_id}/decision",
    response_model=TransformationResponse,
    summary="Aceptar, editar o descartar una transformación",
    responses={
        404: {"description": "No existe, venció o es de otro usuario."},
        409: {"description": "Todavía no está lista, falló o ya se decidió."},
        422: {"description": "`edited_result` no respeta el esquema del formato (`invalid_edited_result`)."},
        429: {"description": "Audio: ya tenés el máximo de transformaciones generándose a la vez."},
        502: {"description": "El almacenamiento no respondió."},
    },
)
async def decide_transformation(
    transformation_id: str, payload: TransformationDecisionRequest, request: Request, owner: str = Depends(current_owner),
):
    """
    ## Revisión docente

    - `aceptada`: `final_result` pasa a ser el resultado propuesto.
    - `editada`: requiere `edited_result`, con el mismo esquema que `result`; si el formato genera
      un archivo, se vuelve a generar con el resultado editado.
    - `descartada`: no se guarda nada y el archivo generado se borra.

    Al aceptar o editar un formato con archivo, `file.file_id` queda en `/files`: se descarga con
    `download-url` y se comparte con `shares`, como cualquier exportación. Una decisión es única.

    **Audio (#122):** lo que se revisa es el guion. Al aceptarlo o editarlo, la transformación pasa a
    `processing` y se genera el audio, con `progress` (fragmentos hechos y total). Consultá con GET
    hasta `ready`, que trae `file` (el MP3 completo) y `extra_files` (un ZIP con un MP3 por sección).
    Si el audio falla queda `failed` con el guion aceptado, y se reintenta con `POST /{id}/file`.
    """
    try:
        return _absolute(request, await transformation_service.decide(transformation_id, payload, owner=owner))
    except TransformationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except TransformationStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except InvalidEditError as e:
        raise HTTPException(status_code=422, detail={"code": "invalid_edited_result", "message": str(e), "errors": e.errors})
    except TooManyActiveError as e:
        raise HTTPException(status_code=429, detail=str(e), headers={"Retry-After": "10"})
    except StorageError:
        raise HTTPException(status_code=502, detail="El almacenamiento de archivos no respondió. Probá de nuevo en unos minutos.")


@router.post(
    "/{transformation_id}/file",
    response_model=TransformationResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Reintentar el audio de una transformación aceptada",
    responses={
        404: {"description": "No existe, venció o es de otro usuario."},
        409: {"description": "El formato no genera un archivo después de aceptarlo, o el archivo no falló."},
        429: {"description": "Límite de uso (audio por día, IA o transformaciones generándose a la vez)."},
    },
)
async def retry_transformation_file(transformation_id: str, request: Request, owner: str = Depends(current_owner)):
    """
    Vuelve a generar el audio de una transformación con el guion aceptado o editado cuyo audio falló
    (por ejemplo, sin cuota de TTS o por un reinicio del servidor). Cuenta para el límite diario de audio.
    Responde `202` con `status = "processing"`: consultá con GET hasta `ready` o `failed`.
    """
    try:
        t = await transformation_service.retry_file(
            transformation_id, owner=owner,
            limit=lambda uses_ai: limit_transformation(request, "audio", uses_ai=uses_ai),
        )
    except TransformationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except TransformationStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except TooManyActiveError as e:
        raise HTTPException(status_code=429, detail=str(e), headers={"Retry-After": "10"})
    return _absolute(request, t)


@router.get(
    "/{transformation_id}/export",
    summary="Exportar una transformación aceptada",
    response_model=None,
    responses={
        200: {"description": "Con `delivery=file`, el archivo. Con `delivery=link`, JSON con una URL firmada de descarga."},
        400: {"description": "El formato no se exporta en esa extensión."},
        404: {"description": "No existe, venció o es de otro usuario."},
        409: {"description": "No está aceptada ni editada: solo lo aceptado se exporta."},
    },
)
def export_transformation(
    transformation_id: str,
    request: Request,
    format: str = Query(
        description="Extensión. Mapa mental: 'md' o 'txt' (lista anidada). Material sin imágenes: 'docx', 'pdf', 'md' o 'txt'."
    ),
    delivery: Delivery = Query(
        default="file", description="'file': el archivo en la respuesta. 'link': URL firmada; el archivo queda en /files."
    ),
    owner: str = Depends(current_owner),
):
    """
    Exporta el resultado final (el aceptado o el editado por el docente). Los formatos con archivo
    generado (audio) se descargan desde `/files` con el `file.file_id` que queda al aceptar.
    """
    try:
        data, content_type, filename, expires_at = transformation_service.export(transformation_id, format, owner=owner)
    except TransformationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except TransformationStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except ExportFormatError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return deliver(request, data, content_type, filename, delivery, owner, "transformation", expires_at)
