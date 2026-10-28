"""
Mapa mental (Issue #118): tema central, ideas principales y detalles del material.

La IA solo devuelve la jerarquía (una llamada de texto); el dibujo lo hace el frontend con la
sintaxis Mermaid `mindmap` que arma el backend. Como un diagrama no es accesible por sí solo,
también se devuelve la misma jerarquía como lista anidada en Markdown.
"""
import re

from app.schemas.transformation import (
    MIND_MAP_MAX_CHILDREN,
    MIND_MAP_TEXT_CHARS,
    MindMapAIOutput,
    MindMapNode,
    MindMapResult,
)
from app.services.llm.base import LLMInvalidResponseError
from app.services.llm_service import llm_service
from app.services.rules_service import split_paragraphs, split_sentences
from app.services.transformations.base import FormatSpec, Generation, clip, metadata_from, source_for_ai

SYSTEM_PROMPT = """
Sos un especialista en accesibilidad de materiales educativos y en Diseño Universal para el
Aprendizaje (DUA). Un docente de nivel secundario o superior te pide un mapa mental de su
material, para que sus estudiantes vean de un vistazo de qué trata y cómo se relacionan las ideas.

## Reglas obligatorias
- Usá solo ideas que están en el material. No agregues conceptos, ejemplos, datos ni
  conclusiones que no aparezcan, aunque sean correctos.
- `central_topic`: de qué trata el material.
- `ideas`: de 2 a 6 ideas principales, en el orden en que aparecen en el material.
- `details`: solo los detalles importantes de cada idea (0 a 4); `subdetails`, solo ejemplos o
  datos concretos del material (0 a 3). Una idea sin detalles está bien.
- Cada nodo en pocas palabras (máximo 8): una frase nominal, sin oraciones largas ni punto final.
- Respetá la terminología propia de la disciplina y el registro del original (si usa voseo,
  mantené el voseo).
- Texto plano: sin Markdown, sin numeración ni viñetas dentro de los nodos.
""".strip()


def _node(text: str) -> str:
    return clip(text, MIND_MAP_TEXT_CHARS)


def from_ai(output: MindMapAIOutput) -> tuple[MindMapResult, list[str]]:
    """Convierte los niveles fijos de la IA en el árbol, dentro de los límites de legibilidad."""
    dropped = 0

    def capped(items: list) -> list:
        nonlocal dropped
        dropped += max(0, len(items) - MIND_MAP_MAX_CHILDREN)
        return items[:MIND_MAP_MAX_CHILDREN]

    ideas = []
    for idea in capped([i for i in output.ideas if i.text.strip()]):
        details = []
        for detail in capped([d for d in idea.details if d.text.strip()]):
            leaves = [MindMapNode(text=_node(s)) for s in capped([s for s in detail.subdetails if s.strip()])]
            details.append(MindMapNode(text=_node(detail.text), children=leaves))
        ideas.append(MindMapNode(text=_node(idea.text), children=details))

    if not output.central_topic.strip() or not ideas:
        raise LLMInvalidResponseError("El modelo devolvió un mapa mental sin tema central o sin ideas principales.")
    warnings = []
    if dropped:
        warnings.append(
            f"Se omitieron {dropped} rama{'s' if dropped != 1 else ''} para que el mapa no supere "
            f"{MIND_MAP_MAX_CHILDREN} por nodo y siga siendo legible."
        )
    return MindMapResult(root=MindMapNode(text=_node(output.central_topic), children=ideas)), warnings


async def generate(text: str) -> Generation:
    source, warnings = source_for_ai(text)
    result = await llm_service.generate_structured(
        system_instruction=SYSTEM_PROMPT,
        contents=f"Material:\n<<<\n{source}\n>>>",
        schema=MindMapAIOutput,
        temperature=0.2,
    )
    mind_map, limit_warnings = from_ai(result.parsed)
    return Generation(result=mind_map, metadata=metadata_from(result), warnings=[*warnings, *limit_warnings])


def mock(text: str) -> MindMapResult:
    """Tema central = primer renglón; ideas = la primera oración de los párrafos siguientes."""
    paragraphs = [p.strip() for p in split_paragraphs(text) if p.strip()] or [text.strip()]
    title = paragraphs[0].splitlines()[0]
    rest = paragraphs[1:] or paragraphs
    ideas = [MindMapNode(text=_node((split_sentences(p) or [p])[0])) for p in rest[:4]]
    return MindMapResult(root=MindMapNode(text=_node(title), children=ideas))


# --- Representaciones -----------------------------------------------------------------

_MD_SPECIAL = re.compile(r"([\\`*_\[\]<>#|])")
_MD_LINE_START = re.compile(r"^(\d+)([.)])|^([-+>])")


def _markdown_text(text: str) -> str:
    """Escapa lo que Markdown interpretaría (énfasis, enlaces, HTML, un número al principio que haría otra lista)."""
    escaped = _MD_SPECIAL.sub(r"\\\1", text)
    return _MD_LINE_START.sub(lambda m: f"{m.group(1)}\\{m.group(2)}" if m.group(1) else f"\\{m.group(3)}", escaped)


def _walk(node: MindMapNode, depth: int = 0):
    yield depth, node.text
    for child in node.children:
        yield from _walk(child, depth + 1)


def to_markdown(result: MindMapResult) -> str:
    """Lista anidada: se lee completa y en orden con lector de pantalla."""
    return "\n".join(f"{'  ' * depth}- {_markdown_text(text)}" for depth, text in _walk(result.root)) + "\n"


def to_text(result: MindMapResult) -> str:
    lines = [f"{'  ' * (depth - 1)}- {text}" if depth else text for depth, text in _walk(result.root)]
    return "\n".join(lines) + "\n"


# Mermaid decodifica entidades con la forma #código; dentro de un texto entre comillas solo
# rompen la sintaxis las comillas, el propio # y los caracteres que activan Markdown o HTML.
_MERMAID_ENTITIES = {"#": "#35;", '"': "#34;", "`": "#96;", "<": "#60;", ">": "#62;"}


def _mermaid_text(text: str) -> str:
    return "".join(_MERMAID_ENTITIES.get(c, c) for c in " ".join(text.split()))


def to_mermaid(result: MindMapResult) -> str:
    """Sintaxis `mindmap` de Mermaid: la jerarquía va por la sangría, cada nodo con su id y texto escapado."""
    lines = ["mindmap"]
    for i, (depth, text) in enumerate(_walk(result.root)):
        label = _mermaid_text(text)
        lines.append(f"{'  ' * (depth + 1)}" + (f'root(("{label}"))' if depth == 0 else f'n{i}["{label}"]'))
    return "\n".join(lines) + "\n"


def views(result: MindMapResult) -> dict[str, str]:
    return {"markdown": to_markdown(result), "mermaid": to_mermaid(result)}


SPEC = FormatSpec(
    schema=MindMapResult,
    filename="mapa_mental",
    mock=mock,
    generate=generate,
    views=views,
    exports={
        "md": lambda r: (to_markdown(r).encode("utf-8"), "text/markdown; charset=utf-8"),
        "txt": lambda r: (to_text(r).encode("utf-8"), "text/plain; charset=utf-8"),
    },
)
