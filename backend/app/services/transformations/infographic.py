"""
Infografía (Issue #119): las ideas principales del material en una plantilla accesible.

No se generan imágenes con IA (escriben mal el español, no son gratis y una imagen no es accesible):
la IA devuelve el contenido como JSON (título, secciones, datos destacados, íconos de una lista
cerrada y la plantilla que mejor le queda) y el backend lo dibuja como SVG con texto real
(infographic_templates.py). El SVG se exporta a PNG y PDF con PyMuPDF, sin navegador en Railway.
"""
import pymupdf

from app.schemas.transformation import (
    INFOGRAPHIC_ICONS,
    INFOGRAPHIC_LIMITS,
    InfographicAIOutput,
    InfographicResult,
    InfographicSection,
)
from app.services.llm.base import LLMInvalidResponseError
from app.services.llm_service import llm_service
from app.services.rules_service import split_paragraphs, split_sentences
from app.services.transformations.base import FormatSpec, Generation, SourceMaterial, clip, metadata_from, source_for_ai
from app.services.transformations.infographic_templates import plain_text, render_svg

MAX_SECTIONS = 6

SYSTEM_PROMPT = f"""
Sos un especialista en accesibilidad de materiales educativos y en Diseño Universal para el
Aprendizaje (DUA). Un docente te pide una infografía de su material: las ideas principales, breves
y ordenadas, para que sus estudiantes las vean de un vistazo.

## Reglas obligatorias
- Usá solo información del material. No agregues conceptos, datos ni ejemplos que no aparezcan.
- Frases breves y claras, con la terminología del material y su registro (si usa voseo, mantenelo).
- Texto plano: sin Markdown, sin viñetas ni emojis.

## Respondé con este JSON, con las claves exactamente así (en inglés)
- `title`: título de la infografía (máximo 10 palabras).
- `subtitle`: bajada de una oración que dice de qué trata; "" si no hace falta.
- `layout`: "pasos" si el material describe un proceso, un procedimiento o una secuencia;
  "comparativa" si compara dos o más cosas; si no, "vertical".
- `sections`: de 3 a {MAX_SECTIONS} ideas principales, en el orden del material. Cada una con:
  - `title`: en pocas palabras (máximo 6);
  - `text`: 1 o 2 oraciones (máximo 220 caracteres);
  - `highlight`: un dato destacado del material (un número con su unidad o una frase muy corta);
    "" si la sección no tiene uno;
  - `icon`: el que mejor la acompaña, de esta lista: {", ".join(INFOGRAPHIC_ICONS)}.
- `closing`: la idea para recordar, en una oración; "" si no hace falta.
""".strip()


def _optional(text: str, limit: int) -> str | None:
    text = text.strip()
    return clip(text, limit) if text else None


def from_ai(output: InfographicAIOutput) -> tuple[InfographicResult, list[str]]:
    """Recorta cada campo a su límite y deja hasta MAX_SECTIONS secciones, con un aviso."""
    limits = INFOGRAPHIC_LIMITS
    sections = [
        InfographicSection(
            title=clip(s.title, limits["section_title"]), text=clip(s.text, limits["text"]),
            highlight=_optional(s.highlight, limits["highlight"]), icon=s.icon,
        )
        for s in output.sections if s.title.strip() and s.text.strip()
    ]
    if not output.title.strip() or not sections:
        raise LLMInvalidResponseError("El modelo devolvió una infografía sin título o sin secciones.")
    warnings = []
    if len(sections) > MAX_SECTIONS:
        warnings.append(
            f"La IA propuso {len(sections)} secciones: se dejaron las primeras {MAX_SECTIONS} para que la "
            "infografía siga siendo legible. Revisá si falta alguna idea importante."
        )
        sections = sections[:MAX_SECTIONS]
    result = InfographicResult(
        title=clip(output.title, limits["title"]),
        subtitle=_optional(output.subtitle, limits["subtitle"]),
        layout=output.layout,
        sections=sections,
        closing=_optional(output.closing, limits["closing"]),
    )
    return result, warnings


async def generate(material: SourceMaterial) -> Generation:
    source, warnings = source_for_ai(material.text)
    result = await llm_service.generate_structured(
        system_instruction=SYSTEM_PROMPT,
        contents=f"Material:\n<<<\n{source}\n>>>",
        schema=InfographicAIOutput,
        temperature=0.2,
    )
    infographic, limit_warnings = from_ai(result.parsed)
    return Generation(result=infographic, metadata=metadata_from(result), warnings=[*warnings, *limit_warnings])


def mock(material: SourceMaterial) -> InfographicResult:
    """Título = primer renglón; hasta 4 secciones con la primera oración de los párrafos siguientes."""
    paragraphs = [p.strip() for p in split_paragraphs(material.text) if p.strip()] or [material.text.strip()]
    title = paragraphs[0].splitlines()[0]
    rest = paragraphs[1:] or paragraphs
    icons = ("idea", "libro", "check", "info")
    sections = [
        InfographicSection(title=f"Idea {i}", text=clip((split_sentences(p) or [p])[0], INFOGRAPHIC_LIMITS["text"]),
                           icon=icons[i - 1])
        for i, p in enumerate(rest[:4], start=1)
    ]
    return InfographicResult(title=clip(title, INFOGRAPHIC_LIMITS["title"]), subtitle="[MOCK] Infografía simulada",
                             sections=sections)


# --- Vistas y exportación ----------------------------------------------------------------


def to_markdown(result: InfographicResult) -> str:
    """La infografía como texto, en orden de lectura: la alternativa para lector de pantalla y para copiar."""
    lines = [f"# {result.title}", ""]
    if result.subtitle:
        lines += [result.subtitle, ""]
    for i, s in enumerate(result.sections, start=1):
        lines += [f"## {f'{i}. ' if result.layout == 'pasos' else ''}{s.title}", ""]
        if s.highlight:
            lines += [f"**{s.highlight}**", ""]
        lines += [s.text, ""]
    if result.closing:
        lines += [f"> {result.closing}", ""]
    return "\n".join(lines)


def _svg_document(result: InfographicResult) -> pymupdf.Document:
    return pymupdf.open(stream=render_svg(result).encode("utf-8"), filetype="svg")


def to_pdf(result: InfographicResult) -> bytes:
    """Una página del tamaño de la infografía, con el texto real (se puede seleccionar y leer)."""
    pdf = pymupdf.open("pdf", _svg_document(result).convert_to_pdf())
    pdf.set_metadata({"title": result.title, "subject": plain_text(result), "creator": "InnovaLab"})
    pdf.set_language("es")
    return pdf.tobytes(garbage=3, deflate=True)


def to_png(result: InfographicResult) -> bytes:
    """El doble de resolución que el SVG (2160 px de ancho), para que se vea nítida en pantalla e impresa."""
    return _svg_document(result)[0].get_pixmap(matrix=pymupdf.Matrix(2, 2)).tobytes("png")


SPEC = FormatSpec(
    schema=InfographicResult,
    filename="infografia",
    mock=mock,
    generate=generate,
    views=lambda result: {"svg": render_svg(result), "markdown": to_markdown(result)},
    exports={
        "svg": lambda r: (render_svg(r).encode("utf-8"), "image/svg+xml"),
        "png": lambda r: (to_png(r), "image/png"),
        "pdf": lambda r: (to_pdf(r), "application/pdf"),
        "md": lambda r: (to_markdown(r).encode("utf-8"), "text/markdown; charset=utf-8"),
    },
)
