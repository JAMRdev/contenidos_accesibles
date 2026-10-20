from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status

from app.core.owner import current_owner
from app.core.rate_limit import limit_ai_usage
from app.db import adaptation_repository
from app.schemas.adaptation import (
    AdaptationProposal,
    AdaptationRequest,
    AdaptedDocumentResponse,
    DecisionLogEntry,
    DecisionRequest,
    FinalReportResponse,
)
from app.services import adaptation_service
from app.services.adaptation_service import (
    AdaptationNotFoundError,
    FragmentRequiredError,
    PendingProposalError,
    ProposalStateError,
)
from app.services.key_rotation_service import AllKeysExhaustedError
from app.services.llm.base import LLMInvalidResponseError, LLMUnavailableError
from app.services.sensitive_data_service import SensitiveDataError

router = APIRouter()


@router.post(
    "",
    response_model=AdaptationProposal,
    status_code=status.HTTP_201_CREATED,
    summary="Proponer una adaptación con IA para una barrera del diagnóstico",
    response_description="Original + propuesta + explicación del cambio, en estado 'pendiente'",
    dependencies=[Depends(limit_ai_usage)],
)
async def create_adaptation(
    payload: AdaptationRequest,
    background_tasks: BackgroundTasks,
    mock: bool = Query(
        default=False,
        description="Si es True, genera una propuesta simulada determinística (0 tokens), ideal para Frontend.",
    ),
    dismissed: list[str] = Query(
        default=[],
        description=(
            "Ids de hallazgos de datos personales en `fragment` que el docente indicó que no lo son "
            "(Issue #66). Repetir el parámetro por cada id."
        ),
    ),
    owner: str = Depends(current_owner),
):
    """
    ## Adaptaciones con IA (Issue #27)

    Después del diagnóstico, el docente elige una barrera (`barrier_id` del diagnóstico
    `analysis_id`) y un tipo de adaptación:

    | `adaptation_type` | Qué hace |
    |---|---|
    | `lenguaje_claro` | Oraciones breves, voz activa y vocabulario frecuente |
    | `consigna_en_pasos` | Pasos numerados con una acción cada uno |
    | `reorganizar` | Contenido extenso en bloques breves de una idea |
    | `mejorar_estructura` | Títulos, listas y jerarquía |

    - **Fragmento**: por defecto se adapta el texto que cita la barrera. Si la regla lo citó
      recortado (`…`), se recupera del material el párrafo completo (o la oración, para
      `consigna_en_pasos`). Con `fragment` el docente puede enviar el texto exacto; es
      obligatorio para barreras globales que no citan texto (responde `422` si falta).
    - **Objetivo pedagógico**: el prompt prohíbe cambiar la tarea, el contenido o la exigencia;
      la IA informa en `preserved_objective` qué objetivo conserva.
    - **Nunca se aplica sola**: la propuesta se guarda en estado `pendiente` y vence junto con
      su diagnóstico (`expires_at`).
    - **Modo Mock (`?mock=true`)**: misma respuesta y mismos errores, sin consumir IA.
    - **Una propuesta pendiente por barrera**: si la barrera ya tiene una (o se está generando),
      responde `409` sin llamar a la IA, con `detail.code = "pending_proposal_exists"` y el
      `detail.proposal_id` de la existente. Después de decidirla se puede pedir otra.
    - **Datos personales (Issue #66)**: si `fragment` contiene datos personales, no se envía a la IA
      y se responde `422` con `detail.code = "sensitive_data_detected"`. Para seguir igual,
      reenviá el pedido con los `id` descartados en `dismissed`.
    """
    try:
        proposal = await adaptation_service.create_proposal(payload, owner=owner, mock=mock, dismissed_findings=dismissed)
        background_tasks.add_task(adaptation_repository.delete_expired)
        return proposal
    except SensitiveDataError as e:
        raise HTTPException(status_code=422, detail=e.to_detail())
    except AdaptationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except FragmentRequiredError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except PendingProposalError as e:
        raise HTTPException(status_code=409, detail=e.to_detail())
    except AllKeysExhaustedError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except LLMInvalidResponseError as e:
        raise HTTPException(status_code=502, detail=str(e))
    except LLMUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al comunicar con el proveedor de IA: {str(e)}",
        )


@router.get(
    "",
    response_model=list[AdaptationProposal],
    summary="Listar las propuestas de adaptación de un diagnóstico",
)
def list_adaptations(
    analysis_id: str = Query(description="ID del diagnóstico cuyas propuestas se quieren listar."),
    owner: str = Depends(current_owner),
):
    """Propuestas generadas para un diagnóstico, de la más antigua a la más reciente."""
    try:
        return adaptation_service.list_proposals(analysis_id, owner=owner)
    except AdaptationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/decisions",
    response_model=list[DecisionLogEntry],
    summary="Registro de decisiones docentes de un diagnóstico (para el informe)",
)
def list_decisions(
    analysis_id: str = Query(description="ID del diagnóstico cuyo registro de modificaciones se quiere obtener."),
    owner: str = Depends(current_owner),
):
    """
    Historial cronológico de decisiones y deshaceres sobre las propuestas del diagnóstico.
    Cada entrada indica el estado anterior y el nuevo, y el texto del docente cuando editó.
    """
    try:
        return adaptation_service.list_decisions(analysis_id, owner=owner)
    except AdaptationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/final-version",
    response_model=AdaptedDocumentResponse,
    summary="Generar la versión final del documento aplicando solo los cambios aceptados (Issue #38)",
)
def get_final_version(
    analysis_id: str = Query(description="ID del diagnóstico cuyo documento adaptado se quiere generar."),
    owner: str = Depends(current_owner),
):
    """
    ## Versión final adaptada (Issue #38)

    Construye el texto adaptado del material analizado:
    - Aplica únicamente las propuestas en estado `aceptada` (con `proposed_text`)
      o `editada` (respetando textualmente `edited_text`).
    - Las propuestas `descartada` y `pendiente` no se aplican: su fragmento
      original se conserva intacto.
    - Si dos propuestas aceptadas/editadas se solapan en el mismo fragmento, se resuelve
      priorizando la edición del docente (`editada` > `aceptada`) o la decisión más
      reciente, y se documenta en `warnings`.
    """
    try:
        return adaptation_service.build_adapted_document(analysis_id, owner=owner)
    except AdaptationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/report",
    response_model=FinalReportResponse,
    summary="Informe final: barreras detectadas, cambios aplicados y puntaje antes/después (Issue #39)",
)
def get_final_report(
    analysis_id: str = Query(description="ID del diagnóstico para el cual se genera el informe de cierre."),
    owner: str = Depends(current_owner),
):
    """
    ## Informe final de adaptación (Issue #39)

    Genera un informe ejecutivo del proceso de mejora de accesibilidad:

    - **score_before / score_after**: puntaje del material original y del adaptado, comparables.
      El posterior no vuelve a consultar la IA (0 tokens): conserva su puntaje y las reglas del
      archivo (imágenes, escaneo, títulos) y recalcula las reglas de texto. Sin cambios aplicados,
      ambos son iguales.
    - **score_delta**: diferencial. Positivo indica mejora de accesibilidad.
    - **barriers_detail**: cada barrera del diagnóstico original con su decisión docente
      (`aceptada`, `editada`, `descartada` o `pendiente`).
    - **executive_summary**: frases legibles que describen los cambios realizados.
    - **disclaimer**: aclaración de que el puntaje es orientativo y no constituye
      certificación oficial de accesibilidad (WCAG).
    """
    try:
        return adaptation_service.build_final_report(analysis_id, owner=owner)
    except AdaptationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get(
    "/{proposal_id}",
    response_model=AdaptationProposal,
    summary="Recuperar una propuesta de adaptación por ID",
)
def get_adaptation(proposal_id: str, owner: str = Depends(current_owner)):
    try:
        return adaptation_service.get_proposal(proposal_id, owner=owner)
    except AdaptationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post(
    "/{proposal_id}/decision",
    response_model=AdaptationProposal,
    summary="Aceptar, editar o descartar una propuesta",
)
def decide_adaptation(proposal_id: str, payload: DecisionRequest, owner: str = Depends(current_owner)):
    """
    ## Revisión docente (Issue #29)

    El docente decide cada propuesta `pendiente` por separado; ninguna se aplica sola.

    - `aceptada`: `final_text` pasa a ser la propuesta de la IA.
    - `editada`: requiere `edited_text`, que pasa a ser `final_text`.
    - `descartada`: `final_text` queda en `null`.

    `original_text` nunca se modifica. Una propuesta ya decidida responde `409`:
    para cambiar de opinión, primero hay que deshacer la decisión.
    """
    try:
        return adaptation_service.decide(proposal_id, payload, owner=owner)
    except AdaptationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ProposalStateError as e:
        raise HTTPException(status_code=409, detail=str(e))


@router.post(
    "/{proposal_id}/undo",
    response_model=AdaptationProposal,
    summary="Deshacer la decisión sobre una propuesta",
)
def undo_adaptation(proposal_id: str, owner: str = Depends(current_owner)):
    """
    Revierte la decisión vigente: la propuesta vuelve a `pendiente`. Responde `409` si ya estaba
    pendiente, o si la barrera tiene otra propuesta pendiente (`detail.code = "pending_proposal_exists"`).
    """
    try:
        return adaptation_service.undo(proposal_id, owner=owner)
    except AdaptationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except PendingProposalError as e:
        raise HTTPException(status_code=409, detail=e.to_detail())
    except ProposalStateError as e:
        raise HTTPException(status_code=409, detail=str(e))
