"""
Endpoint de política de datos y privacidad (Issue #41).

Expone la política activa del backend para que el Frontend pueda renderizar el aviso
de procesamiento con IA externa y las garantías de almacenamiento/privacidad.
"""

from fastapi import APIRouter

from app.core.config import settings
from app.schemas.privacy import (
    ExternalAIProcessingPolicy,
    PrivacyPolicyResponse,
    StoragePolicy,
    UsageStatisticsPolicy,
)
from app.services import retention_service
from app.services.document_service import MAX_ANALYSIS_WORDS

router = APIRouter()


@router.get(
    "/policy",
    response_model=PrivacyPolicyResponse,
    summary="Consultar política de privacidad y tratamiento de datos (Issue #41)",
    description=(
        "Devuelve las condiciones de privacidad activas en el backend: proveedor y modelo de IA en uso, "
        "qué datos se envían a la IA externa, filtros de datos personales (PII), garantía de no persistencia "
        "en disco local, política de retención en almacenamiento temporal (Cloudflare R2 o local) e historial "
        "de uso por usuario (permanente y sin contenido del material, Issue #104)."
    ),
)
def get_privacy_policy() -> PrivacyPolicyResponse:
    storage_provider = (
        "Cloudflare R2 (bucket privado cifrado en reposo)"
        if settings.STORAGE_BACKEND == "r2"
        else "Almacenamiento local (desarrollo)"
    )

    # Retención vigente de los archivos nuevos (Issue #64): la del panel o FILE_RETENTION_HOURS
    retention_hours = retention_service.file_retention_hours()
    return PrivacyPolicyResponse(
        external_ai_processing=ExternalAIProcessingPolicy(
            enabled=True,
            provider=settings.AI_PROVIDER,
            model=settings.AI_MODEL,
            max_words_per_analysis=MAX_ANALYSIS_WORDS,
            data_sent_description=(
                f"Únicamente el texto extraído del documento educativo (hasta {MAX_ANALYSIS_WORDS} palabras). "
                "Los archivos binarios originales nunca se envían al proveedor de IA."
            ),
            pii_protection=(
                "Detección preventiva local con expresiones regulares: se buscan DNI, CUIL/CUIT, nombres rotulados "
                "('alumno/estudiante: ...') y calificaciones antes de llamar a la IA. Si se detectan datos personales, "
                "el material se rechaza inmediatamente sin enviarse a la IA ni guardarse."
            ),
        ),
        storage_policy=StoragePolicy(
            local_disk_storage="Cero persistencia en disco local. Todos los documentos se procesan en memoria volátil (RAM).",
            storage_backend=settings.STORAGE_BACKEND,
            storage_provider_name=storage_provider,
            retention_period_hours=retention_hours,
            auto_deletion_enabled=True,
            object_folders={
                "uploads": "Archivos originales subidos por el docente",
                "results": "Diagnósticos e informes de accesibilidad",
                "adapted": "Versiones adaptadas generadas",
            },
        ),
        # Historial de uso por usuario (Issue #104): permanente, sin contenido
        usage_statistics=UsageStatisticsPolicy(
            enabled=True,
            retention="Indefinida: el historial de uso no vence ni se borra con los materiales.",
            deleted_with_materials=False,
            recorded_data=[
                "Usuario (id de la cuenta), fecha y tipo de cada acción: diagnóstico, propuesta, decisión, "
                "subida, exportación, descarga, enlace compartido, llamada a la IA y límite de uso alcanzado",
                "Formato del material o del archivo (texto, PDF, Word, PowerPoint, Markdown) y su tamaño",
                "Puntaje y cantidad de barreras del diagnóstico, tipo de adaptación y decisión del docente",
                "Proveedor y modelo de IA, latencia y tokens consumidos",
                "Nombre y email de la cuenta, primer y último uso, y rol",
            ],
            never_recorded=[
                "El texto o el contenido del material",
                "Las propuestas de adaptación, los textos editados ni las respuestas de la IA",
                "Los nombres de los archivos",
            ],
            visible_to="Solo el equipo con acceso al panel de administración.",
        ),
        user_rights=(
            "El docente mantiene el control pedagógico total de su material. Los datos se eliminan "
            f"automáticamente al transcurrir el período de retención de {retention_hours} horas. "
            "El historial de uso (sin contenido del material) se conserva para las estadísticas del servicio."
        ),
    )
