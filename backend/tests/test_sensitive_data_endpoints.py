"""
Pruebas HTTP de la respuesta 422 por datos personales (Issue #66).
Usan ?mock=true, igual que test_pdf_endpoint_mock_returns_alt_text_proposals: 0 tokens.
"""

import pymupdf
from fastapi.testclient import TestClient

from app.main import app
from app.services.sensitive_data_service import detect

client = TestClient(app)

SAMPLE_URL = "/api/v1/analysis/sample?mock=true"
PDF_URL = "/api/v1/analysis/pdf?mock=true"

CLEAN_TEXT = (
    "Actividad 1: Leé el texto sobre el ciclo del agua.\n"
    "Después respondé las preguntas de la guía con tus palabras."
)
SENSITIVE_TEXT = "Alumno: Juan Pérez\nDNI: 30.123.456\n" + CLEAN_TEXT


def _post_text(text: str, url: str = SAMPLE_URL):
    return client.post(url, content=text.encode("utf-8"), headers={"Content-Type": "text/plain"})


def _pdf_with_text(lines: list[str]) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=600, height=800)
    for i, line in enumerate(lines):
        page.insert_text((50, 80 + i * 20), line, fontsize=12)
    pdf_bytes = doc.tobytes()
    doc.close()
    return pdf_bytes


def test_sample_con_datos_personales_responde_422_con_hallazgos():
    response = _post_text(SENSITIVE_TEXT)

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "sensitive_data_detected"
    assert detail["total"] == 2
    assert [f["type"] for f in detail["findings"]] == ["nombre_rotulado", "dni"]
    assert {"id", "type", "label", "masked", "line"} <= set(detail["findings"][0])
    # La respuesta nunca repite el dato completo.
    assert "Juan" not in response.text and "30.123.456" not in response.text


def test_sample_con_todos_los_hallazgos_descartados_responde_200():
    ids = [f.id for f in detect(SENSITIVE_TEXT)]
    query = "".join(f"&dismissed={i}" for i in ids)

    response = _post_text(SENSITIVE_TEXT, SAMPLE_URL + query)

    assert response.status_code == 200
    assert any("El docente indicó que 2 posible(s) dato(s)" in w for w in response.json()["warnings"])


def test_sample_con_descarte_parcial_sigue_respondiendo_422():
    first_id = detect(SENSITIVE_TEXT)[0].id

    response = _post_text(SENSITIVE_TEXT, SAMPLE_URL + f"&dismissed={first_id}")

    assert response.status_code == 422
    assert [f["type"] for f in response.json()["detail"]["findings"]] == ["dni"]


def test_sample_sin_datos_personales_responde_200_como_antes():
    response = _post_text(CLEAN_TEXT)
    assert response.status_code == 200


def test_pdf_con_datos_personales_responde_422():
    pdf_bytes = _pdf_with_text(["Planilla de seguimiento", "Alumno: Juan Perez", "Nota final: 9"])

    response = client.post(PDF_URL, files={"file": ("planilla.pdf", pdf_bytes, "application/pdf")})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "sensitive_data_detected"
    assert {f["type"] for f in detail["findings"]} == {"nombre_rotulado", "calificacion"}
    assert "Juan" not in response.text