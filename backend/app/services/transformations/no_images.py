"""
Material sin imágenes (Issue #117): el contenido completo sin depender de imágenes, para
estudiantes ciegos o con baja visión que usan lector de pantalla, o para imprimir.

El texto del material no lo reescribe la IA: el backend inserta la descripción de cada imagen
con contenido en el lugar donde estaba, después del párrafo que cita la IA (o, si no lo encuentra,
el que rodeaba a la imagen en el original). Las decorativas se omiten y se informan.

- Archivo (file_id): una llamada con visión describe las imágenes (descripción completa, datos
  de gráficos y tablas, información solo por color).
- Diagnóstico (analysis_id): el diagnóstico no guarda las imágenes, así que se usan los textos
  alternativos que propuso, sin llamar a la IA.
"""
import re

from app.schemas.adaptation import AdaptationMetadata
from app.schemas.transformation import ImageDescription, NoImagesAIOutput, NoImagesResult
from app.services.export_service import export_document
from app.services.llm_service import llm_service
from app.services.text_matching import fragment_parts, normalize
from app.services.transformations.base import FormatSpec, Generation, SourceMaterial, metadata_from, source_for_ai

SYSTEM_PROMPT = """
Sos un especialista en accesibilidad de materiales educativos (WCAG 1.1.1, contenido no textual) y
en Diseño Universal para el Aprendizaje. Un docente quiere una versión de su material sin imágenes,
para estudiantes ciegos o con baja visión que usan lector de pantalla, o para imprimirla: el
contenido tiene que quedar completo sin ver ninguna imagen.

Recibís el texto del material y sus imágenes, cada una con su número, su ubicación y el texto que la
rodeaba. Para cada imagen:
- `role`: 'informativa' si aporta contenido para entender el material o resolver la actividad;
  'decorativa' si es solo estética.
- `description`: si es informativa, una descripción completa, no un texto alternativo corto. En
  un gráfico, qué representa, sus datos y la tendencia; en un esquema o mapa conceptual, sus partes
  y cómo se relacionan; en una foto que se analiza, lo que el estudiante tiene que observar. Sin
  empezar con "Imagen de" y sin inventar nada que no se vea en la imagen.
- `data_table`: los datos de un gráfico o una tabla como tabla Markdown; vacío si no tiene datos.
- `color_only`: la información que la imagen da solo con color; vacío si no la hay.
- `insert_after`: copiá tal cual, del texto del material, las últimas 5 a 12 palabras del párrafo
  después del cual estaba la imagen. Vacío si no se puede saber.

Redactá en español claro, respetando el registro del material (si usa voseo, mantené el voseo).
""".strip()

_NO_AI = AdaptationMetadata(
    provider="rules", model="sin-imagenes-v1", connection="Sin llamada a IA",
    latency_ms=0, prompt_tokens=0, output_tokens=0, thinking_tokens=0, total_tokens=0,
)
_MIN_ANCHOR_CHARS = 12
_ANCHOR_WORDS = 8
_UNPLACED_TITLE = "Imágenes que no se pudieron ubicar en el texto"


# --- Ubicar e insertar las descripciones ---------------------------------------------


def _context_anchor(context: str) -> str:
    """Las últimas palabras del texto anterior a la imagen (el contexto es 'anterior [...] posterior')."""
    before = context.split(" [...] ")[0].strip()
    words = before.split()
    if before.endswith("...") or before.endswith("…"):
        words = words[:-1]  # recortado: la última palabra puede estar cortada
    return " ".join(words[-_ANCHOR_WORDS:])


def _normalized(lines: list[str]) -> tuple[str, list[int]]:
    """El texto en un solo renglón normalizado, con la posición donde termina cada renglón original."""
    joined, ends = "", []
    for line in lines:
        joined = f"{joined} {normalize(line)}" if joined else normalize(line)
        ends.append(len(joined))
    return joined, ends


def _find(joined: str, ends: list[int], quote: str, start: int = 0) -> tuple[int, int] | None:
    """
    (renglón donde termina la cita, posición del final) buscando desde `start`, sin mayúsculas,
    comillas ni espacios de más. Cada tramo de una cita con '...' se busca después del anterior.
    """
    parts = fragment_parts(quote)
    if not parts or len(" ".join(parts)) < _MIN_ANCHOR_CHARS:
        return None
    position = start
    for part in parts:
        found = joined.find(part, position)
        if found < 0:
            return None
        position = found + len(part)
    return next(i for i, e in enumerate(ends) if e >= position), position


def _block(image: ImageDescription) -> str:
    lines = [f"[Imagen {image.image_index} · {image.location}]", image.description or ""]
    if image.data_table:
        lines += ["Datos:", image.data_table.strip()]
    if image.color_only:
        lines.append(f"Información que la imagen daba solo con color: {image.color_only}")
    lines.append(f"[Fin de la imagen {image.image_index}]")
    return "\n".join(line for line in lines if line)


def build(text: str, items: list[tuple[ImageDescription, list[str]]]) -> tuple[NoImagesResult, list[str]]:
    """Inserta cada imagen informativa después del renglón de su primera cita que aparezca en el texto."""
    lines = text.splitlines()
    inserts: dict[int, list[str]] = {}
    unplaced: list[str] = []
    images, omitted, color_notes = [], [], []
    joined, ends = _normalized(lines)
    # Las imágenes van en el orden del documento: cada una se busca después de la cita de la anterior,
    # para que una consigna repetida ("Observá el gráfico y respondé") no las junte en su primera
    # aparición. Si no aparece más adelante, se busca desde el renglón de la anterior: dos imágenes
    # seguidas después del mismo párrafo.
    cursor = line_start = 0
    for image, anchors in sorted(items, key=lambda item: item[0].image_index):
        if image.role == "decorativa":
            images.append(image.model_copy(update={"placement": None}))
            omitted.append(f"{image.location}: {image.description}" if image.description else image.location)
            continue
        match = next(
            (m for start in dict.fromkeys((cursor, line_start)) for a in anchors if a
             if (m := _find(joined, ends, a, start)) is not None),
            None,
        )
        line = match[0] if match else None
        if match is None:
            unplaced.append(_block(image))
        else:
            inserts.setdefault(line, []).append(_block(image))
            cursor = match[1]
            line_start = ends[line - 1] if line else 0
        images.append(image.model_copy(update={"placement": "al_final" if line is None else "en_su_lugar"}))
        if image.color_only:
            color_notes.append(f"Imagen {image.image_index} ({image.location}): {image.color_only}")

    out: list[str] = []
    for i, line in enumerate(lines):
        out.append(line)
        for block in inserts.get(i, []):
            out += ["", block, ""]
    if unplaced:
        out += ["", _UNPLACED_TITLE, "", *[f"{b}\n" for b in unplaced]]
    result_text = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()

    warnings = []
    if unplaced:
        warnings.append(
            f"{len(unplaced)} imagen{'es' if len(unplaced) != 1 else ''} no se pudo ubicar en el texto: "
            f"su descripción quedó al final, en «{_UNPLACED_TITLE}». Revisá dónde corresponde."
        )
    if omitted:
        warnings.append(f"Se omitieron {len(omitted)} imagen{'es' if len(omitted) != 1 else ''} decorativa{'s' if len(omitted) != 1 else ''}.")
    if color_notes:
        warnings.append("Hay imágenes que daban información solo con color: quedó explicada en texto (ver color_notes).")
    result = NoImagesResult(text=result_text, images=images, omitted_images=omitted, color_notes=color_notes)
    return result, warnings


# --- Generación ----------------------------------------------------------------------


def _from_alt_texts(material: SourceMaterial) -> list[tuple[ImageDescription, list[str]]]:
    return [
        (
            ImageDescription(image_index=a.image_index, location=a.location_description, role="informativa",
                             description=a.suggested_alt_text, source="diagnostico"),
            [_context_anchor(a.context_used)],
        )
        for a in material.alt_texts
    ]


def _vision_contents(material: SourceMaterial, text: str) -> list[dict]:
    contents: list[dict] = [{"type": "text", "text": f"Texto del material:\n<<<\n{text}\n>>>\n\nImágenes del material:"}]
    for img in material.images:
        contents.append({"type": "image", "data": img.image_bytes, "mime_type": img.mime_type})
        contents.append({
            "type": "text",
            "text": f"--- IMAGEN {img.image_index} ---\nUbicación: {img.location_description}\n"
                    f"Texto que la rodeaba:\n\"\"\"{img.context_text}\"\"\"",
        })
    return contents


async def generate(material: SourceMaterial) -> Generation:
    warnings: list[str] = []
    if material.images:
        text, warnings = source_for_ai(material.text)
        result = await llm_service.generate_structured(
            system_instruction=SYSTEM_PROMPT,
            contents=_vision_contents(material, text),
            schema=NoImagesAIOutput,
            temperature=0.2,
            require_vision=True,
        )
        by_index = {i.image_index: i for i in result.parsed.images}
        items = []
        for img in material.images:
            ai = by_index.get(img.image_index)
            if ai is None:
                warnings.append(f"La IA no describió la imagen {img.image_index} ({img.location_description}): describila a mano.")
                description = ImageDescription(
                    image_index=img.image_index, location=img.location_description, role="informativa",
                    description="Falta la descripción de esta imagen: revisala en el material original.", source="ia",
                )
                items.append((description, [_context_anchor(img.context_text)]))
                continue
            description = ImageDescription(
                image_index=img.image_index, location=img.location_description, role=ai.role,
                description=ai.description.strip() or None, data_table=ai.data_table.strip() or None,
                color_only=ai.color_only.strip() or None, source="ia",
            )
            items.append((description, [ai.insert_after, _context_anchor(img.context_text)]))
        metadata = metadata_from(result)
    elif material.alt_texts:
        items = _from_alt_texts(material)
        metadata = _NO_AI
        warnings.append(
            "Se usaron los textos alternativos que propuso el diagnóstico (no se volvieron a mirar las imágenes). "
            "Para descripciones completas, con los datos de gráficos y tablas, transformá el archivo con file_id."
        )
    else:
        items, metadata = [], _NO_AI
        warnings.append("El material no tiene imágenes con contenido: la versión sin imágenes es igual al original.")

    result, build_warnings = build(material.text, items)
    return Generation(result=result, metadata=metadata, warnings=[*warnings, *build_warnings])


def mock(material: SourceMaterial) -> NoImagesResult:
    """Descripciones simuladas en el lugar de cada imagen, con la misma inserción que el modo real."""
    items = [
        (
            ImageDescription(image_index=img.image_index, location=img.location_description, role="informativa",
                             description=f"[MOCK] Descripción simulada de la imagen {img.image_index}.", source="ia"),
            [_context_anchor(img.context_text)],
        )
        for img in material.images
    ] or _from_alt_texts(material)
    return build(material.text, items)[0]


def _export(fmt: str):
    def exporter(result: NoImagesResult) -> tuple[bytes, str]:
        notes = [f"Imagen decorativa omitida: {o}" for o in result.omitted_images]
        return export_document("Material sin imágenes", result.text, fmt, notes=notes)
    return exporter


SPEC = FormatSpec(
    schema=NoImagesResult,
    filename="material_sin_imagenes",
    mock=mock,
    generate=generate,
    uses_ai=lambda material: bool(material.images),  # sin imágenes o con el diagnóstico, no llama a la IA
    exports={fmt: _export(fmt) for fmt in ("docx", "pdf", "md", "txt")},
)
