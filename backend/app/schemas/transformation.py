"""
Transformar material (Issue #116): nueva versión del contenido como material sin imágenes, mapa
mental, infografía o audio, que el docente revisa antes de guardarla.

La IA solo devuelve texto estructurado (los esquemas *Result, validados con Pydantic); los
archivos (imagen, audio) los genera el backend a partir de ese resultado. Los esquemas de cada
formato son la base común: cada issue de formato (#117, #118, #119, #122) los completa.
"""
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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


class ImageDescription(BaseModel):
    """Una imagen del material y cómo quedó en la versión sin imágenes."""

    image_index: int
    location: str = Field(description="Dónde estaba en el original (ej. 'Página 2, sector superior').")
    role: Literal["informativa", "decorativa"] = Field(
        description="'informativa' aporta contenido y se describe en el texto; 'decorativa' se omite."
    )
    description: str | None = Field(default=None, description="Descripción en texto (solo las informativas).")
    data_table: str | None = Field(
        default=None, description="Datos de un gráfico o tabla como tabla de texto (Markdown), si los tiene."
    )
    color_only: str | None = Field(
        default=None, description="Información que la imagen transmitía solo con color (#80), si la había."
    )
    placement: Literal["en_su_lugar", "al_final"] | None = Field(
        default=None,
        description=(
            "'en_su_lugar': la descripción quedó donde estaba la imagen. 'al_final': no se pudo ubicar y "
            "quedó en la sección final. None para las decorativas."
        ),
    )
    source: Literal["ia", "diagnostico"] = Field(
        description="'ia': descripta ahora con un modelo con visión. 'diagnostico': el texto alternativo del diagnóstico."
    )


class NoImagesResult(BaseModel):
    """Material sin imágenes (Issue #117)."""

    text: str = Field(
        min_length=1,
        description="Material completo, con cada imagen con contenido reemplazada por su descripción en texto.",
    )
    images: list[ImageDescription] = Field(default_factory=list, description="Cada imagen y qué se hizo con ella.")
    omitted_images: list[str] = Field(
        default_factory=list, description="Imágenes decorativas omitidas, descriptas en pocas palabras."
    )
    color_notes: list[str] = Field(
        default_factory=list, description="Avisos de información que el original daba solo con color."
    )


# Lo que devuelve el modelo con visión por cada imagen. Campos de texto vacíos en lugar de nulos:
# algunos proveedores con JSON Schema estricto no admiten campos opcionales.
class NoImagesAIImage(BaseModel):
    image_index: int = Field(description="Número de la imagen, el mismo que se indicó en el pedido.")
    role: Literal["informativa", "decorativa"] = Field(
        description=(
            "'informativa' si aporta contenido necesario para entender el material (gráfico, esquema, mapa, "
            "foto que se analiza, tabla); 'decorativa' si es solo estética (ilustración de adorno, logo, viñeta)."
        )
    )
    description: str = Field(
        description=(
            "Informativa: descripción completa que reemplace a la imagen para quien no la ve (si es un gráfico, "
            "qué muestra y sus datos y tendencias; si es un esquema, sus partes y relaciones). Decorativa: qué es, "
            "en pocas palabras."
        )
    )
    data_table: str = Field(
        description=(
            "Si la imagen es un gráfico o una tabla con datos, esos datos como tabla Markdown "
            "(| encabezado | ... |). Vacío si no tiene datos."
        )
    )
    color_only: str = Field(
        description=(
            "Si la imagen transmite información solo con color (ej. 'en rojo las provincias con sequía'), "
            "cuál es esa información. Vacío si no."
        )
    )
    insert_after: str = Field(
        description=(
            "Cita textual del material, de 5 a 12 palabras copiadas tal cual, del final del párrafo después del "
            "cual estaba la imagen. Vacío si no se puede saber."
        )
    )


class NoImagesAIOutput(BaseModel):
    images: list[NoImagesAIImage] = Field(description="Una entrada por cada imagen recibida.")


# Mapa mental legible (Issue #118): tema central + 3 niveles, pocas ramas por nodo y textos cortos
MIND_MAP_MAX_DEPTH = 4
MIND_MAP_MAX_CHILDREN = 6
MIND_MAP_TEXT_CHARS = 80


class MindMapNode(BaseModel):
    # Sin esto, un texto de solo espacios editado por el docente pasa min_length y queda como ítem vacío
    model_config = ConfigDict(str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=MIND_MAP_TEXT_CHARS, description="Idea en pocas palabras.")
    children: list["MindMapNode"] = Field(default_factory=list, max_length=MIND_MAP_MAX_CHILDREN)

    def depth(self) -> int:
        return 1 + max((c.depth() for c in self.children), default=0)


class MindMapResult(BaseModel):
    """Mapa mental (Issue #118): tema central e ideas principales en jerarquía."""

    root: MindMapNode = Field(
        description=(
            f"Tema central del material, con sus ideas principales como hijos. Hasta {MIND_MAP_MAX_DEPTH} niveles "
            f"contando el tema central, {MIND_MAP_MAX_CHILDREN} hijos por nodo y {MIND_MAP_TEXT_CHARS} caracteres por nodo."
        )
    )

    @model_validator(mode="after")
    def _max_depth(self) -> "MindMapResult":
        if self.root.depth() > MIND_MAP_MAX_DEPTH:
            raise ValueError(f"El mapa mental admite hasta {MIND_MAP_MAX_DEPTH} niveles, contando el tema central.")
        # La misma regla que para lo generado por la IA (mind_map.from_ai)
        if not self.root.children:
            raise ValueError("El mapa mental necesita al menos una idea principal además del tema central.")
        return self


# Lo que devuelve la IA: niveles fijos en lugar de un árbol recursivo, porque los proveedores con
# JSON Schema estricto no admiten esquemas recursivos. Sin límites de largo: se recorta al convertir.
class MindMapAIDetail(BaseModel):
    text: str = Field(description="Detalle de la idea, en pocas palabras (máximo 8).")
    subdetails: list[str] = Field(
        description="Ejemplos o datos concretos del material sobre ese detalle (0 a 3), en pocas palabras."
    )


class MindMapAIIdea(BaseModel):
    text: str = Field(description="Idea principal del material, en pocas palabras (máximo 8).")
    details: list[MindMapAIDetail] = Field(description="Detalles de la idea que están en el material (0 a 4).")


class MindMapAIOutput(BaseModel):
    central_topic: str = Field(description="Tema central del material, en pocas palabras (máximo 8).")
    ideas: list[MindMapAIIdea] = Field(description="Ideas principales del material (2 a 6), en el orden en que aparecen.")


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
    views: dict[str, str] | None = Field(
        default=None,
        description=(
            "Otras representaciones del resultado (el final si ya se decidió) que arma el backend. "
            "Mapa mental: 'markdown' (lista anidada, accesible con lector de pantalla) y 'mermaid' (sintaxis mindmap)."
        ),
    )
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
