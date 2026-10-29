"""
Plantillas de la infografía (Issue #119): dibujan el JSON de contenido como un SVG accesible.

Propuesta inicial para que la ajuste el equipo de diseño (FigJam, Design System Obelisco): la
paleta, los tamaños y los íconos están arriba, separados del armado.

- Texto real (<text>), en orden de lectura: título, bajada, secciones en orden y cierre.
- role="img" con <title> y <desc>: el lector de pantalla lee la infografía completa desde <desc>.
- Íconos y adornos con aria-hidden: son decorativos, el título de cada sección lleva el significado.
- Contraste AA en todos los pares de texto y fondo (lo verifican los tests).
- El texto se corta en líneas con las métricas de Helvetica de PyMuPDF, la misma fuente con la que
  se exporta a PNG y PDF (en el navegador se ve con Arial, de métricas equivalentes).
"""
from dataclasses import dataclass, field
from xml.sax.saxutils import escape

import pymupdf

from app.schemas.transformation import InfographicResult, InfographicSection

WIDTH = 1080
MARGIN = 56
GAP = 24
CARD_PADDING = 28
LEAD = 72  # círculo del ícono o del número de paso
FONT_FAMILY = "Helvetica, Arial, sans-serif"

PALETTE = {
    "background": "#FFFFFF",
    "header": "#0B4F8A",
    "header_text": "#FFFFFF",
    "header_subtitle": "#E3EEF9",
    "card": "#F3F7FB",
    "card_border": "#C9D9EA",
    "section_title": "#0B3A66",
    "text": "#1F2933",
    "highlight": "#DCEAF8",
    "highlight_text": "#0B3A66",
    "lead": "#0B4F8A",
    "lead_text": "#FFFFFF",
    "connector": "#9DB8D6",
    "closing": "#FFF4D6",
    "closing_text": "#3D2E00",
}

# (fuente de PyMuPDF para medir, tamaño, alto de línea, negrita)
STYLES = {
    "title": ("hebo", 46, 56, True),
    "subtitle": ("helv", 26, 36, False),
    "section_title": ("hebo", 30, 38, True),
    "highlight": ("hebo", 30, 38, True),
    "text": ("helv", 25, 35, False),
    "closing": ("hebo", 25, 35, True),
    "step": ("hebo", 34, 34, True),
}

# Íconos de 24 × 24 con trazo (dibujos simples propios). La lista es la de InfographicIcon.
ICONS = {
    "idea": '<circle cx="12" cy="10" r="6"/><path d="M9.5 18h5M10.5 21h3M10 15.5v2.5M14 15.5v2.5"/>',
    "libro": '<path d="M3 5h7a2 2 0 0 1 2 2v13a2 2 0 0 0-2-2H3zM21 5h-7a2 2 0 0 0-2 2v13a2 2 0 0 1 2-2h7z"/>',
    "pregunta": '<circle cx="12" cy="12" r="9"/><path d="M9.5 9.5a2.5 2.5 0 1 1 3.6 2.2c-.7.4-1.1.9-1.1 1.6v.7M12 17.2v.1"/>',
    "alerta": '<path d="M12 3 22 20H2z"/><path d="M12 10v4.5M12 17.2v.1"/>',
    "check": '<circle cx="12" cy="12" r="9"/><path d="m8 12.5 2.8 2.8L16.5 9"/>',
    "reloj": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>',
    "grafico": '<path d="M3 21h18M6.5 17v-5M12 17V4.5M17.5 17V9"/>',
    "persona": '<circle cx="12" cy="8" r="4"/><path d="M4.5 20.5c0-4 3.4-6.5 7.5-6.5s7.5 2.5 7.5 6.5"/>',
    "grupo": '<circle cx="8.5" cy="9" r="3"/><circle cx="16" cy="8" r="2.5"/>'
             '<path d="M2.5 19.5c0-3.2 2.7-5.5 6-5.5s6 2.3 6 5.5M15 13.5c3 0 6.5 1.5 6.5 5"/>',
    "planeta": '<circle cx="12" cy="12" r="6.5"/><ellipse cx="12" cy="12" rx="11" ry="3.5" transform="rotate(-20 12 12)"/>',
    "ciencia": '<path d="M9 3h6M10 3v6.5L4.8 18.2A1.8 1.8 0 0 0 6.4 21h11.2a1.8 1.8 0 0 0 1.6-2.8L14 9.5V3M7.5 15h9"/>',
    "calculo": '<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8.5 11.5h.1M12 11.5h.1M15.5 11.5h.1'
               'M8.5 15h.1M12 15h.1M15.5 15h.1M8.5 18h.1M12 18h3.5"/>',
    "lugar": '<path d="M12 21s7-6.2 7-11.5a7 7 0 0 0-14 0C5 14.8 12 21 12 21z"/><circle cx="12" cy="9.5" r="2.5"/>',
    "naturaleza": '<path d="M5 19C5 10.5 11 5 19.5 4.5 19.5 13 14 19 5 19zM5 19l8-8"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.8v.1"/>',
}


@dataclass
class _Canvas:
    parts: list[str] = field(default_factory=list)

    def add(self, element: str) -> None:
        self.parts.append(element)


def _attr(value: str) -> str:
    return escape(value, {'"': "&quot;"})


# MuPDF dibuja el SVG con Nimbus Sans, un 7,7 % más ancho que las métricas de Helvetica (medido al
# extraer el texto del PDF exportado): se mide con ese margen para que nada se salga de su recuadro
RENDER_WIDTH_FACTOR = 1.08


def text_width(text: str, style: str) -> float:
    font, size, _, _ = STYLES[style]
    return pymupdf.get_text_length(text, fontname=font, fontsize=size) * RENDER_WIDTH_FACTOR


def wrap(text: str, style: str, width: float) -> list[str]:
    """Líneas de `text` que entran en `width` con el estilo dado (las palabras muy largas se cortan)."""
    def fits(s: str) -> bool:
        return text_width(s, style) <= width

    lines: list[str] = []
    current = ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if fits(candidate):
            current = candidate
            continue
        if current:
            lines.append(current)
        while not fits(word):  # una palabra más ancha que la línea
            cut = len(word) - 1
            while cut > 1 and not fits(word[:cut] + "-"):
                cut -= 1
            lines.append(word[:cut] + "-")
            word = word[cut:]
        current = word
    if current:
        lines.append(current)
    return lines or [""]


def _text(canvas: _Canvas, lines: list[str], style: str, x: float, y: float, color: str, anchor: str = "start") -> float:
    """Escribe las líneas desde `y` (borde superior) y devuelve el alto ocupado."""
    _, size, line_height, bold = STYLES[style]
    weight = ' font-weight="bold"' if bold else ""
    first_baseline = y + size * 0.85 + (line_height - size) / 2
    spans = "".join(
        f'<tspan x="{x:.1f}" y="{first_baseline + i * line_height:.1f}">{escape(line)}</tspan>'
        for i, line in enumerate(lines)
    )
    canvas.add(f'<text font-family="{FONT_FAMILY}" font-size="{size}"{weight} fill="{color}" text-anchor="{anchor}">{spans}</text>')
    return len(lines) * line_height


def _height(lines: list[str], style: str) -> float:
    return len(lines) * STYLES[style][2]


def _icon(canvas: _Canvas, name: str | None, cx: float, cy: float, diameter: float) -> None:
    canvas.add(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{diameter / 2:.1f}" fill="{PALETTE["lead"]}" aria-hidden="true"/>')
    paths = ICONS.get(name or "info", ICONS["info"])
    scale = diameter * 0.55 / 24
    canvas.add(
        f'<g aria-hidden="true" transform="translate({cx - 12 * scale:.1f} {cy - 12 * scale:.1f}) scale({scale:.3f})" '
        f'fill="none" stroke="{PALETTE["lead_text"]}" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        f"{paths}</g>"
    )


def _step(canvas: _Canvas, number: int, cx: float, cy: float) -> None:
    canvas.add(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{LEAD / 2:.1f}" fill="{PALETTE["lead"]}" aria-hidden="true"/>')
    _, size, _, _ = STYLES["step"]
    canvas.add(
        f'<text font-family="{FONT_FAMILY}" font-size="{size}" font-weight="bold" fill="{PALETTE["lead_text"]}" '
        f'text-anchor="middle" x="{cx:.1f}" y="{cy + size * 0.35:.1f}">{number}</text>'
    )


@dataclass
class _Body:
    """Título, dato destacado y texto de una sección, ya cortados en líneas para un ancho."""

    title: list[str]
    highlight: list[str]
    text: list[str]

    @classmethod
    def of(cls, s: InfographicSection, width: float) -> "_Body":
        return cls(
            title=wrap(s.title, "section_title", width),
            highlight=wrap(s.highlight, "highlight", width - 32) if s.highlight else [],
            text=wrap(s.text, "text", width),
        )

    def height(self) -> float:
        h = _height(self.title, "section_title") + 10 + _height(self.text, "text")
        if self.highlight:
            h += _height(self.highlight, "highlight") + 16 + 12
        return h

    def draw(self, canvas: _Canvas, x: float, y: float) -> None:
        y += _text(canvas, self.title, "section_title", x, y, PALETTE["section_title"]) + 10
        if self.highlight:
            widest = max(text_width(line, "highlight") for line in self.highlight)
            h = _height(self.highlight, "highlight") + 16
            canvas.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{widest + 32:.1f}" height="{h:.1f}" rx="10" '
                       f'fill="{PALETTE["highlight"]}" aria-hidden="true"/>')
            _text(canvas, self.highlight, "highlight", x + 16, y + 8, PALETTE["highlight_text"])
            y += h + 12
        _text(canvas, self.text, "text", x, y, PALETTE["text"])


def _card(canvas: _Canvas, x: float, y: float, w: float, h: float) -> None:
    canvas.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="18" fill="{PALETTE["card"]}" '
               f'stroke="{PALETTE["card_border"]}" stroke-width="2" aria-hidden="true"/>')


def _header(canvas: _Canvas, result: InfographicResult) -> float:
    inner = WIDTH - 2 * MARGIN
    title = wrap(result.title, "title", inner)
    subtitle = wrap(result.subtitle, "subtitle", inner) if result.subtitle else []
    height = 48 + _height(title, "title") + (12 + _height(subtitle, "subtitle") if subtitle else 0) + 48
    canvas.add(f'<rect x="0" y="0" width="{WIDTH}" height="{height:.1f}" fill="{PALETTE["header"]}" aria-hidden="true"/>')
    y = 48 + _text(canvas, title, "title", MARGIN, 48, PALETTE["header_text"])
    if subtitle:
        _text(canvas, subtitle, "subtitle", MARGIN, y + 12, PALETTE["header_subtitle"])
    return height


def _list_layout(canvas: _Canvas, result: InfographicResult, y: float, numbered: bool) -> float:
    """'vertical' (ícono a la izquierda) y 'pasos' (número a la izquierda, unidos por una línea)."""
    card_w = WIDTH - 2 * MARGIN
    body_x = MARGIN + CARD_PADDING + LEAD + 24
    body_w = MARGIN + card_w - CARD_PADDING - body_x
    lead_cx = MARGIN + CARD_PADDING + LEAD / 2
    for i, section in enumerate(result.sections, start=1):
        body = _Body.of(section, body_w)
        h = max(LEAD, body.height()) + 2 * CARD_PADDING
        if numbered and i > 1:  # la línea que une los pasos, en el espacio entre tarjetas
            canvas.add(f'<line x1="{lead_cx:.1f}" y1="{y - GAP:.1f}" x2="{lead_cx:.1f}" y2="{y:.1f}" '
                       f'stroke="{PALETTE["connector"]}" stroke-width="4" aria-hidden="true"/>')
        _card(canvas, MARGIN, y, card_w, h)
        if numbered:
            _step(canvas, i, lead_cx, y + CARD_PADDING + LEAD / 2)
        else:
            _icon(canvas, section.icon, lead_cx, y + CARD_PADDING + LEAD / 2, LEAD)
        body.draw(canvas, body_x, y + CARD_PADDING)
        y += h + GAP
    return y


def _grid_layout(canvas: _Canvas, result: InfographicResult, y: float) -> float:
    """'comparativa': dos columnas, con el ícono arriba; la última ocupa todo el ancho si quedó sola."""
    col_w = (WIDTH - 2 * MARGIN - GAP) / 2
    sections = result.sections
    for start in range(0, len(sections), 2):
        row = sections[start:start + 2]
        w = col_w if len(row) == 2 else WIDTH - 2 * MARGIN
        bodies = [_Body.of(s, w - 2 * CARD_PADDING) for s in row]
        h = 2 * CARD_PADDING + LEAD * 0.8 + 16 + max(b.height() for b in bodies)
        for j, (section, body) in enumerate(zip(row, bodies)):
            x = MARGIN + j * (col_w + GAP)
            _card(canvas, x, y, w, h)
            _icon(canvas, section.icon, x + CARD_PADDING + LEAD * 0.4, y + CARD_PADDING + LEAD * 0.4, LEAD * 0.8)
            body.draw(canvas, x + CARD_PADDING, y + CARD_PADDING + LEAD * 0.8 + 16)
        y += h + GAP
    return y


def _closing(canvas: _Canvas, text: str, y: float) -> float:
    w = WIDTH - 2 * MARGIN
    lines = wrap(text, "closing", w - 2 * CARD_PADDING)
    h = _height(lines, "closing") + 2 * CARD_PADDING
    canvas.add(f'<rect x="{MARGIN}" y="{y:.1f}" width="{w}" height="{h:.1f}" rx="18" fill="{PALETTE["closing"]}" aria-hidden="true"/>')
    _text(canvas, lines, "closing", MARGIN + CARD_PADDING, y + CARD_PADDING, PALETTE["closing_text"])
    return y + h + GAP


def plain_text(result: InfographicResult) -> str:
    """La infografía completa en texto, en orden de lectura: va en <desc> y en la versión en texto."""
    parts = [result.title]
    if result.subtitle:
        parts.append(result.subtitle)
    for i, s in enumerate(result.sections, start=1):
        label = f"Paso {i}" if result.layout == "pasos" else f"Sección {i}"
        parts.append(f"{label}: {s.title}." + (f" {s.highlight}." if s.highlight else "") + f" {s.text}")
    if result.closing:
        parts.append(result.closing)
    return "\n".join(parts)


def render_svg(result: InfographicResult) -> str:
    canvas = _Canvas()
    y = _header(canvas, result) + 40
    if result.layout == "comparativa":
        y = _grid_layout(canvas, result, y)
    else:
        y = _list_layout(canvas, result, y, numbered=result.layout == "pasos")
    if result.closing:
        y = _closing(canvas, result.closing, y + 8)
    height = round(y - GAP + MARGIN)
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{height}" viewBox="0 0 {WIDTH} {height}" '
        f'role="img" aria-labelledby="infografia-titulo infografia-descripcion" lang="es">'
        f'<title id="infografia-titulo">{escape(result.title)}</title>'
        f'<desc id="infografia-descripcion">{escape(plain_text(result))}</desc>'
        f'<rect width="{WIDTH}" height="{height}" fill="{PALETTE["background"]}" aria-hidden="true"/>'
        + "".join(canvas.parts)
        + "</svg>"
    )
