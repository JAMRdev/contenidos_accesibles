"""
Transformar material (Issue #116): nueva versión del contenido como material sin imágenes, mapa
mental, infografía o audio, que el docente revisa antes de guardarla.

La IA solo devuelve texto estructurado (los esquemas *Result, validados con Pydantic); los
archivos (imagen, audio) los genera el backend a partir de ese resultado. Los esquemas de cada
formato son la base común: cada issue de formato (#117, #118, #119, #122) los completa.
"""
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from app.schemas.adaptation import AdaptationMetadata

TransformationFormat = Literal["no_images", "mind_map", "infographic", "audio"]
# Estado de la generación (asíncrona): el POST responde 'pending' y el resultado llega con 'ready'
JobStatus = Literal["pending", "processing", "ready", "failed"]
# Revisión del docente, con los mismos valores que las propuestas de adaptación
ReviewStatus = Literal["pendiente", "aceptada", "editada", "descartada"]

FORMATS: tuple[str, ...] = ("no_images", "mind_map", "infographic", "audio")

# ---------------------------------------------------------------------------
# Resultado estructurado de cada formato. Las `description` viajan al modelo de IA.
# ---------------------------------------------------------------------------


class NoImagesResult(BaseModel):
    """Material sin imágenes (Issue #117)."""

    text: str = Field(
        min_length=1,
        description="Material completo, con cada imagen con contenido reemplazada por su descripción en texto.",
    )
    omitted_images: list[str] = Field(
        default_factory=list, description="Imágenes decorativas omitidas, descriptas en pocas palabras."
    )


class MindMapNode(BaseModel):
    text: str = Field(min_length=1, max_length=120, description="Idea en pocas palabras.")
    children: list["MindMapNode"] = Field(default_factory=list, max_length=8)


class MindMapResult(BaseModel):
    """Mapa mental (Issue #118): tema central e ideas principales en jerarquía."""

    root: MindMapNode = Field(description="Tema central del material, con sus ideas principales como hijos.")


class InfographicSection(BaseModel):
    title: str = Field(min_length=1, max_length=80)
    text: str = Field(min_length=1, max_length=300)
    highlight: str | None = Field(default=None, max_length=60, description="Dato destacado opcional.")
    icon: str | None = Field(default=None, max_length=40, description="Ícono sugerido de la lista cerrada.")


class InfographicResult(BaseModel):
    """Infografía (Issue #119): contenido que dibuja una plantilla accesible, sin generar imágenes con IA."""

    title: str = Field(min_length=1, max_length=100)
    subtitle: str | None = Field(default=None, max_length=160)
    sections: list[InfographicSection] = Field(min_length=1, max_length=6)
    closing: str | None = Field(default=None, max_length=200)


class AudioResult(BaseModel):
    """Audio (Issue #122): guion revisable que se convierte en un archivo de audio con TTS."""

    script: str = Field(min_length=1, description="Guion que se va a leer, en texto plano y en orden de lectura.")


RESULT_SCHEMAS: dict[str, type[BaseModel]] = {
    "no_images": NoImagesResult,
    "mind_map": MindMapResult,
    "infographic": InfographicResult,
    "audio": AudioResult,
}

# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class TransformationRequest(BaseModel):
    format: TransformationFormat = Field(
        description=(
            "'no_images': material sin imágenes, con sus descripciones en texto. "
            "'mind_map': mapa mental con el tema central y las ideas principales. "
            "'infographic': infografía con plantillas accesibles. "
            "'audio': archivo de audio con guion revisable."
        )
    )
    analysis_id: str | None = Field(
        default=None,
        description="Diagnóstico cuyo material se transforma (con los cambios aceptados, si los hay).",
    )
    file_id: str | None = Field(
        default=None, description="Archivo subido con /files (pdf, docx o pptx) que se transforma sin diagnosticarlo."
    )

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "TransformationRequest":
        if bool(self.analysis_id) == bool(self.file_id):
            raise ValueError("Indicá el material con 'analysis_id' o con 'file_id' (uno de los dos).")
        return self


class TransformationSource(BaseModel):
    type: Literal["analysis", "file"]
    id: str


class TransformationError(BaseModel):
    code: str = Field(description="Ej. 'ai_unavailable', 'invalid_ai_response', 'interrupted', 'internal_error'.")
    message: str


class GeneratedFileInfo(BaseModel):
    """Archivo que genera el backend a partir del resultado (imagen de la infografía, audio)."""

    filename: str
    content_type: str
    size_bytes: int
    preview_url: str | None = Field(
        default=None,
        description=(
            "URL firmada de pocos minutos para revisar el archivo antes de decidir. Se renueva en cada "
            "consulta; no hay después de descartar."
        ),
    )
    expires_in_seconds: int | None = None
    file_id: str | None = Field(
        default=None,
        description=(
            "Solo después de aceptar o editar: el archivo queda en /files para descargarlo "
            "(download-url) y compartirlo (shares)."
        ),
    )


class TransformationResponse(BaseModel):
    transformation_id: str
    format: TransformationFormat
    source: TransformationSource
    status: JobStatus = Field(description="Estado de la generación: consultá con GET hasta 'ready' o 'failed'.")
    review_status: ReviewStatus = Field(
        default="pendiente", description="Decisión del docente. Nada se guarda ni se comparte sin aceptar."
    )
    result: dict[str, Any] | None = Field(
        default=None, description="Resultado estructurado propuesto (esquema del formato). Solo con 'ready'."
    )
    edited_result: dict[str, Any] | None = Field(default=None, description="Versión del docente (solo 'editada').")
    final_result: dict[str, Any] | None = Field(
        default=None, description="Lo que se guarda según la decisión: el propuesto o el editado. None si no se aceptó."
    )
    file: GeneratedFileInfo | None = None
    error: TransformationError | None = Field(default=None, description="Solo con 'failed'.")
    warnings: list[str] = Field(default_factory=list)
    created_at: str
    updated_at: str
    decided_at: str | None = None
    expires_at: str = Field(description="Vence con el material de origen, como mucho (UTC, ISO 8601).")
    metadata: AdaptationMetadata | None = Field(default=None, description="Proveedor, modelo, latencia y tokens.")


class TransformationDecisionRequest(BaseModel):
    decision: Literal["aceptada", "editada", "descartada"]
    edited_result: dict[str, Any] | None = Field(
        default=None,
        description="Obligatorio con 'editada': el resultado corregido, con el mismo esquema que `result`.",
    )

    @model_validator(mode="after")
    def _edited_only_when_edited(self) -> "TransformationDecisionRequest":
        if self.decision == "editada" and not self.edited_result:
            raise ValueError("'edited_result' es obligatorio cuando la decisión es 'editada'.")
        if self.decision != "editada" and self.edited_result is not None:
            raise ValueError("'edited_result' solo se envía cuando la decisión es 'editada'.")
        return self
