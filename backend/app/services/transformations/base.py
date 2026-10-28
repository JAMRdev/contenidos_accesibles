"""
Tipos comunes de los formatos de "Transformar material" (Issue #116).

Cada formato (un módulo de este paquete) define un FormatSpec: su esquema, su mock y, cuando su
issue lo implementa, el generador con IA, el renderer del archivo, las vistas derivadas del
resultado (p. ej. Markdown o Mermaid) y las exportaciones. transformation_service los registra.
"""
from dataclasses import dataclass, field
from typing import Awaitable, Callable

from pydantic import BaseModel

from app.schemas.adaptation import AdaptationMetadata
from app.schemas.analysis import AltTextProposal
from app.schemas.document import ExtractedImage
from app.services.llm_service import StructuredResult

# Caracteres del material que se mandan a la IA: un archivo de 10 MB puede tener mucho más texto
# del que entra en la ventana de un modelo de la capa gratuita
MAX_SOURCE_CHARS = 30_000


@dataclass(frozen=True)
class SourceMaterial:
    """Material que se transforma. Solo vive en memoria mientras se genera."""

    text: str
    # Imágenes relevantes con sus bytes: solo si el material es un archivo (el diagnóstico no las guarda)
    images: list[ExtractedImage] = field(default_factory=list)
    # Textos alternativos que propuso el diagnóstico, cuando el material es un diagnóstico
    alt_texts: list[AltTextProposal] = field(default_factory=list)


@dataclass(frozen=True)
class GeneratedFile:
    data: bytes
    content_type: str
    extension: str  # con el punto: ".png", ".mp3"


@dataclass(frozen=True)
class Generation:
    """Lo que devuelve el generador con IA de un formato."""

    result: BaseModel
    metadata: AdaptationMetadata
    warnings: list[str] = field(default_factory=list)


# Exportación del resultado aceptado: (bytes, content type)
Exporter = Callable[[BaseModel], tuple[bytes, str]]


@dataclass(frozen=True)
class FormatSpec:
    schema: type[BaseModel]
    filename: str
    # Resultado fijo a partir del material, sin IA (?mock=true)
    mock: Callable[[SourceMaterial], BaseModel]
    # Generador con IA real; None hasta que el issue del formato lo implementa (responde 501)
    generate: Callable[[SourceMaterial], Awaitable[Generation]] | None = None
    # Archivo a partir del resultado (corre en un hilo). None = el formato no genera archivo
    render: Callable[[BaseModel], GeneratedFile | None] | None = None
    mock_render: Callable[[BaseModel], GeneratedFile | None] | None = None
    # Otras representaciones del resultado que arma el backend (nombre → texto)
    views: Callable[[BaseModel], dict[str, str]] | None = None
    # Formatos de exportación del resultado aceptado (extensión → exportador)
    exports: dict[str, Exporter] = field(default_factory=dict)


def clip(text: str, limit: int) -> str:
    """Una línea, con '…' si supera el límite."""
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def source_for_ai(text: str) -> tuple[str, list[str]]:
    """El material recortado a MAX_SOURCE_CHARS, con un aviso si se recortó."""
    if len(text) <= MAX_SOURCE_CHARS:
        return text, []
    return text[:MAX_SOURCE_CHARS], [
        f"El material es muy extenso: se transformaron sus primeros {MAX_SOURCE_CHARS:,} caracteres.".replace(",", ".")
    ]


def metadata_from(result: StructuredResult) -> AdaptationMetadata:
    usage = result.usage
    return AdaptationMetadata(
        provider=result.provider,
        model=result.model,
        connection=result.connection_label,
        latency_ms=result.latency_ms,
        prompt_tokens=usage.prompt_tokens if usage else None,
        output_tokens=usage.output_tokens if usage else None,
        thinking_tokens=usage.thinking_tokens if usage else None,
        total_tokens=usage.total_tokens if usage else None,
    )
