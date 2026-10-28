"""Tipografía poco legible (issue #79, parte 3). Los archivos se generan en el test."""

import io

import pymupdf
import pytest
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING
from docx.oxml import parse_xml
from docx.shared import Pt
from pptx import Presentation
from pptx.enum.text import PP_ALIGN
from pptx.opc.constants import RELATIONSHIP_TYPE as PPTX_RT
from pptx.util import Inches
from pptx.util import Pt as PptPt

from app.schemas.document import ExtractedDocument, LegibilityStats, TypographyIssue
from app.services import rules_service as rs
from app.services.document_service import document_service
from app.services.legibility_rules import (
    DECORATIVE_BODY_POINTS,
    MAX_TYPOGRAPHY_PENALTY,
    TYPOGRAPHY_POINTS,
)
from app.services.legibility_typography import is_decorative_font
from app.services.office_service import office_service

TEXT = (
    "Los ecosistemas acuáticos reúnen seres vivos que dependen del agua para vivir. "
    "En esta actividad vamos a observar cómo se relacionan entre sí y con su ambiente. "
)  # 28 palabras: los párrafos de justificado e interlineado usan TEXT * 2 (30 o más)
SHORT = "Leé con atención antes de empezar."


_ISSUE_PREFIXES = {
    "Pasajes en una fuente decorativa": "fuente_decorativa",
    "La fuente principal del texto es decorativa": "fuente_decorativa",
    "Pasajes largos en cursiva": "cursiva",
    "Texto largo en MAYÚSCULAS": "mayusculas",
    "Texto subrayado": "subrayado",
    "Texto justificado": "justificado",
    "Interlineado menor": "interlineado",
}


def _typography(result: rs.RulesResult) -> dict[str, dict]:
    """Barreras de tipografía por regla, en el orden en que aparecen."""
    found = {}
    for finding in result.findings:
        for prefix, rule in _ISSUE_PREFIXES.items():
            if finding.barrier["issue"].startswith(prefix):
                found[rule] = finding.barrier
    return found


def _penalty(result: rs.RulesResult, rule: str):
    return next((p for p in result.penalties if p.rule == f"tipografia_{rule}"), None)


# --- Generadores -----------------------------------------------------------


def _pdf(*boxes, links: tuple = (), lines: tuple = ()) -> rs.RulesResult:
    """boxes: (texto, fuente base14). lines: (x0, y, x1) dibujadas. links: rectángulos de enlace."""
    doc = pymupdf.open()
    page = doc.new_page()
    y = 50
    for text, font in boxes:
        page.insert_textbox(pymupdf.Rect(50, y, 550, y + 120), text, fontsize=11, fontname=font)
        y += 140
    for x0, ly, x1 in lines:
        page.draw_line((x0, ly), (x1, ly), width=0.7)
    for rect in links:
        page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(*rect), "uri": "https://example.org"})
    data = doc.tobytes()
    doc.close()
    return rs.run_rules(document_service.process_pdf(data, "material.pdf"))


def _underlined_pdf(text: str, link: bool = False) -> rs.RulesResult:
    """Una línea de texto con una raya dibujada justo debajo de la base (como subraya un PDF)."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 550, 300), TEXT * 3, fontsize=11)
    page.insert_text((50, 340), text, fontsize=11)
    width = pymupdf.get_text_length(text, fontsize=11)
    page.draw_line((50, 341.5), (50 + width, 341.5), width=0.7)
    if link:
        page.insert_link({"kind": pymupdf.LINK_URI, "from": pymupdf.Rect(48, 328, 52 + width, 344),
                          "uri": "https://example.org"})
    data = doc.tobytes()
    doc.close()
    return rs.run_rules(document_service.process_pdf(data, "material.pdf"))


def _docx(build, *, spacing: float | None = 1.5) -> rs.RulesResult:
    """build(document) agrega el contenido. spacing: interlineado del estilo Normal (None = el de la plantilla)."""
    document = Document()
    if spacing is not None:
        document.styles["Normal"].paragraph_format.line_spacing = spacing
    build(document)
    buffer = io.BytesIO()
    document.save(buffer)
    return rs.run_rules(office_service.process_docx(buffer.getvalue(), "material.docx"))


def _run(document, text: str, **font):
    run = document.add_paragraph().add_run(text)
    for name, value in font.items():
        setattr(run.font, name, value)
    return run


def _pptx(build, *, line_spacing: float | None = 1.5, minor_font: str | None = None) -> rs.RulesResult:
    """build(slide) agrega cuadros con _box. line_spacing: el de cada párrafo (None = heredado)."""
    presentation = Presentation()
    if minor_font:
        theme_part = presentation.slide_masters[0].part.part_related_by(PPTX_RT.THEME)
        theme_part._blob = theme_part.blob.replace(b'<a:minorFont><a:latin typeface="Calibri"',
                                                   f'<a:minorFont><a:latin typeface="{minor_font}"'.encode())
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    build(slide, line_spacing)
    buffer = io.BytesIO()
    presentation.save(buffer)
    return rs.run_rules(office_service.process_pptx(buffer.getvalue(), "material.pptx"))


def _box(slide, line_spacing, text: str, top: float = 0.3, **font):
    frame = slide.shapes.add_textbox(Inches(0.5), Inches(top), Inches(9), Inches(1.5)).text_frame
    paragraph = frame.paragraphs[0]
    run = paragraph.add_run()
    run.text = text
    run.font.size = PptPt(18)
    for name, value in font.items():
        setattr(run.font, name, value)
    if line_spacing is not None:
        paragraph.line_spacing = line_spacing
    return paragraph, run


# --- Fuentes decorativas -----------------------------------------------------


@pytest.mark.parametrize("name, expected", [
    ("Brush Script MT", True),
    ("ABCDEF+BrushScriptMT-Italic", True),  # así aparece en un PDF
    ("Lucida Handwriting", True),
    ("Papyrus", True),
    ("Segoe Script", True),
    ("Arial", False),
    ("Calibri", False),
    ("Times New Roman", False),
    ("Comic Sans MS", False),  # la recomienda la British Dyslexia Association
    ("", False),
    (None, False),
])
def test_fuentes_decorativas(name, expected):
    assert is_decorative_font(name) is expected


# --- PDF --------------------------------------------------------------------


def test_pdf_pasaje_largo_en_cursiva():
    result = _pdf((TEXT * 2, "Times-Italic"))
    found = _typography(result)
    assert list(found) == ["cursiva"]
    assert found["cursiva"]["severity"] == "para_revisar"
    assert "(página 1)" in found["cursiva"]["issue"]
    assert "negrita" in found["cursiva"]["recommendation"]
    assert found["cursiva"]["fragment_found"] is True


def test_pdf_mayusculas_sostenidas():
    found = _typography(_pdf((TEXT.upper() * 2, "helv")))
    assert list(found) == ["mayusculas"]


def test_pdf_siglas_y_titulos_cortos_en_mayusculas_no_se_marcan():
    with_acronyms = "El ADN y el ARN están en todas las células. La ONU y la OMS publican informes. " * 3
    result = _pdf(("UNIDAD 3: LOS ECOSISTEMAS ACUÁTICOS", "helv"), (with_acronyms, "helv"))
    assert "mayusculas" not in _typography(result)


def test_pdf_subrayado_dibujado_debajo_del_texto():
    found = _typography(_underlined_pdf("Leé con mucha atención todas las indicaciones antes de empezar."))
    assert list(found) == ["subrayado"]


def test_pdf_un_enlace_subrayado_no_se_marca():
    found = _typography(_underlined_pdf("Consultá la guía completa en el sitio del ministerio.", link=True))
    assert found == {}


def test_pdf_bordes_de_tabla_no_cuentan_como_subrayado():
    # Celda con borde inferior más ancho que el texto, varios puntos por debajo de la base.
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((60, 100), "Especie observada en la muestra del arroyo", fontsize=11)
    page.draw_rect(pymupdf.Rect(50, 85, 550, 106), width=0.7)
    data = doc.tobytes()
    doc.close()
    assert _typography(rs.run_rules(document_service.process_pdf(data, "material.pdf"))) == {}


def test_pdf_texto_normal_no_genera_falsos_positivos():
    # En PDF no se evalúan justificado ni interlineado (el archivo no los guarda).
    assert _typography(_pdf((TEXT * 3, "helv"), (TEXT * 2, "tiro"))) == {}


# --- Word -------------------------------------------------------------------


def test_word_cursiva_en_el_run_y_heredada_del_estilo():
    def build(document):
        style = document.styles.add_style("Cita larga", WD_STYLE_TYPE.PARAGRAPH)
        style.font.italic = True
        document.add_paragraph(TEXT, style="Cita larga")
        _run(document, TEXT, italic=True)

    found = _typography(_docx(build))
    assert list(found) == ["cursiva"]
    assert "2 párrafo(s)" in found["cursiva"]["explanation"]
    assert "página" not in found["cursiva"]["issue"]  # Word no informa páginas


def test_word_formato_todo_mayusculas():
    # El texto está en minúsculas pero se ve en mayúsculas: la cita es el texto original.
    found = _typography(_docx(lambda d: _run(d, TEXT, all_caps=True)))
    assert list(found) == ["mayusculas"]
    assert found["mayusculas"]["original_text"].startswith("Los ecosistemas")
    assert found["mayusculas"]["fragment_found"] is True


def test_word_subrayado_y_enlace():
    def build(document):
        paragraph = document.add_paragraph()
        paragraph.add_run("Leé con mucha atención todas las indicaciones.").font.underline = True
        link = document.add_paragraph().add_run("Consultá la guía completa en el sitio del ministerio.")
        link.style = document.styles.add_style("Hyperlink", WD_STYLE_TYPE.CHARACTER)
        link.font.underline = True

    found = _typography(_docx(build))
    assert list(found) == ["subrayado"]
    assert "1 párrafo(s)" in found["subrayado"]["explanation"]


def test_word_subrayado_corto_no_se_marca():
    def build(document):
        paragraph = document.add_paragraph()
        paragraph.add_run("Importante:").font.underline = True
        paragraph.add_run(" " + TEXT)

    assert _typography(_docx(build)) == {}


def test_word_pasajes_en_fuente_decorativa_y_cuerpo_normal():
    def build(document):
        for _ in range(3):
            document.add_paragraph(TEXT * 2)
        _run(document, TEXT, name="Brush Script MT")

    result = _docx(build)
    found = _typography(result)
    assert list(found) == ["fuente_decorativa"]
    assert found["fuente_decorativa"]["severity"] == "para_revisar"
    assert "Brush Script MT" in found["fuente_decorativa"]["issue"]
    assert _penalty(result, "fuente_decorativa").points == TYPOGRAPHY_POINTS


def test_word_cuerpo_en_fuente_decorativa_es_barrera():
    def build(document):
        document.styles["Normal"].font.name = "Lucida Handwriting"
        document.add_paragraph(TEXT * 2)

    result = _docx(build)
    barrier = _typography(result)["fuente_decorativa"]
    assert barrier["severity"] == "barrera_detectada"
    assert barrier["issue"].startswith("La fuente principal del texto es decorativa")
    assert _penalty(result, "fuente_decorativa").points == DECORATIVE_BODY_POINTS


def test_word_comic_sans_no_se_marca():
    assert _typography(_docx(lambda d: _run(d, TEXT * 2, name="Comic Sans MS"))) == {}


def test_word_justificado_solo_en_parrafos_largos():
    def build(document):
        document.add_paragraph(TEXT * 2).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        document.add_paragraph(SHORT).alignment = WD_ALIGN_PARAGRAPH.JUSTIFY

    found = _typography(_docx(build))
    assert list(found) == ["justificado"]
    assert "1 párrafo(s)" in found["justificado"]["explanation"]
    assert "izquierda" in found["justificado"]["recommendation"]


@pytest.mark.parametrize("spacing, flagged, detail", [
    (1.5, False, None),
    (2.0, False, None),
    (1.0, True, "1"),
    (None, True, "1,15"),  # la plantilla de python-docx trae 1,15
])
def test_word_interlineado(spacing, flagged, detail):
    found = _typography(_docx(lambda d: d.add_paragraph(TEXT * 2), spacing=spacing))
    assert ("interlineado" in found) is flagged
    if flagged:
        assert f"interlineado de {detail}" in found["interlineado"]["explanation"]
        assert "1,5" in found["interlineado"]["recommendation"]


def test_word_interlineado_exacto_en_puntos():
    def build(document):
        paragraph = document.add_paragraph()
        paragraph.add_run(TEXT * 2).font.size = Pt(12)
        paragraph.paragraph_format.line_spacing_rule = WD_LINE_SPACING.EXACTLY
        paragraph.paragraph_format.line_spacing = Pt(13)  # 13 / 12 = 1,08

    found = _typography(_docx(build))
    assert "interlineado de 1,08" in found["interlineado"]["explanation"]


def test_word_interlineado_en_parrafos_cortos_no_se_marca():
    assert _typography(_docx(lambda d: d.add_paragraph(SHORT), spacing=1.0)) == {}


def test_word_material_bien_formateado_no_genera_falsos_positivos():
    def build(document):
        document.add_paragraph(TEXT * 2)
        paragraph = document.add_paragraph()
        paragraph.add_run("Importante: ").bold = True
        paragraph.add_run(TEXT)
        paragraph.add_run(" Escherichia coli").italic = True  # nombre científico, corto

    result = _docx(build)
    assert _typography(result) == {}
    assert result.metrics.typography_issues == 0


# --- PowerPoint -------------------------------------------------------------


def test_powerpoint_cursiva_mayusculas_y_subrayado():
    def build(slide, spacing):
        _box(slide, spacing, TEXT, top=0.3, italic=True)
        _box(slide, spacing, TEXT.upper(), top=2.0)
        _, run = _box(slide, spacing, "Leé con mucha atención todas las indicaciones.", top=3.7)
        run.font.underline = True

    found = _typography(_pptx(build))
    assert list(found) == ["cursiva", "mayusculas", "subrayado"]
    assert "(diapositiva 1)" in found["cursiva"]["issue"]


def test_powerpoint_formato_todo_mayusculas():
    def build(slide, spacing):
        _, run = _box(slide, spacing, TEXT)
        run._r.get_or_add_rPr().set("cap", "all")

    assert list(_typography(_pptx(build))) == ["mayusculas"]


def test_powerpoint_enlace_subrayado_no_se_marca():
    def build(slide, spacing):
        _, run = _box(slide, spacing, "Consultá la guía completa en el sitio del ministerio.")
        run.font.underline = True
        run.hyperlink.address = "https://example.org"

    assert _typography(_pptx(build)) == {}


def test_powerpoint_fuente_del_tema_decorativa():
    # La plantilla usa una fuente decorativa como fuente de cuerpo del tema (+mn-lt).
    result = _pptx(lambda slide, spacing: _box(slide, spacing, TEXT * 2), minor_font="Papyrus")
    barrier = _typography(result)["fuente_decorativa"]
    assert barrier["severity"] == "barrera_detectada"
    assert "Papyrus" in barrier["issue"]


def test_powerpoint_justificado_e_interlineado_heredado():
    def build(slide, spacing):
        paragraph, _ = _box(slide, spacing, TEXT * 2)
        paragraph.alignment = PP_ALIGN.JUSTIFY

    found = _typography(_pptx(build, line_spacing=None))  # sin interlineado propio: sencillo
    assert list(found) == ["justificado", "interlineado"]
    assert "interlineado de 1" in found["interlineado"]["explanation"]


def test_powerpoint_reducir_texto_al_desbordar_achica_el_interlineado():
    def build(slide, spacing):
        paragraph, _ = _box(slide, spacing, TEXT * 2)
        body_pr = paragraph._p.getparent().find(
            "{http://schemas.openxmlformats.org/drawingml/2006/main}bodyPr"
        )
        body_pr.append(parse_xml(
            '<a:normAutofit xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
            'fontScale="100000" lnSpcReduction="20000"/>'
        ))

    found = _typography(_pptx(build))  # 1,5 reducido un 20% = 1,2
    assert "interlineado de 1,2" in found["interlineado"]["explanation"]


def test_powerpoint_material_bien_formateado_no_genera_falsos_positivos():
    def build(slide, spacing):
        _box(slide, spacing, TEXT * 2, top=0.3)  # largo, alineado a la izquierda, interlineado 1,5
        _box(slide, spacing, "UNIDAD 3", top=2.0)
        _box(slide, spacing, SHORT, top=3.7, italic=True)

    assert _typography(_pptx(build)) == {}


# --- Agregado, puntaje y casos que no se evalúan --------------------------------


def test_una_barrera_por_regla_con_la_cantidad_de_parrafos():
    def build(document):
        for _ in range(4):
            _run(document, TEXT, italic=True)

    result = _docx(build)
    found = _typography(result)
    assert list(found) == ["cursiva"]
    assert "4 párrafo(s)" in found["cursiva"]["explanation"]
    assert _penalty(result, "cursiva").count == 4


def test_penalizacion_de_tipografia_tiene_tope():
    def build(document):
        document.styles["Normal"].font.name = "Lucida Handwriting"
        paragraph = document.add_paragraph()
        paragraph.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        run = paragraph.add_run(TEXT * 2)
        run.italic = True
        run.underline = True
        run.font.all_caps = True

    result = _docx(build, spacing=1.0)
    penalties = [p for p in result.penalties if p.rule.startswith("tipografia_")]
    assert len(_typography(result)) == 6  # las 6 barreras se muestran
    assert sum(p.points for p in penalties) == MAX_TYPOGRAPHY_PENALTY
    assert all(p.points > 0 for p in penalties)  # las que superan el tope no figuran con 0
    assert result.metrics.typography_issues == 6
    comment = next(d for d in result.dimensions if d.category == "accesibilidad_visual").comment
    assert "Tipografía poco legible" in comment


def test_texto_pegado_no_se_evalua_por_tipografia():
    text = (TEXT * 2).upper()
    doc = ExtractedDocument(
        source_type="text", raw_text=text, text_for_analysis=text,
        word_count=rs.count_words(text), character_count=len(text),
        legibility=LegibilityStats(typography_issues=[
            TypographyIssue(rule="mayusculas", passages=1, word_count=60, example=text[:40]),
        ]),
    )
    result = rs.run_rules(doc)
    assert _typography(result) == {}
    assert result.metrics.typography_issues == 0


def test_pdf_escaneado_no_se_evalua_por_tipografia():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 550, 300), TEXT * 3, fontsize=11, fontname="Times-Italic")
    extracted = document_service.process_pdf(doc.tobytes(), "material.pdf").model_copy(
        update={"is_scanned_pdf": True}
    )
    assert _typography(rs.run_rules(extracted)) == {}


def test_texto_invisible_del_pdf_no_se_evalua_por_tipografia():
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 550, 300), TEXT.upper() * 3, fontsize=11,
                        fontname="Times-Italic", render_mode=3)
    extracted = document_service.process_pdf(doc.tobytes(), "material.pdf")
    assert extracted.legibility.typography_issues == []
