"""
Audio (Issue #122): el material como un audio que el docente escucha, revisa y comparte.

1. La IA convierte el material en un **guion para escuchar**, dividido en secciones: frases cortas,
   números, fechas, siglas y símbolos escritos como se dicen, listas en prosa, y las imágenes y
   tablas contadas con palabras. Si el material tiene imágenes, primero pasa por la versión sin
   imágenes (#117), que las describe en su lugar.
2. El docente revisa y edita el guion. Recién al aceptarlo se genera el audio (render_after_decision):
   así no se gasta TTS en una versión que se descarta.
3. Cada sección se convierte en voz con tts_service (#121, Gemini TTS, voz Kore). Sale un MP3
   completo y un ZIP con un MP3 por sección (capítulos). El guion final es la transcripción.
"""
import asyncio
import io
import re
import unicodedata
import zipfile

from app.core.config import settings
from app.schemas.adaptation import AdaptationMetadata
from app.schemas.transformation import (
    AUDIO_MAX_SECTIONS,
    AUDIO_TITLE_CHARS,
    AudioAIOutput,
    AudioResult,
    AudioSection,
)
from app.services.llm.base import LLMInvalidResponseError
from app.services.llm_service import llm_service
from app.services.rules_service import split_paragraphs
from app.services.transformations import no_images
from app.services.transformations.base import (
    FormatSpec,
    GeneratedFile,
    Generation,
    Progress,
    RenderedFiles,
    SourceMaterial,
    clip,
    metadata_from,
    source_for_ai,
)
from app.services.tts import audio as tts_audio
from app.services.tts_service import tts_service

SYSTEM_PROMPT = f"""
Sos guionista de audios educativos accesibles. Un docente quiere su material como audio para
estudiantes que lo van a escuchar (con discapacidad visual, dislexia o para repasar): convertí el
material en un guion para leer en voz alta.

- Respetá el contenido: no agregues información que no esté en el material y no omitas ideas,
  consignas ni datos. Si el material usa voseo, mantené el voseo.
- Frases cortas, una idea por oración, en el orden del material.
- Escribí como se dicen los números, las fechas, las horas, los porcentajes, las unidades, las
  fórmulas y los símbolos: "2.000 millones" → "dos mil millones"; "25 %" → "veinticinco por
  ciento"; "1810" → "mil ochocientos diez"; "km/h" → "kilómetros por hora"; "H2O" → "hache dos o".
- Siglas: la primera vez, el nombre completo y después la sigla; si la sigla se lee letra por
  letra, separala ("A, D, N").
- Imágenes, gráficos y tablas (en el material aparecen como "[Imagen N · …]" y "Datos:"): contá
  qué muestran y sus datos importantes. Una tabla corta, fila por fila; una larga, su tendencia y
  los valores extremos. La información que se daba solo con color, en palabras.
- Listas y viñetas, en prosa: "Son tres pasos. Primero, … Segundo, … Tercero, …".
- Sin Markdown, sin viñetas, sin emojis, sin URLs (decí "el enlace que está en el material") y
  sin encabezados repetidos de página.
- Dividí el guion en secciones (2 a 8) que sigan la estructura del material. El título de cada
  sección se lee antes de su texto: no lo repitas en el texto. La primera sección empieza con una
  presentación breve: de qué trata el audio y cuántas secciones tiene.
- Largo total: hasta {f"{settings.TTS_MAX_CHARS:,}".replace(",", ".")} caracteres.

Respondé con este JSON, con las claves exactamente así (en inglés):
- `title`: el título del audio, en pocas palabras.
- `sections`: la lista de secciones; cada una con `title` (su título corto) y `text` (lo que se lee).
""".strip()

# Pausa entre secciones en el audio completo (entre fragmentos de una sección es más corta)
SECTION_PAUSE_S = 1.0


# --- Guion ---------------------------------------------------------------------------


def from_ai(output: AudioAIOutput) -> tuple[AudioResult, list[str]]:
    """Recorta títulos, saca secciones vacías y respeta el límite de secciones y de caracteres."""
    sections = [
        AudioSection(title=clip(s.title, AUDIO_TITLE_CHARS), text=s.text.strip())
        for s in output.sections if s.title.strip() and s.text.strip()
    ]
    if not output.title.strip() or not sections:
        raise LLMInvalidResponseError("El modelo devolvió un guion sin título o sin secciones.")
    warnings: list[str] = []
    if len(sections) > AUDIO_MAX_SECTIONS:
        warnings.append(f"El guion tenía {len(sections)} secciones: se juntaron las últimas para no superar {AUDIO_MAX_SECTIONS}.")
        tail = sections[AUDIO_MAX_SECTIONS - 1:]
        sections = [*sections[:AUDIO_MAX_SECTIONS - 1],
                    AudioSection(title=tail[0].title, text="\n\n".join(f"{s.title}. {s.text}" for s in tail))]
    title = clip(output.title, AUDIO_TITLE_CHARS)

    def excess() -> int:
        return AudioResult.model_construct(title=title, sections=sections).characters() - settings.TTS_MAX_CHARS

    dropped = 0
    while len(sections) > 1 and excess() > 0:
        sections.pop()
        dropped += 1
    cut = excess() > 0
    if cut:  # una sola sección más larga que el máximo: se corta su texto
        last = sections[-1]
        sections[-1] = AudioSection(title=last.title, text=clip(last.text, max(1, len(last.text) - excess())))
    if dropped or cut:
        warnings.append(
            f"El guion superaba el máximo de {settings.TTS_MAX_CHARS} caracteres (el largo máximo de un audio): "
            f"se acortó{f' y se omitieron las últimas {dropped} secciones' if dropped else ''}. "
            "Revisá el guion antes de aceptarlo."
        )
    return AudioResult(title=title, sections=sections), warnings


def _sum_metadata(first: AdaptationMetadata, second: AdaptationMetadata) -> AdaptationMetadata:
    """Las dos llamadas (descripción de imágenes y guion): el modelo del guion y los tokens y la latencia sumados."""
    def add(a, b):
        return None if a is None and b is None else (a or 0) + (b or 0)
    return second.model_copy(update={
        "latency_ms": first.latency_ms + second.latency_ms,
        "prompt_tokens": add(first.prompt_tokens, second.prompt_tokens),
        "output_tokens": add(first.output_tokens, second.output_tokens),
        "thinking_tokens": add(first.thinking_tokens, second.thinking_tokens),
        "total_tokens": add(first.total_tokens, second.total_tokens),
    })


async def generate(material: SourceMaterial) -> Generation:
    text, warnings, described = material.text, [], None
    if material.images or material.alt_texts:
        # Las imágenes y tablas, descriptas en su lugar con la lógica del material sin imágenes (#117)
        described = await no_images.generate(material)
        text = described.result.text
        warnings += described.warnings
    source, cut_warnings = source_for_ai(text)
    result = await llm_service.generate_structured(
        system_instruction=SYSTEM_PROMPT,
        contents=f"Material:\n<<<\n{source}\n>>>",
        schema=AudioAIOutput,
        temperature=0.3,
        max_output_tokens=16_384,
    )
    script, script_warnings = from_ai(result.parsed)
    metadata = metadata_from(result)
    if described is not None and described.metadata.total_tokens:
        metadata = _sum_metadata(described.metadata, metadata)
    return Generation(result=script, metadata=metadata, warnings=[*warnings, *cut_warnings, *script_warnings])


def mock(material: SourceMaterial) -> AudioResult:
    """Título = primer renglón; hasta 3 secciones con los párrafos siguientes, recortados."""
    paragraphs = [p.strip() for p in split_paragraphs(material.text) if p.strip()] or [material.text.strip()]
    title = paragraphs[0].splitlines()[0]
    rest = paragraphs[1:] or paragraphs
    sections = [AudioSection(title=f"Parte {i}", text=clip(p, 400)) for i, p in enumerate(rest[:3], start=1)]
    return AudioResult(title=clip(title, AUDIO_TITLE_CHARS), sections=sections)


# --- Transcripción -------------------------------------------------------------------


def transcript_markdown(result: AudioResult) -> str:
    """El guion, que es lo que se escucha: para personas sordas o con hipoacusia y para buscar en el audio."""
    parts = [f"# {result.title}", ""]
    for i, s in enumerate(result.sections, start=1):
        parts += [f"## {i}. {s.title}", "", s.text, ""]
    return "\n".join(parts)


def transcript_text(result: AudioResult) -> str:
    parts = [result.title.upper(), "=" * 60, ""]
    for i, s in enumerate(result.sections, start=1):
        parts += [f"{i}. {s.title}", "", s.text, ""]
    return "\n".join(parts)


# --- Audio ---------------------------------------------------------------------------


def _slug(text: str) -> str:
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9]+", "_", ascii_text).strip("_")[:40] or "seccion"


def _chapters_zip(chapters: list[tuple[str, bytes]]) -> bytes:
    buffer = io.BytesIO()
    # MP3 ya está comprimido: se guarda sin volver a comprimir
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as zf:
        for name, data in chapters:
            zf.writestr(name, data)
    return buffer.getvalue()


async def render(result: AudioResult, mock: bool, on_progress: Progress) -> RenderedFiles:
    """Una síntesis por sección: el MP3 completo (con una pausa entre secciones) y un MP3 por sección en un ZIP."""
    texts = result.section_texts()
    total = sum(tts_service.count_chunks(t) for t in texts)
    done = 0
    pcm_sections = []
    for text in texts:
        async def progress(section_done: int, _section_total: int, before: int = done) -> None:
            await on_progress(before + section_done, total)
        pcm = await tts_service.synthesize_pcm(text, mock=mock, on_progress=progress)
        done += pcm.chunks
        pcm_sections.append(pcm)

    rate = pcm_sections[0].sample_rate
    pause = tts_audio.silence(SECTION_PAUSE_S, rate)
    full = pause.join(p.pcm for p in pcm_sections)
    chapter_mp3s = await asyncio.gather(*(asyncio.to_thread(tts_audio.to_mp3, p.pcm, rate) for p in pcm_sections))
    full_mp3 = await asyncio.to_thread(tts_audio.to_mp3, full, rate)
    chapters = [
        (f"{i:02d}_{_slug(s.title)}{tts_audio.EXTENSION}", data)
        for i, (s, data) in enumerate(zip(result.sections, chapter_mp3s), start=1)
    ]
    minutes, seconds = divmod(round(tts_audio.duration_s(full, rate)), 60)
    duration = f"{minutes} min {seconds} s" if minutes else f"{seconds} s"
    return RenderedFiles(
        main=GeneratedFile(data=full_mp3, content_type=tts_audio.CONTENT_TYPE, extension=tts_audio.EXTENSION),
        extras=[("_capitulos", GeneratedFile(data=_chapters_zip(chapters), content_type="application/zip", extension=".zip"))],
        warnings=[f"Audio de {duration} en {len(chapters)} capítulos."],
    )


def _export(fmt: str):
    def exporter(result: AudioResult) -> tuple[bytes, str]:
        if fmt == "md":
            return transcript_markdown(result).encode("utf-8"), "text/markdown; charset=utf-8"
        return transcript_text(result).encode("utf-8"), "text/plain; charset=utf-8"
    return exporter


SPEC = FormatSpec(
    schema=AudioResult,
    filename="audio",
    mock=mock,
    generate=generate,
    render_after_decision=render,
    views=lambda result: {"transcript": transcript_markdown(result)},
    exports={fmt: _export(fmt) for fmt in ("md", "txt")},
)
