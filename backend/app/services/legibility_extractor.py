"""
Extracción de datos tipográficos para las reglas de legibilidad visual (issue #79).

Hace su propia pasada sobre el archivo ya abierto para no modificar la extracción de texto
ni la detección de títulos de document_service.py y office_service.py. Todo es local y en
memoria: no consume tokens.

Por cada formato se mide:
- body_font_size: el tamaño de letra predominante, ponderado por caracteres.
- small_text_passages: párrafos (o bloques del PDF) con muchas palabras en letra chica.
- low_contrast_passages: párrafos cuyo texto no alcanza el contraste de WCAG 1.4.3 con su fondo.

Criterio general: un dato que no se conoce (tamaño o color heredado del patrón de diapositivas,
color "automático" o de tema en Word, fondo con degradado o imagen) no se inventa. Ese
fragmento no se evalúa: es preferible no marcar nada que marcar algo falso.
"""

import colorsys
import functools
import logging
from dataclasses import dataclass

from docx.enum.dml import MSO_COLOR_TYPE as DOCX_COLOR_TYPE
from docx.oxml.ns import qn as w_qn
from docx.table import Table
from lxml import etree
from pptx.opc.constants import RELATIONSHIP_TYPE as RT
from pptx.oxml.ns import qn as p_qn
from pptx.shapes.group import GroupShape

from app.schemas.document import LegibilityStats, LowContrastPassage, SmallTextPassage

# Un pasaje cuenta como "letra chica" si tiene al menos MIN_SMALL_PASSAGE_WORDS palabras en menos
# de SMALL_PASSAGE_PT. Así no se marcan números de página, pies de foto de dos palabras ni marcas
# de agua cortas.
SMALL_PASSAGE_PT = 8.0
MIN_SMALL_PASSAGE_WORDS = 20
MAX_STORED_PASSAGES = 20  # el total se informa aparte en *_passages_total
PASSAGE_EXCERPT_WORDS = 40

# Contraste (WCAG 1.4.3, nivel AA): 4.5:1 para texto normal y 3:1 para texto grande.
NORMAL_TEXT_MIN_RATIO = 4.5
LARGE_TEXT_MIN_RATIO = 3.0
LARGE_TEXT_PT = 18.0
LARGE_BOLD_TEXT_PT = 14.0
# Pasajes de menos palabras ("Página 1 de 3" en gris, una fuente corta) no se marcan por contraste.
MIN_LOW_CONTRAST_WORDS = 5

WHITE = (255, 255, 255)
RGB = tuple[int, int, int]

logger = logging.getLogger(__name__)


def _never_fails(extract):
    """
    Si la lectura tipográfica falla con un archivo inesperado, el análisis sigue sin las reglas
    de legibilidad (LegibilityStats vacío) en lugar de rechazar el material del docente.
    """
    @functools.wraps(extract)
    def wrapper(document) -> LegibilityStats:
        try:
            return extract(document)
        except Exception:  # noqa: BLE001 - cualquier error del archivo deja las reglas sin datos
            logger.warning("No se pudieron leer los datos de legibilidad (%s)", extract.__name__, exc_info=True)
            return LegibilityStats()
    return wrapper


@dataclass
class Fragment:
    """Trozo de texto con formato uniforme. None en un dato significa 'no se conoce'."""

    text: str
    size: float | None = None
    color: RGB | None = None
    background: RGB | None = None
    bold: bool = False


# ---------------------------------------------------------------------------
# Contraste (WCAG 2.x)
# ---------------------------------------------------------------------------

def _channel(value: int) -> float:
    c = value / 255
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def relative_luminance(rgb: RGB) -> float:
    r, g, b = (_channel(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: RGB, b: RGB) -> float:
    """Relación de contraste entre dos colores, de 1 (iguales) a 21 (negro sobre blanco)."""
    la, lb = sorted((relative_luminance(a), relative_luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


def is_large_text(size: float | None, bold: bool) -> bool:
    return size is not None and (size >= LARGE_TEXT_PT or (bold and size >= LARGE_BOLD_TEXT_PT))


def hex_color(rgb: RGB) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def parse_hex(value: str | None) -> RGB | None:
    """'CCCCCC' o '#CCCCCC' a (204, 204, 204). 'auto' o un valor inválido devuelven None."""
    value = (value or "").lstrip("#")
    if len(value) != 6:
        return None
    try:
        return tuple(int(value[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _excerpt(texts: list[str]) -> str:
    words = " ".join(texts).split()
    return " ".join(words[:PASSAGE_EXCERPT_WORDS]) + ("…" if len(words) > PASSAGE_EXCERPT_WORDS else "")


class _Collector:
    """Acumula tamaños y contraste por caracteres, y arma las listas de pasajes."""

    def __init__(self) -> None:
        self.char_counts: dict[float, int] = {}
        self.passages: list[SmallTextPassage] = []
        self.passages_total = 0
        self.contrast_chars = 0
        self.low_contrast: list[LowContrastPassage] = []
        self.low_contrast_total = 0

    def add_paragraph(self, fragments: list[Fragment], page_number: int) -> None:
        """fragments: los trozos de un mismo párrafo (Word/PowerPoint) o bloque (PDF)."""
        page_number = max(page_number, 1)
        self._small_text(fragments, page_number)
        self._contrast(fragments, page_number)

    def _small_text(self, fragments: list[Fragment], page_number: int) -> None:
        small_texts: list[str] = []
        small_sizes: dict[float, int] = {}
        small_words = 0
        for fragment in fragments:
            stripped = fragment.text.strip()
            if not stripped or fragment.size is None:
                continue
            size = round(fragment.size, 1)
            self.char_counts[size] = self.char_counts.get(size, 0) + len(stripped)
            if size < SMALL_PASSAGE_PT:
                small_texts.append(stripped)
                small_sizes[size] = small_sizes.get(size, 0) + len(stripped)
                small_words += len(stripped.split())

        if small_words < MIN_SMALL_PASSAGE_WORDS:
            return
        self.passages_total += 1
        if len(self.passages) < MAX_STORED_PASSAGES:
            self.passages.append(SmallTextPassage(
                text=_excerpt(small_texts),
                page_number=page_number,
                font_size=max(small_sizes, key=small_sizes.get),
                word_count=small_words,
            ))

    def _contrast(self, fragments: list[Fragment], page_number: int) -> None:
        # (color, fondo, grande) -> [caracteres, palabras, textos, ratio]
        failing: dict[tuple[RGB, RGB, bool], list] = {}
        for fragment in fragments:
            stripped = fragment.text.strip()
            if not stripped or fragment.color is None or fragment.background is None:
                continue
            self.contrast_chars += len(stripped)
            ratio = contrast_ratio(fragment.color, fragment.background)
            large = is_large_text(fragment.size, fragment.bold)
            # Sin tamaño conocido no se sabe si el texto es grande: solo se marca lo que falla
            # incluso con el umbral de texto grande.
            required = LARGE_TEXT_MIN_RATIO if large or fragment.size is None else NORMAL_TEXT_MIN_RATIO
            if ratio >= required:
                continue
            key = (fragment.color, fragment.background, large)
            entry = failing.setdefault(key, [0, 0, [], ratio])
            entry[0] += len(stripped)
            entry[1] += len(stripped.split())
            entry[2].append(stripped)

        words = sum(e[1] for e in failing.values())
        if words < MIN_LOW_CONTRAST_WORDS:
            return
        self.low_contrast_total += 1
        if len(self.low_contrast) >= MAX_STORED_PASSAGES:
            return
        # Se informa la combinación de colores que más texto ocupa dentro del pasaje.
        (color, background, large), (_, _, _, ratio) = max(failing.items(), key=lambda item: item[1][0])
        self.low_contrast.append(LowContrastPassage(
            text=_excerpt([t for e in failing.values() for t in e[2]]),
            page_number=page_number,
            text_color=hex_color(color),
            background_color=hex_color(background),
            contrast_ratio=round(ratio, 2),
            required_ratio=LARGE_TEXT_MIN_RATIO if large else NORMAL_TEXT_MIN_RATIO,
            is_large_text=large,
            word_count=words,
        ))

    def result(self) -> LegibilityStats:
        return LegibilityStats(
            body_font_size=max(self.char_counts, key=self.char_counts.get) if self.char_counts else None,
            measured_chars=sum(self.char_counts.values()),
            small_text_passages=self.passages,
            small_text_passages_total=self.passages_total,
            contrast_measured_chars=self.contrast_chars,
            low_contrast_passages=self.low_contrast,
            low_contrast_passages_total=self.low_contrast_total,
        )


# ---------------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------------

MIN_BACKGROUND_FILL_OPACITY = 0.5


def _int_to_rgb(value: int) -> RGB:
    return ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)


def _float_to_rgb(fill) -> RGB | None:
    """Color de relleno de PyMuPDF (tupla de 0 a 1, en RGB, gris o CMYK) a RGB de 0 a 255."""
    if not fill:
        return None
    if len(fill) == 1:
        fill = (fill[0],) * 3
    elif len(fill) == 4:  # CMYK
        c, m, y, k = fill
        fill = ((1 - c) * (1 - k), (1 - m) * (1 - k), (1 - y) * (1 - k))
    elif len(fill) != 3:
        return None
    return tuple(round(max(0.0, min(1.0, v)) * 255) for v in fill)


def _pdf_fills(page) -> list[tuple]:
    """Rectángulos rellenos opacos de la página, en orden de dibujo: (rect, color)."""
    fills = []
    for drawing in page.get_drawings():
        color = _float_to_rgb(drawing.get("fill"))
        opacity = drawing.get("fill_opacity")
        if color is None or (opacity is not None and opacity < MIN_BACKGROUND_FILL_OPACITY):
            continue
        fills.append((drawing["rect"], color))
    return fills


def _pdf_background(bbox, fills: list[tuple]) -> RGB:
    """Fondo del texto: el último rectángulo relleno que contiene su centro; si no hay, blanco."""
    x = (bbox[0] + bbox[2]) / 2
    y = (bbox[1] + bbox[3]) / 2
    for rect, color in reversed(fills):
        if rect.x0 <= x <= rect.x1 and rect.y0 <= y <= rect.y1:
            return color
    return WHITE


@_never_fails
def extract_pdf_legibility(doc) -> LegibilityStats:
    """doc es un pymupdf.Document ya abierto. Cada bloque de texto se trata como un párrafo."""
    collector = _Collector()
    for page_idx in range(len(doc)):
        page = doc.load_page(page_idx)
        fills = _pdf_fills(page)
        for block in page.get_text("dict").get("blocks", []):
            if block.get("type") != 0:  # 0 = texto
                continue
            fragments = []
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    # alpha 0: texto invisible (por ejemplo, la capa de OCR de un escaneo).
                    visible = span.get("alpha", 255) > 0
                    fragments.append(Fragment(
                        text=span.get("text", ""),
                        size=span.get("size"),
                        color=_int_to_rgb(span.get("color", 0)) if visible else None,
                        background=_pdf_background(span["bbox"], fills) if visible else None,
                        bold=bool(span.get("flags", 0) & 16),
                    ))
            collector.add_paragraph(fragments, page_idx + 1)
    return collector.result()


# ---------------------------------------------------------------------------
# Word (.docx)
# ---------------------------------------------------------------------------

# Colores de resaltado de Word (w:highlight).
_HIGHLIGHT_COLORS = {
    "yellow": "FFFF00", "green": "00FF00", "cyan": "00FFFF", "magenta": "FF00FF",
    "blue": "0000FF", "red": "FF0000", "darkBlue": "000080", "darkCyan": "008080",
    "darkGreen": "008000", "darkMagenta": "800080", "darkRed": "800000",
    "darkYellow": "808000", "darkGray": "808080", "lightGray": "C0C0C0",
    "black": "000000", "white": "FFFFFF",
}


def _style_chain(style):
    while style is not None:
        yield style
        style = style.base_style


def _style_sizes(style):
    """Tamaño definido en el estilo o en alguno de sus estilos base."""
    for s in _style_chain(style):
        font = getattr(s, "font", None)
        if font is not None and font.size is not None:
            return font.size.pt
    return None


def _docx_default_size(document) -> float | None:
    """Tamaño de docDefaults (w:sz está en medios puntos). Word usa 10 pt si no está definido."""
    sz = document.styles.element.find(
        f"{w_qn('w:docDefaults')}/{w_qn('w:rPrDefault')}/{w_qn('w:rPr')}/{w_qn('w:sz')}"
    )
    if sz is not None and sz.get(w_qn("w:val"), "").isdigit():
        return int(sz.get(w_qn("w:val"))) / 2
    return 10.0


def _docx_run_size(run, paragraph, default_size: float | None) -> float | None:
    if run.font.size is not None:
        return run.font.size.pt
    for style in (run.style, paragraph.style):
        size = _style_sizes(style) if style is not None else None
        if size is not None:
            return size
    return default_size


def _rgb_of(font_color) -> tuple[bool, RGB | None]:
    """
    (definido, color). Con un color del tema, Word guarda además en w:val el color ya calculado
    (con su aclarado u oscurecido), así que se usa ese. Definido sin color ("Automático", o un
    color del tema sin w:val): no se puede saber cómo se ve.
    """
    if font_color is None or font_color.type is None:
        return False, None
    if font_color.type not in (DOCX_COLOR_TYPE.RGB, DOCX_COLOR_TYPE.THEME):
        return True, None
    rgb = font_color.rgb
    return True, tuple(rgb) if rgb is not None else None


def _docx_run_color(run, paragraph) -> RGB | None:
    """Color explícito del run o heredado de sus estilos. Automático o de tema: None (no se evalúa)."""
    defined, rgb = _rgb_of(run.font.color)
    if defined:
        return rgb
    for style in (run.style, paragraph.style):
        for s in _style_chain(style):
            font = getattr(s, "font", None)
            if font is None:
                continue
            defined, rgb = _rgb_of(font.color)
            if defined:
                return rgb
    return None


def _shading_fill(properties) -> RGB | None:
    """Relleno de w:shd dentro de rPr, pPr o tcPr ('auto' o ausente: None)."""
    if properties is None:
        return None
    shd = properties.find(w_qn("w:shd"))
    return parse_hex(shd.get(w_qn("w:fill"))) if shd is not None else None


def _docx_run_background(run, paragraph_fill: RGB | None) -> RGB | None:
    r_pr = run._r.rPr
    if r_pr is not None:
        highlight = r_pr.find(w_qn("w:highlight"))
        if highlight is not None and highlight.get(w_qn("w:val")) in _HIGHLIGHT_COLORS:
            return parse_hex(_HIGHLIGHT_COLORS[highlight.get(w_qn("w:val"))])
        run_fill = _shading_fill(r_pr)
        if run_fill is not None:
            return run_fill
    return paragraph_fill


def _docx_page_background(document) -> RGB:
    """Color de página (Diseño > Color de página); blanco si no está definido."""
    background = document.element.find(w_qn("w:background"))
    color = parse_hex(background.get(w_qn("w:color"))) if background is not None else None
    return color or WHITE


def _docx_paragraphs(document):
    """(párrafo, relleno de la celda) del cuerpo y de las tablas (celdas combinadas, una sola vez)."""
    for item in document.iter_inner_content():
        if isinstance(item, Table):
            seen = set()
            for row in item.rows:
                for cell in row.cells:
                    if id(cell._tc) in seen:
                        continue
                    seen.add(id(cell._tc))
                    cell_fill = _shading_fill(cell._tc.tcPr)
                    for paragraph in cell.paragraphs:
                        yield paragraph, cell_fill
        else:
            yield item, None


@_never_fails
def extract_docx_legibility(document) -> LegibilityStats:
    """document es un docx.Document ya abierto. Word no informa páginas: todo va como página 1."""
    collector = _Collector()
    default_size = _docx_default_size(document)
    page_background = _docx_page_background(document)
    for paragraph, cell_fill in _docx_paragraphs(document):
        paragraph_fill = _shading_fill(paragraph._p.pPr) or cell_fill or page_background
        style_bold = any(
            getattr(s, "font", None) is not None and s.font.bold for s in _style_chain(paragraph.style)
        )
        fragments = [
            Fragment(
                text=run.text,
                size=_docx_run_size(run, paragraph, default_size),
                color=_docx_run_color(run, paragraph),
                background=_docx_run_background(run, paragraph_fill),
                bold=run.bold if run.bold is not None else style_bold,
            )
            for run in paragraph.runs
        ]
        collector.add_paragraph(fragments, 1)
    return collector.result()


# ---------------------------------------------------------------------------
# PowerPoint (.pptx)
# ---------------------------------------------------------------------------
#
# PowerPoint casi nunca guarda colores RGB: usa colores del tema ("Texto 1", "Énfasis 2, más claro
# 40%") que se resuelven así: el patrón traduce el nombre (clrMap: tx1 -> dk1), el tema da el
# color base y los modificadores (lumMod, lumOff, tint, shade) lo aclaran u oscurecen. El texto que
# no define color lo hereda de su lista de estilos, del marcador de posición del diseño y del patrón,
# de los estilos de texto del patrón o del estilo por defecto de la presentación.

# Nombres de clrMap que se traducen; los demás (dk1, lt1, accent1…) son colores del tema directos.
_MAPPED_SCHEME_NAMES = (
    "bg1", "tx1", "bg2", "tx2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6",
    "hlink", "folHlink",
)
_COLOR_NAMES = {"srgbClr", "schemeClr", "sysClr", "scrgbClr", "prstClr", "hslClr"}
_A_NS = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
_TITLE_PLACEHOLDERS = ("title", "ctrTitle")
_BODY_PLACEHOLDERS = ("body", "subTitle", "obj", None)  # sin tipo = contenido (obj)


def _pptx_font_scale(text_frame) -> float:
    """Factor de 'Reducir el texto al desbordar' (normAutofit fontScale, en milésimas de %)."""
    autofit = text_frame._bodyPr.find(p_qn("a:normAutofit"))
    scale = autofit.get("fontScale") if autofit is not None else None
    return int(scale) / 100_000 if scale and scale.isdigit() else 1.0


def _apply_modifiers(rgb: RGB, color_el) -> RGB:
    """Aplica lumMod/lumOff (en HSL, como PowerPoint), tint (hacia blanco) y shade (hacia negro)."""
    mods = {_localname(m): int(m.get("val", "100000")) / 100_000 for m in color_el}
    r, g, b = (v / 255 for v in rgb)
    if "lumMod" in mods or "lumOff" in mods:
        h, lum, s = colorsys.rgb_to_hls(r, g, b)
        lum = max(0.0, min(1.0, lum * mods.get("lumMod", 1.0) + mods.get("lumOff", 0.0)))
        r, g, b = colorsys.hls_to_rgb(h, lum, s)
    if "tint" in mods:
        r, g, b = (c * mods["tint"] + (1 - mods["tint"]) for c in (r, g, b))
    if "shade" in mods:
        r, g, b = (c * mods["shade"] for c in (r, g, b))
    return tuple(round(max(0.0, min(1.0, c)) * 255) for c in (r, g, b))


def _localname(element) -> str:
    return etree.QName(element).localname


class _PptxTheme:
    """Colores y estilos de relleno del tema de un patrón, con su clrMap."""

    def __init__(self, master) -> None:
        self.scheme: dict[str, RGB] = {}
        self.fill_styles: list = []
        self.bg_fill_styles: list = []
        try:
            theme = etree.fromstring(master.part.part_related_by(RT.THEME).blob)
        except (KeyError, etree.XMLSyntaxError):
            theme = None
        if theme is not None:
            for slot in theme.iterfind(".//a:clrScheme/*", _A_NS):
                base = slot[0] if len(slot) else None
                if base is None:
                    continue
                value = base.get("lastClr") if _localname(base) == "sysClr" else base.get("val")
                rgb = parse_hex(value)
                if rgb is not None:
                    self.scheme[_localname(slot)] = rgb
            fmt = theme.find(".//a:fmtScheme", _A_NS)
            if fmt is not None:
                fills = fmt.find("a:fillStyleLst", _A_NS)
                bg_fills = fmt.find("a:bgFillStyleLst", _A_NS)
                self.fill_styles = list(fills) if fills is not None else []
                self.bg_fill_styles = list(bg_fills) if bg_fills is not None else []
        clr_map = master._element.find(p_qn("p:clrMap"))
        self.master_map = dict(clr_map.attrib) if clr_map is not None else {}

    @staticmethod
    def color_map(master_map: dict, *parts) -> dict:
        """clrMap del patrón, con la redefinición del diseño o la diapositiva si la tienen."""
        mapping = dict(master_map)
        for part in parts:
            override = part._element.find(f"{p_qn('p:clrMapOvr')}/{p_qn('a:overrideClrMapping')}")
            if override is not None:
                mapping = dict(override.attrib)
        return mapping

    def resolve(self, color_el, mapping: dict, placeholder: RGB | None = None) -> RGB | None:
        """Elemento de color de DrawingML a RGB. phClr usa el color del estilo que lo llama."""
        if color_el is None:
            return None
        kind = _localname(color_el)
        if kind == "srgbClr":
            base = parse_hex(color_el.get("val"))
        elif kind == "sysClr":
            base = parse_hex(color_el.get("lastClr"))
        elif kind == "schemeClr":
            name = color_el.get("val")
            if name == "phClr":
                base = placeholder
            else:
                if name in _MAPPED_SCHEME_NAMES:
                    name = mapping.get(name, name)
                base = self.scheme.get(name)
        else:  # scrgbClr, prstClr, hslClr: poco usados en materiales; no se evalúan
            base = None
        if base is None:
            return None
        if any(_localname(m) == "alpha" and int(m.get("val", "100000")) < 50_000 for m in color_el):
            return None  # muy transparente: el color real depende de lo que hay detrás
        return _apply_modifiers(base, color_el)

    def fill(self, properties, mapping: dict, placeholder: RGB | None = None) -> tuple[bool, RGB | None]:
        """
        (define relleno, color) de spPr, tcPr o bgPr. noFill o sin relleno: no define (se ve lo de
        atrás). Degradado, imagen o trama: definido pero desconocido.
        """
        if properties is None:
            return False, None
        solid = properties.find(p_qn("a:solidFill"))
        if solid is not None:
            return True, self.resolve(_first_color(solid), mapping, placeholder)
        if any(properties.find(p_qn(t)) is not None for t in ("a:gradFill", "a:blipFill", "a:pattFill", "a:grpFill")):
            return True, None
        return False, None

    def style_fill(self, ref_el, mapping: dict) -> tuple[bool, RGB | None]:
        """Relleno de fillRef/bgRef: idx 1-999 en fillStyleLst, 1001+ en bgFillStyleLst; 0 = sin relleno."""
        idx = int(ref_el.get("idx", "0"))
        if idx == 0:
            return False, None
        styles, position = (self.bg_fill_styles, idx - 1001) if idx >= 1001 else (self.fill_styles, idx - 1)
        if not 0 <= position < len(styles):
            return True, None
        style = styles[position]
        color = self.resolve(_first_color(ref_el), mapping)
        if _localname(style) == "solidFill":
            return True, self.resolve(_first_color(style), mapping, placeholder=color)
        if _localname(style) == "noFill":
            return False, None
        return True, None


def _first_color(element):
    """Primer elemento de color (srgbClr, schemeClr…) dentro de solidFill, fillRef, fontRef, etc."""
    if element is None:
        return None
    return next((child for child in element if _localname(child) in _COLOR_NAMES), None)


def _pptx_slide_background(slide, theme: _PptxTheme, mapping: dict) -> RGB | None:
    """Fondo de la diapositiva, del diseño o del patrón (leído del XML, sin modificarlo)."""
    for part in (slide, slide.slide_layout, slide.slide_layout.slide_master):
        bg = part._element.find(p_qn("p:cSld")).find(p_qn("p:bg"))
        if bg is None:
            continue
        bg_pr = bg.find(p_qn("p:bgPr"))
        if bg_pr is not None:
            defined, color = theme.fill(bg_pr, mapping)
            return color if defined else None
        bg_ref = bg.find(p_qn("p:bgRef"))
        if bg_ref is not None:
            defined, color = theme.style_fill(bg_ref, mapping)
            return color if defined else None
        return None
    return None


def _placeholder_chain(shape) -> list:
    """El marcador de posición de la diapositiva, el del diseño y el del patrón (los que existan)."""
    chain = [shape]
    current = shape
    for _ in range(2):
        try:
            current = current._base_placeholder
        except (AttributeError, KeyError, ValueError):
            current = None
        if current is None:
            break
        chain.append(current)
    return chain


def _placeholder_type(shape) -> str | None:
    ph = shape._element.find(f".//{p_qn('p:nvPr')}/{p_qn('p:ph')}")
    return ph.get("type") if ph is not None else None


def _is_placeholder(shape) -> bool:
    return shape._element.find(f".//{p_qn('p:nvPr')}/{p_qn('p:ph')}") is not None


def _shape_fill(shape, theme: _PptxTheme, mapping: dict) -> tuple[bool, RGB | None]:
    """Relleno propio, heredado del marcador de posición o del estilo de la forma (p:style fillRef)."""
    chain = _placeholder_chain(shape) if _is_placeholder(shape) else [shape]
    for item in chain:
        defined, color = theme.fill(item._element.spPr, mapping)
        if defined:
            return defined, color
        if item._element.spPr is not None and item._element.spPr.find(p_qn("a:noFill")) is not None:
            return False, None
    style = shape._element.find(p_qn("p:style"))
    fill_ref = style.find(p_qn("a:fillRef")) if style is not None else None
    if fill_ref is not None:
        return theme.style_fill(fill_ref, mapping)
    return False, None


def _level_color(list_style, level: int, theme: _PptxTheme, mapping: dict) -> tuple[bool, RGB | None]:
    """Color del nivel de una lista de estilos (lstStyle, txStyles o defaultTextStyle)."""
    if list_style is None:
        return False, None
    lvl = list_style.find(p_qn(f"a:lvl{level + 1}pPr"))
    def_rpr = lvl.find(p_qn("a:defRPr")) if lvl is not None else None
    solid = def_rpr.find(p_qn("a:solidFill")) if def_rpr is not None else None
    if solid is None:
        return False, None
    return True, theme.resolve(_first_color(solid), mapping)


class _PptxTextDefaults:
    """Color por defecto del texto de una forma, según la cadena de herencia de PowerPoint."""

    def __init__(self, presentation, theme: _PptxTheme, master, mapping: dict) -> None:
        self.theme = theme
        self.mapping = mapping
        self.default_style = presentation._element.find(p_qn("p:defaultTextStyle"))
        tx_styles = master._element.find(p_qn("p:txStyles"))
        self.title_style = tx_styles.find(p_qn("p:titleStyle")) if tx_styles is not None else None
        self.body_style = tx_styles.find(p_qn("p:bodyStyle")) if tx_styles is not None else None
        self.other_style = tx_styles.find(p_qn("p:otherStyle")) if tx_styles is not None else None

    def color(self, shape, text_frame, level: int) -> RGB | None:
        sources = [text_frame._txBody.find(p_qn("a:lstStyle"))]
        if _is_placeholder(shape):
            for base in _placeholder_chain(shape)[1:]:
                tx_body = base._element.find(p_qn("p:txBody"))
                sources.append(tx_body.find(p_qn("a:lstStyle")) if tx_body is not None else None)
            ph_type = _placeholder_type(shape)
            sources.append(
                self.title_style if ph_type in _TITLE_PLACEHOLDERS
                else self.body_style if ph_type in _BODY_PLACEHOLDERS
                else self.other_style
            )
        for source in sources:
            defined, color = _level_color(source, level, self.theme, self.mapping)
            if defined:
                return color
        if not _is_placeholder(shape):
            style = shape._element.find(p_qn("p:style"))
            font_ref = style.find(p_qn("a:fontRef")) if style is not None else None
            if font_ref is not None and _first_color(font_ref) is not None:
                return self.theme.resolve(_first_color(font_ref), self.mapping)
            defined, color = _level_color(self.default_style, level, self.theme, self.mapping)
            if defined:
                return color
        return None


def _run_color(run, paragraph, theme: _PptxTheme, mapping: dict, default: RGB | None) -> RGB | None:
    """Color del run, del párrafo, de enlace o el heredado. None si no se puede resolver."""
    for props in (run._r.rPr, paragraph._p.pPr.find(p_qn("a:defRPr")) if paragraph._p.pPr is not None else None):
        if props is None:
            continue
        solid = props.find(p_qn("a:solidFill"))
        if solid is not None:
            return theme.resolve(_first_color(solid), mapping)
        if any(props.find(p_qn(t)) is not None for t in ("a:gradFill", "a:blipFill", "a:pattFill")):
            return None
    r_pr = run._r.rPr
    if r_pr is not None and r_pr.find(p_qn("a:hlinkClick")) is not None:
        return theme.resolve(etree.fromstring(f'<a:schemeClr xmlns:a="{_A_NS["a"]}" val="hlink"/>'), mapping)
    return default


def _pptx_text_frames(shapes, theme: _PptxTheme, mapping: dict, slide_background: RGB | None):
    """(forma, marco de texto, fondo) de formas, grupos y celdas de tabla."""
    for shape in shapes:
        if isinstance(shape, GroupShape):
            yield from _pptx_text_frames(shape.shapes, theme, mapping, slide_background)
        elif getattr(shape, "has_table", False):
            for row in shape.table.rows:
                for cell in row.cells:
                    # Sin relleno propio, el de la celda sale del estilo de tabla: no se evalúa.
                    defined, fill = theme.fill(cell._tc.tcPr, mapping)
                    yield shape, cell.text_frame, fill if defined else None
        elif getattr(shape, "has_text_frame", False) and shape.has_text_frame:
            defined, fill = _shape_fill(shape, theme, mapping)
            yield shape, shape.text_frame, fill if defined else slide_background


@_never_fails
def extract_pptx_legibility(presentation) -> LegibilityStats:
    """presentation es un pptx.Presentation ya abierto. Se saltean las diapositivas ocultas."""
    collector = _Collector()
    themes: dict[int, _PptxTheme] = {}
    for number, slide in enumerate(presentation.slides, start=1):
        if slide._element.get("show") in ("0", "false"):
            continue
        layout = slide.slide_layout
        master = layout.slide_master
        theme = themes.setdefault(id(master), _PptxTheme(master))
        mapping = _PptxTheme.color_map(theme.master_map, layout, slide)
        defaults = _PptxTextDefaults(presentation, theme, master, mapping)
        background = _pptx_slide_background(slide, theme, mapping)
        for shape, text_frame, fill in _pptx_text_frames(slide.shapes, theme, mapping, background):
            scale = _pptx_font_scale(text_frame)
            for paragraph in text_frame.paragraphs:
                default_color = defaults.color(shape, text_frame, paragraph.level)
                fragments = []
                for run in paragraph.runs:
                    size = run.font.size or paragraph.font.size
                    bold = run.font.bold if run.font.bold is not None else bool(paragraph.font.bold)
                    fragments.append(Fragment(
                        text=run.text,
                        size=size.pt * scale if size is not None else None,
                        color=_run_color(run, paragraph, theme, mapping, default_color),
                        background=fill,
                        bold=bold,
                    ))
                collector.add_paragraph(fragments, number)
    return collector.result()
