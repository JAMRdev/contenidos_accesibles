"""
Tipografía poco legible (issue #79, parte 3): cursiva, MAYÚSCULAS sostenidas, subrayado que no es
enlace, fuentes decorativas o manuscritas, texto justificado e interlineado reducido.

Recibe los fragmentos que arma legibility_extractor.py (con su formato ya resuelto para cada
formato de archivo) y agrupa los casos por regla. Respaldo: WCAG 1.4.8 (texto no justificado,
interlineado de al menos 1,5) y la guía de estilo de la British Dyslexia Association (evitar
cursiva, subrayado y mayúsculas sostenidas; fuentes simples). Los umbrales de palabras son un punto
de partida para calibrar con materiales reales (ver docs/motor_reglas.md).
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from app.schemas.document import TypographyIssue

# Se marcan pasajes largos, no una palabra o un título destacados.
MIN_ITALIC_WORDS = 20
MIN_CAPS_WORDS = 12
MIN_CAPS_RATIO = 0.8  # del total de palabras del párrafo: así no cuentan las siglas
MIN_UNDERLINE_WORDS = 5
MIN_DECORATIVE_WORDS = 15
# Justificado e interlineado se evalúan solo en párrafos de texto corrido.
MIN_LAYOUT_PARAGRAPH_WORDS = 30
MIN_LINE_SPACING = 1.5  # WCAG 1.4.8 (AAA); el equipo eligió este mínimo en lugar del 1,15 del issue

MAX_STORED_PAGES = 10
EXCERPT_WORDS = 40

RULE_ORDER = ("fuente_decorativa", "cursiva", "mayusculas", "subrayado", "justificado", "interlineado")

# Fuentes decorativas o manuscritas frecuentes (nombre normalizado: minúsculas, sin espacios).
# Comic Sans no está: la guía de la British Dyslexia Association la recomienda.
DECORATIVE_FONTS = (
    "algerian", "blackadder", "bradleyhand", "brushscript", "castellar", "chiller", "curlz",
    "dancingscript", "edwardianscript", "forte", "freestylescript", "frenchscript", "gigi",
    "greatvibes", "harrington", "inkfree", "informalroman", "jokerman", "juiceitc", "kristenitc",
    "kunstlerscript", "lobster", "lucidacalligraphy", "lucidahandwriting", "magneto", "matura",
    "mistral", "monotypecorsiva", "oldenglish", "pacifico", "palacescript", "papyrus", "parisienne",
    "pristina", "ragestalic", "ravie", "satisfy", "segoeprint", "segoescript", "showcardgothic",
    "snapitc", "tempussans", "vinerhand", "vivaldi", "vladimirscript",
)
DECORATIVE_KEYWORDS = ("script", "handwriting", "calligraph")
NOT_DECORATIVE = ("comicsans",)

_SUBSET_PREFIX_RE = re.compile(r"^[A-Z]{6}\+")


def display_font_name(name: str | None) -> str | None:
    """Nombre de la fuente para mostrar: sin el prefijo de subconjunto de los PDF ('ABCDEF+')."""
    if not name:
        return None
    return _SUBSET_PREFIX_RE.sub("", name).strip() or None


def _normalize_font(name: str) -> str:
    """'ABCDEF+BrushScriptMT-Italic' o 'Brush Script MT' -> 'brushscriptmt'."""
    name = _SUBSET_PREFIX_RE.sub("", name)
    name = re.split(r"[-,]", name, maxsplit=1)[0]  # sin "-Italic", ",Bold"
    return re.sub(r"[^a-z0-9]", "", name.lower())


def is_decorative_font(name: str | None) -> bool:
    if not name:
        return False
    normalized = _normalize_font(name)
    if not normalized or normalized.startswith(NOT_DECORATIVE):
        return False
    return normalized.startswith(DECORATIVE_FONTS) or any(k in normalized for k in DECORATIVE_KEYWORDS)


@dataclass
class ParagraphFormat:
    """Formato del párrafo (solo Word y PowerPoint). None: no se conoce."""

    justified: bool | None = None
    line_spacing: float | None = None  # múltiplo de interlineado sencillo (1.0 = sencillo)


@dataclass
class _Found:
    passages: int = 0
    words: int = 0
    example: str | None = None
    pages: list[int] = field(default_factory=list)
    details: Counter = field(default_factory=Counter)


def _excerpt(texts: list[str]) -> str:
    words = " ".join(texts).split()
    return " ".join(words[:EXCERPT_WORDS]) + ("…" if len(words) > EXCERPT_WORDS else "")


def _words(text: str) -> int:
    return len(text.split())


def _caps_counts(text: str) -> tuple[int, int]:
    """(palabras en mayúsculas, palabras evaluables). Solo cuentan palabras de 2 letras o más."""
    caps = counted = 0
    for word in text.split():
        letters = [c for c in word if c.isalpha()]
        if len(letters) < 2:
            continue
        counted += 1
        if all(c.isupper() for c in letters):
            caps += 1
    return caps, counted


def format_spacing(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


class TypographyCollector:
    """Agrupa por regla los párrafos con tipografía poco legible."""

    def __init__(self) -> None:
        self.found: dict[str, _Found] = {}
        self.font_chars: Counter = Counter()

    def _record(self, rule: str, page: int, words: int, texts: list[str], detail: str | None = None) -> None:
        entry = self.found.setdefault(rule, _Found())
        entry.passages += 1
        entry.words += words
        if entry.example is None:
            entry.example = _excerpt(texts)
        if page and page not in entry.pages and len(entry.pages) < MAX_STORED_PAGES:
            entry.pages.append(page)
        if detail:
            entry.details[detail] += words

    def add_paragraph(self, fragments, page: int | None, fmt: ParagraphFormat | None) -> None:
        """fragments: Fragment de legibility_extractor. page None: el formato no informa páginas."""
        fragments = [f for f in fragments if f.visible and f.text.strip()]
        texts = [f.text.strip() for f in fragments]
        if not texts:
            return
        total_words = sum(_words(t) for t in texts)
        italic, underline, decorative, caps_texts = [], [], [], []
        caps = counted = 0
        decorative_fonts: Counter = Counter()

        for fragment in fragments:
            text = fragment.text.strip()
            # Un PDF puede traer como nombre solo el prefijo de subconjunto ("ABCDEF+"): sin nombre
            # real, la fuente no cuenta (si no, None podría quedar como "fuente predominante").
            if fragment.font and (font_name := display_font_name(fragment.font)):
                self.font_chars[font_name] += len(text)
                if is_decorative_font(fragment.font):
                    decorative.append(text)
                    decorative_fonts[font_name] += _words(text)
            if fragment.italic:
                italic.append(text)
            if fragment.underline and not fragment.link:
                underline.append(text)
            shown = text.upper() if fragment.caps else text
            fragment_caps, fragment_counted = _caps_counts(shown)
            caps += fragment_caps
            counted += fragment_counted
            if fragment_caps:
                caps_texts.append(text)

        italic_words = sum(_words(t) for t in italic)
        if italic_words >= MIN_ITALIC_WORDS:
            self._record("cursiva", page, italic_words, italic)
        if caps >= MIN_CAPS_WORDS and counted and caps / counted >= MIN_CAPS_RATIO:
            self._record("mayusculas", page, caps, caps_texts)
        underline_words = sum(_words(t) for t in underline)
        if underline_words >= MIN_UNDERLINE_WORDS:
            self._record("subrayado", page, underline_words, underline)
        decorative_words = sum(_words(t) for t in decorative)
        if decorative_words >= MIN_DECORATIVE_WORDS:
            font = decorative_fonts.most_common(1)[0][0]
            self._record("fuente_decorativa", page, decorative_words, decorative, detail=font)

        if fmt is None or total_words < MIN_LAYOUT_PARAGRAPH_WORDS:
            return
        if fmt.justified:
            self._record("justificado", page, total_words, texts)
        if fmt.line_spacing is not None and fmt.line_spacing < MIN_LINE_SPACING - 1e-6:
            self._record("interlineado", page, total_words, texts, detail=format_spacing(fmt.line_spacing))

    def result(self) -> tuple[str | None, bool, list[TypographyIssue]]:
        """(fuente predominante, si es decorativa, problemas por regla)."""
        body_font = self.font_chars.most_common(1)[0][0] if self.font_chars else None
        issues = [
            TypographyIssue(
                rule=rule,
                passages=entry.passages,
                word_count=entry.words,
                example=entry.example or "",
                pages=sorted(entry.pages),
                detail=entry.details.most_common(1)[0][0] if entry.details else None,
            )
            for rule in RULE_ORDER
            if (entry := self.found.get(rule))
        ]
        return body_font, is_decorative_font(body_font), issues
