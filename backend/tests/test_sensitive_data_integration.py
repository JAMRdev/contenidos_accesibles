"""
Integración del detector de datos sensibles con analyze_document (Issue #66).

Corren el camino real (mock=False) reemplazando la IA con patch, igual que
test_alt_text_service.py: no consumen tokens ni necesitan API keys.
"""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.analysis import AccessibilityDiagnostic
from app.schemas.document import ExtractedDocument, ExtractedImage
from app.services.accessibility_service import analyze_document
from app.services.document_service import document_service
from app.services.llm_service import StructuredResult
from app.services.sensitive_data_service import SensitiveDataError, detect

ALT_TEXT_TARGET = "app.services.alt_text_service.alt_text_service.generate_proposals"
TEXT_LLM_TARGET = "app.services.accessibility_service.llm_service.generate_structured"
SAVE_TARGET = "app.services.accessibility_service.analysis_repository.save"

CLEAN_TEXT = (
    "Actividad 1: Leé el texto sobre el ciclo del agua. "
    "Después respondé las preguntas de la guía con tus palabras."
)


def _image() -> ExtractedImage:
    return ExtractedImage(
        image_index=1,
        page_number=1,
        bbox=(50.0, 100.0, 200.0, 250.0),
        width=150,
        height=150,
        location_description="Página 1, sector central",
        context_text="",
        image_bytes=b"\x89PNGfake",
    )


def _scanned_pdf() -> ExtractedDocument:
    """PDF escaneado: sin texto digital, todo el contenido es imagen."""
    return ExtractedDocument(
        source_type="pdf",
        filename="escaneado.pdf",
        raw_text="",
        text_for_analysis="",
        word_count=0,
        character_count=0,
        has_images=True,
        image_count=1,
        is_scanned_pdf=True,
        extracted_images=[_image()],
    )


def _digital_pdf(text: str = CLEAN_TEXT) -> ExtractedDocument:
    """PDF con texto digital e imágenes."""
    return ExtractedDocument(
        source_type="pdf",
        filename="digital.pdf",
        raw_text=text,
        text_for_analysis=text,
        word_count=len(text.split()),
        character_count=len(text),
        has_images=True,
        image_count=1,
        is_scanned_pdf=False,
        extracted_images=[_image()],
    )


def _text_llm_result() -> StructuredResult:
    return StructuredResult(
        parsed=AccessibilityDiagnostic(
            estimated_score=80, summary="Diagnóstico simulado.", dimensions=[], strengths=[], barriers=[]
        ),
        provider="groq",
        model="gpt-oss-120b",
        connection_label="Groq",
        latency_ms=1,
        usage=None,
    )


async def _analyze_with_fake_ai(doc, **kwargs):
    """Corre analyze_document en modo real con las dos IAs simuladas. Devuelve (respuesta, mocks)."""
    with patch(ALT_TEXT_TARGET, new_callable=AsyncMock, return_value=[]) as alt_text, \
         patch(TEXT_LLM_TARGET, new_callable=AsyncMock, return_value=_text_llm_result()) as text_llm:
        response = await analyze_document(doc, mock=False, persist=False, **kwargs)
    return response, alt_text, text_llm


# --- Datos sensibles en el texto ---------------------------------------------------


def test_datos_sensibles_bloquean_antes_de_ia_y_de_guardar():
    doc = _digital_pdf("Alumno: Juan Pérez\nDNI: 30.123.456\n" + CLEAN_TEXT)

    async def _run():
        with patch(ALT_TEXT_TARGET, new_callable=AsyncMock) as alt_text, \
             patch(TEXT_LLM_TARGET, new_callable=AsyncMock) as text_llm, \
             patch(SAVE_TARGET) as save:
            with pytest.raises(SensitiveDataError) as exc_info:
                await analyze_document(doc, mock=False, persist=True)
        assert alt_text.call_count == 0, "Las imágenes no deben llegar a la IA multimodal"
        assert text_llm.call_count == 0, "El texto no debe llegar a la IA"
        assert save.call_count == 0, "El material no debe guardarse en la base"
        assert len(exc_info.value.findings) == 2

    asyncio.run(_run())


def test_datos_sensibles_bloquean_tambien_en_modo_mock():
    doc = document_service.process_text("Estudiante: Ana Gómez\nNota final: 9\n" + CLEAN_TEXT)
    with patch(SAVE_TARGET) as save:
        with pytest.raises(SensitiveDataError):
            asyncio.run(analyze_document(doc, mock=True, persist=True))
    assert save.call_count == 0


def test_hallazgos_descartados_permiten_seguir_y_dejan_registro():
    text = "Alumno: Juan Pérez\n" + CLEAN_TEXT
    ids = [f.id for f in detect(text)]
    doc = document_service.process_text(text)

    response = asyncio.run(analyze_document(doc, mock=True, persist=False, dismissed_findings=ids))

    assert any("El docente indicó que 1 posible(s) dato(s)" in w for w in response.warnings)


def test_material_limpio_no_agrega_avisos_de_privacidad():
    response, alt_text, text_llm = asyncio.run(_analyze_with_fake_ai(_digital_pdf()))
    assert text_llm.call_count == 1
    assert not any("datos personales" in w or "dato(s) personal" in w for w in response.warnings)


# --- Imágenes de PDFs escaneados ----------------------------------------------------


def test_pdf_escaneado_no_envia_imagenes_sin_confirmacion():
    response, alt_text, _ = asyncio.run(_analyze_with_fake_ai(_scanned_pdf()))

    assert alt_text.call_count == 0
    assert response.alt_text_proposals == []
    assert any("el PDF es escaneado y no se pudo verificar" in w for w in response.warnings)


def test_pdf_escaneado_con_confirmacion_del_docente_envia_imagenes():
    response, alt_text, _ = asyncio.run(
        _analyze_with_fake_ai(_scanned_pdf(), confirm_images_without_personal_data=True)
    )

    assert alt_text.call_count == 1
    assert any("El docente confirmó que las imágenes" in w for w in response.warnings)
    assert not any("no se pudo verificar" in w for w in response.warnings)


def test_pdf_digital_con_imagenes_se_comporta_como_antes():
    response, alt_text, text_llm = asyncio.run(_analyze_with_fake_ai(_digital_pdf()))

    assert alt_text.call_count == 1
    assert text_llm.call_count == 1
    assert not any("PDF es escaneado" in w or "El docente confirmó" in w for w in response.warnings)

def test_pdf_con_poco_texto_digital_se_comporta_como_antes():
    """
    document_service marca is_scanned_pdf=True si hay menos de 25 palabras y una imagen por
    página. Si tiene alguna palabra digital, las imágenes se envían como antes (opción A).
    """
    doc = _digital_pdf("Ficha 1: observá la imagen.").model_copy(update={"is_scanned_pdf": True})
    response, alt_text, _ = asyncio.run(_analyze_with_fake_ai(doc))
 
    assert alt_text.call_count == 1
    assert not any("PDF es escaneado y no se pudo verificar" in w for w in response.warnings)
def test_confirmacion_no_cambia_nada_en_pdf_digital():
    response, alt_text, _ = asyncio.run(
        _analyze_with_fake_ai(_digital_pdf(), confirm_images_without_personal_data=True)
    )

    assert alt_text.call_count == 1
    assert not any("El docente confirmó" in w for w in response.warnings)


def test_modo_mock_no_cambia_con_pdf_escaneado():
    """El mock sigue generando propuestas simuladas como antes, sin avisos nuevos."""
    response = asyncio.run(analyze_document(_scanned_pdf(), mock=True, persist=False))

    assert len(response.alt_text_proposals) == 1
    assert "[MOCK]" in response.alt_text_proposals[0].suggested_alt_text
    assert not any("PDF es escaneado y no se pudo verificar" in w for w in response.warnings)