from typing import Literal

from pydantic import BaseModel, Field, model_validator

AdaptationType = Literal["lenguaje_claro", "consigna_en_pasos", "reorganizar", "mejorar_estructura"]
ProposalStatus = Literal["pendiente", "aceptada", "editada", "descartada"]

MAX_FRAGMENT_CHARS = 6_000

# ---------------------------------------------------------------------------
# Esquema que se envía al modelo de IA como salida estructurada (JSON Schema).
# Las `description` viajan al modelo: están redactadas como instrucciones.
# ---------------------------------------------------------------------------


class AdaptationAIOutput(BaseModel):
    proposed_text: str = Field(
        description=(
            "Versión adaptada del fragmento, lista para reemplazar al original. Solo el texto "
            "adaptado: sin comentarios, sin comillas envolventes ni encabezados del tipo 'Propuesta:'."
        )
    )
    explanation: str = Field(
        description="Qué se cambió y por qué mejora la accesibilidad, en 1 a 3 oraciones dirigidas al docente."
    )
    preserved_objective: str = Field(
        description=(
            "Objetivo pedagógico del fragmento original que la propuesta conserva sin cambios "
            "(qué tiene que aprender o hacer el estudiante), en una oración."
        )
    )


# ---------------------------------------------------------------------------
# Esquemas de la API.
# ---------------------------------------------------------------------------


class AdaptationRequest(BaseModel):
    analysis_id: str = Field(
        description="ID del diagnóstico (devuelto por /analysis/sample o /analysis/pdf) al que pertenece la barrera."
    )
    barrier_id: str = Field(
        pattern=r"^bar-\d+$",
        description="ID de la barrera del diagnóstico que se quiere adaptar (ej. 'bar-2').",
        examples=["bar-1"],
    )
    adaptation_type: AdaptationType = Field(
        description=(
            "'lenguaje_claro': reescribe con vocabulario y sintaxis más simples. "
            "'consigna_en_pasos': divide una consigna en pasos numerados de una acción cada uno. "
            "'reorganizar': reordena un contenido extenso en bloques breves. "
            "'mejorar_estructura': agrega títulos, listas y jerarquía al fragmento."
        )
    )
    fragment: str | None = Field(
        default=None,
        min_length=10,
        max_length=MAX_FRAGMENT_CHARS,
        description=(
            "Opcional. Texto exacto a adaptar si el docente quiere ajustar el fragmento de la barrera. "
            "Obligatorio cuando la barrera no cita un fragmento textual (ej. evaluaciones globales del documento)."
        ),
    )


class AdaptationMetadata(BaseModel):
    provider: str = Field(description="Proveedor de IA que generó la propuesta (ej. groq, gemini, mock).")
    model: str
    connection: str = Field(description="Nombre de la conexión de /admin que se usó.")
    latency_ms: int
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    total_tokens: int | None = None


class AdaptationProposal(BaseModel):
    proposal_id: str = Field(description="Identificador único de la propuesta (UUIDv4).")
    analysis_id: str
    barrier_id: str = Field(description="Barrera del diagnóstico a la que responde la propuesta.")
    adaptation_type: AdaptationType
    status: ProposalStatus = Field(
        default="pendiente",
        description="Las propuestas nunca se aplican solas: nacen 'pendiente' hasta que el docente decide.",
    )
    original_text: str = Field(description="Fragmento original del material, sin modificar.")
    fragment_source: Literal["barrier", "request"] = Field(
        description="'barrier' si el fragmento salió de la barrera del diagnóstico; 'request' si lo envió el docente."
    )
    proposed_text: str = Field(description="Versión adaptada propuesta.")
    explanation: str = Field(description="Qué se cambió y por qué.")
    preserved_objective: str = Field(description="Objetivo pedagógico que la propuesta conserva.")
    edited_text: str | None = Field(
        default=None, description="Texto corregido por el docente (solo con status 'editada')."
    )
    final_text: str | None = Field(
        default=None,
        description=(
            "Texto que reemplazaría al original según la decisión: la propuesta si fue 'aceptada', "
            "el texto del docente si fue 'editada'; None si está 'pendiente' o 'descartada'."
        ),
    )
    decided_at: str | None = Field(default=None, description="Fecha de la decisión vigente (UTC, ISO 8601).")
    warnings: list[str] = Field(default_factory=list)
    created_at: str = Field(description="Fecha de creación (UTC, ISO 8601).")
    expires_at: str = Field(description="Vence junto con el diagnóstico al que pertenece (UTC, ISO 8601).")
    metadata: AdaptationMetadata


# ---------------------------------------------------------------------------
# Revisión docente (Issue #29).
# ---------------------------------------------------------------------------


class DecisionRequest(BaseModel):
    decision: Literal["aceptada", "editada", "descartada"] = Field(
        description="Decisión del docente sobre una propuesta 'pendiente'."
    )
    edited_text: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_FRAGMENT_CHARS,
        description="Obligatorio con 'editada': el texto final corregido por el docente. No se admite con otras decisiones.",
    )

    @model_validator(mode="after")
    def _edited_text_only_when_edited(self) -> "DecisionRequest":
        if self.decision == "editada" and not (self.edited_text and self.edited_text.strip()):
            raise ValueError("'edited_text' es obligatorio cuando la decisión es 'editada'.")
        if self.decision != "editada" and self.edited_text is not None:
            raise ValueError("'edited_text' solo se envía cuando la decisión es 'editada'.")
        return self


class DecisionLogEntry(BaseModel):
    """Una modificación registrada para el informe: decisión o deshacer."""

    proposal_id: str
    barrier_id: str
    adaptation_type: AdaptationType
    action: Literal["decision", "deshacer"]
    from_status: ProposalStatus
    to_status: ProposalStatus
    edited_text: str | None = Field(default=None, description="Texto del docente cuando la decisión fue 'editada'.")
    created_at: str = Field(description="Momento de la modificación (UTC, ISO 8601).")


# ---------------------------------------------------------------------------
# Versión final adaptada (Issue #38).
# ---------------------------------------------------------------------------


class AdaptedDocumentResponse(BaseModel):
    """Versión final del material generada aplicando únicamente las propuestas aceptadas y editadas."""

    analysis_id: str = Field(description="ID del diagnóstico al que pertenece el documento adaptado.")
    original_text: str = Field(description="Texto original completo del material analizado.")
    adapted_text: str = Field(description="Texto final con las adaptaciones aceptadas y editadas aplicadas.")
    total_proposals: int = Field(description="Cantidad total de propuestas registradas para este diagnóstico.")
    applied_count: int = Field(description="Cantidad de propuestas aceptadas o editadas aplicadas con éxito.")
    discarded_count: int = Field(default=0, description="Cantidad de propuestas descartadas (no aplicadas).")
    pending_count: int = Field(default=0, description="Cantidad de propuestas pendientes (no aplicadas).")
    applied_proposals: list[str] = Field(
        default_factory=list,
        description="IDs de las propuestas aplicadas en el texto final.",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Advertencias durante la aplicación (ej. solapamientos resueltos o citas no encontradas).",
    )


# ---------------------------------------------------------------------------
# Informe final (Issue #39).
# ---------------------------------------------------------------------------

BarrierResolutionStatus = Literal["aceptada", "editada", "descartada", "pendiente"]


class BarrierResolutionDetail(BaseModel):
    """Barrera del diagnóstico original junto con la decisión docente que se tomó sobre ella."""

    barrier_id: str = Field(description="Identificador de la barrera en el diagnóstico original (ej. 'bar-1').")
    category: str = Field(description="Categoría de la barrera ('comprension', 'estructura', 'accesibilidad_visual').")
    severity: str = Field(description="Severidad original ('para_revisar', 'barrera_detectada').")
    issue: str = Field(description="Nombre breve del problema detectado.")
    original_text: str = Field(description="Fragmento original del material citado por la barrera.")
    resolution_status: BarrierResolutionStatus = Field(
        description=(
            "'aceptada': la propuesta de la IA se aplicó tal cual. "
            "'editada': el docente aplicó su propia versión. "
            "'descartada': el docente decidió no cambiar este fragmento. "
            "'pendiente': no se tomó ninguna decisión sobre esta barrera."
        )
    )
    final_text: str | None = Field(
        default=None,
        description="Texto aplicado en el documento final ('aceptada' o 'editada'). None si no se aplicó.",
    )
    proposal_id: str | None = Field(
        default=None,
        description="ID de la propuesta de adaptación vinculada a esta barrera, si existe.",
    )


class FinalReportResponse(BaseModel):
    """Informe ejecutivo de cierre del proceso de adaptación del material (Issue #39)."""

    analysis_id: str = Field(description="ID del diagnóstico de referencia.")
    generated_at: str = Field(description="Fecha y hora de generación del informe (UTC, ISO 8601).")

    # Puntajes
    score_before: int = Field(ge=0, le=100, description="Puntaje de accesibilidad del material original.")
    score_after: int = Field(
        ge=0,
        le=100,
        description=(
            "Puntaje estimado del material adaptado, comparable con score_before: conserva el puntaje de la IA "
            "y las reglas del archivo (imágenes, escaneo, títulos) y recalcula las reglas de texto."
        ),
    )
    score_delta: int = Field(description="Diferencial de puntaje (score_after − score_before). Positivo indica mejora.")
    score_breakdown_before: "ScoreBreakdown | None" = Field(
        default=None,
        description="Detalle del cálculo de puntaje del material original.",
    )
    score_breakdown_after: "ScoreBreakdown | None" = Field(
        default=None,
        description="Detalle del cálculo de puntaje del material adaptado (IA original + reglas recalculadas).",
    )
    disclaimer: str = Field(
        default=(
            "El puntaje obtenido es de carácter orientativo para la mejora de la "
            "accesibilidad pedagógica y no constituye una certificación oficial de accesibilidad (WCAG)."
        ),
        description="Aclaración sobre el carácter orientativo del puntaje.",
    )

    # Resumen ejecutivo en lenguaje natural
    executive_summary: list[str] = Field(
        description="Frases legibles que describen los cambios realizados (ej. 'Se simplificaron 2 consignas...')."
    )

    # Métricas consolidadas
    total_barriers: int = Field(description="Cantidad total de barreras detectadas en el diagnóstico original.")
    resolved_count: int = Field(description="Barreras resueltas (aceptadas + editadas).")
    discarded_count: int = Field(description="Barreras descartadas por el docente.")
    pending_count: int = Field(description="Barreras sin decisión tomada.")
    total_proposals: int = Field(description="Total de propuestas de adaptación generadas.")

    # Detalle por barrera
    barriers_detail: list[BarrierResolutionDetail] = Field(
        description="Estado de cada barrera del diagnóstico original con la decisión correspondiente."
    )

    warnings: list[str] = Field(
        default_factory=list,
        description="Advertencias del proceso de generación del informe.",
    )


# Referencia diferida para ScoreBreakdown (importado en el servicio)
from app.schemas.analysis import ScoreBreakdown  # noqa: E402
FinalReportResponse.model_rebuild()
