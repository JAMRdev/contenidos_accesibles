"""
Tests de integración de los endpoints con TestClient (Issue #44).

Recorren el flujo mínimo del MVP de punta a punta y los errores de entrada de cada endpoint.
Todo en modo mock (?mock=true): 0 tokens y sin depender de un proveedor de IA.
"""
import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.main import app
from tests.test_office_documents import DOCX_MIME, _docx

client = TestClient(app)

MATERIAL = (
    "Unidad 3: El ciclo del agua\n\n"
    "El ciclo del agua, también conocido como ciclo hidrológico, es el proceso de circulación del agua entre "
    "los distintos compartimentos de la hidrosfera, en el cual se producen transformaciones de estado físico "
    "que dependen de la temperatura y la presión, y que permiten que el agua pase de la superficie terrestre a "
    "la atmósfera mediante la evaporación y la transpiración de las plantas, para luego condensarse formando "
    "nubes y regresar a la superficie en forma de precipitación.\n\n"
    "Actividad 1: Leé el texto de la página 8, subrayá las ideas principales, armá un cuadro comparativo entre "
    "evaporación y condensación, respondé las preguntas del final y entregá todo en una hoja aparte antes del viernes."
)
LONG_SENTENCE = MATERIAL.split("\n\n")[1]
ACTIVITY = MATERIAL.split("\n\n")[2]
TOO_BIG = b"%PDF-1.7\n" + b"0" * (10 * 1024 * 1024 + 1)


def _pdf(text: str = "Actividad 1: leé el texto y respondé las preguntas del final.") -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=11)
    return doc.tobytes()


def _encrypted_pdf() -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((50, 80), "Material protegido")
    return doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="docente", owner_pw="admin")


def _scanned_pdf() -> bytes:
    """Una página que es solo una imagen: sin texto digital seleccionable."""
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 500), False)
    pix.set_rect(pix.irect, (200, 200, 200))
    page.insert_image(page.rect, pixmap=pix)
    return doc.tobytes()


def _barrier(analysis: dict, text: str) -> dict:
    return next(b for b in analysis["barriers"] if b["original_text"].startswith(text[:40]))


def _propose(analysis_id: str, barrier_id: str, adaptation_type: str, fragment: str | None = None) -> dict:
    payload = {"analysis_id": analysis_id, "barrier_id": barrier_id, "adaptation_type": adaptation_type}
    if fragment:
        payload["fragment"] = fragment
    res = client.post("/api/v1/adaptations?mock=true", json=payload)
    assert res.status_code == 201, res.text
    return res.json()


def _decide(proposal_id: str, decision: str, edited_text: str | None = None) -> None:
    body = {"decision": decision} | ({"edited_text": edited_text} if edited_text else {})
    res = client.post(f"/api/v1/adaptations/{proposal_id}/decision", json=body)
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# Flujo mínimo del MVP: material → diagnóstico → propuestas → revisión → versión final → informe
# ---------------------------------------------------------------------------

def test_flujo_mvp_completo_con_texto():
    analysis = client.post("/api/v1/analysis/sample?mock=true", json={"text": MATERIAL}).json()
    aid = analysis["analysis_id"]
    assert analysis["barriers"] and analysis["dimensions"]

    # El diagnóstico se puede recuperar sin volver a analizar
    assert client.get(f"/api/v1/analysis/{aid}").json()["analysis_id"] == aid

    edited = "El ciclo del agua es la circulación del agua en la naturaleza. El agua se evapora y forma nubes."
    p_edit = _propose(aid, _barrier(analysis, LONG_SENTENCE)["id"], "lenguaje_claro")
    p_steps = _propose(aid, _barrier(analysis, ACTIVITY)["id"], "consigna_en_pasos", fragment=ACTIVITY)
    # Una propuesta pendiente por barrera: la tercera va a otra barrera, con el fragmento del docente
    other = next(b["id"] for b in analysis["barriers"] if b["id"] not in (p_edit["barrier_id"], p_steps["barrier_id"]))
    p_discard = _propose(aid, other, "mejorar_estructura", fragment="Unidad 3: El ciclo del agua")
    _decide(p_edit["proposal_id"], "editada", edited)
    _decide(p_steps["proposal_id"], "aceptada")
    _decide(p_discard["proposal_id"], "descartada")

    proposals = client.get("/api/v1/adaptations", params={"analysis_id": aid}).json()
    assert {p["status"] for p in proposals} == {"editada", "aceptada", "descartada"}
    decisions = client.get("/api/v1/adaptations/decisions", params={"analysis_id": aid}).json()
    assert len(decisions) == 3

    final = client.get("/api/v1/adaptations/final-version", params={"analysis_id": aid}).json()
    assert final["applied_count"] == 2 and final["discarded_count"] == 1
    assert edited in final["adapted_text"]  # la edición del docente, textual
    assert p_steps["proposed_text"] in final["adapted_text"]
    assert LONG_SENTENCE not in final["adapted_text"] and ACTIVITY not in final["adapted_text"]
    assert final["adapted_text"].startswith("Unidad 3: El ciclo del agua")  # la descartada no cambia
    assert final["original_text"] == MATERIAL  # el original queda intacto

    report = client.get("/api/v1/adaptations/report", params={"analysis_id": aid}).json()
    assert report["resolved_count"] == 2 and report["discarded_count"] == 1
    assert report["score_after"] > report["score_before"]


def test_flujo_mvp_con_deshacer_vuelve_al_original():
    analysis = client.post("/api/v1/analysis/sample?mock=true", json={"text": MATERIAL}).json()
    aid = analysis["analysis_id"]
    proposal = _propose(aid, _barrier(analysis, LONG_SENTENCE)["id"], "lenguaje_claro")
    _decide(proposal["proposal_id"], "aceptada")

    assert client.post(f"/api/v1/adaptations/{proposal['proposal_id']}/undo").status_code == 200

    final = client.get("/api/v1/adaptations/final-version", params={"analysis_id": aid}).json()
    assert final["adapted_text"] == MATERIAL
    assert final["pending_count"] == 1


@pytest.mark.parametrize(
    ("filename", "content", "mime"),
    [
        ("actividad.pdf", _pdf(ACTIVITY), "application/pdf"),
        ("actividad.docx", _docx(lambda d: d.add_paragraph(ACTIVITY)), DOCX_MIME),
    ],
    ids=["pdf", "docx"],
)
def test_flujo_mvp_desde_archivo(filename, content, mime):
    analysis = client.post("/api/v1/analysis/file?mock=true", files={"file": (filename, content, mime)}).json()
    aid = analysis["analysis_id"]
    proposal = _propose(aid, analysis["barriers"][0]["id"], "consigna_en_pasos", fragment=ACTIVITY)
    _decide(proposal["proposal_id"], "aceptada")

    final = client.get("/api/v1/adaptations/final-version", params={"analysis_id": aid}).json()
    assert final["applied_count"] == 1, final["warnings"]
    assert proposal["proposed_text"] in final["adapted_text"]


# ---------------------------------------------------------------------------
# /documents
# ---------------------------------------------------------------------------

def test_process_text_devuelve_metricas():
    res = client.post("/api/v1/documents/process-text", json={"text": "Leé el texto y respondé las preguntas."})
    assert res.status_code == 200
    assert res.json()["source_type"] == "text"
    assert res.json()["word_count"] == 7


@pytest.mark.parametrize(
    "payload",
    [{"text": "corto"}, {"text": "a " * 25_001}, {}],
    ids=["texto-corto", "texto-largo", "sin-texto"],
)
def test_process_text_rechaza_texto_invalido(payload):
    assert client.post("/api/v1/documents/process-text", json=payload).status_code == 422


def test_upload_pdf_extrae_el_texto():
    res = client.post("/api/v1/documents/upload-pdf", files={"file": ("a.pdf", _pdf(), "application/pdf")})
    assert res.status_code == 200
    assert "Actividad 1" in res.json()["raw_text"]


PDF_ERRORS = [
    ("a.txt", _pdf(), "application/pdf", 400, "extensión .pdf"),
    ("a.pdf", _pdf(), "image/png", 400, "Content-Type"),
    ("a.pdf", b"esto no es un pdf", "application/pdf", 400, "firma '%PDF-' ausente"),
    ("a.pdf", b"", "application/pdf", 400, "vacío"),
    ("a.pdf", b"%PDF-1.7 roto", "application/pdf", 400, "No se pudo procesar"),
    ("a.pdf", _encrypted_pdf(), "application/pdf", 400, "contraseña"),
    ("a.pdf", TOO_BIG, "application/pdf", 413, "10 MB"),
]
PDF_ERROR_IDS = ["extension", "content-type", "firma", "vacio", "corrupto", "cifrado", "mayor-a-10mb"]


@pytest.mark.parametrize(("filename", "content", "mime", "status", "detail"), PDF_ERRORS, ids=PDF_ERROR_IDS)
def test_upload_pdf_errores(filename, content, mime, status, detail):
    res = client.post("/api/v1/documents/upload-pdf", files={"file": (filename, content, mime)})
    assert res.status_code == status
    assert detail in res.json()["detail"]


def test_upload_generico_rechaza_mas_de_10mb():
    res = client.post("/api/v1/documents/upload", files={"file": ("a.pdf", TOO_BIG, "application/pdf")})
    assert res.status_code == 413


# ---------------------------------------------------------------------------
# /analysis
# ---------------------------------------------------------------------------

def test_analysis_sample_acepta_text_plain():
    res = client.post(
        "/api/v1/analysis/sample?mock=true",
        content=MATERIAL.encode("utf-8"),
        headers={"Content-Type": "text/plain"},
    )
    assert res.status_code == 200


@pytest.mark.parametrize(
    ("body", "content_type", "error_type"),
    [
        (b'{"text": "corto"}', "application/json", "string_too_short"),
        (('{"text": "' + "a " * 10_001 + '"}').encode(), "application/json", "string_too_long"),
        (b"", "application/json", "json_invalid"),
        (b'{"text": ', "application/json", "json_invalid"),
        ("Leé el texto y respondé las preguntas.".encode("latin-1"), "text/plain", "unicode_decode"),
    ],
    ids=["texto-corto", "texto-largo", "vacio", "json-roto", "no-utf8"],
)
def test_analysis_sample_errores_de_entrada(body, content_type, error_type):
    res = client.post("/api/v1/analysis/sample?mock=true", content=body, headers={"Content-Type": content_type})
    assert res.status_code == 422
    assert res.json()["detail"][0]["type"] == error_type


def test_analysis_sample_solo_espacios_responde_422():
    res = client.post("/api/v1/analysis/sample?mock=true", json={"text": " " * 20})
    assert res.status_code == 422


@pytest.mark.parametrize(("filename", "content", "mime", "status", "detail"), PDF_ERRORS, ids=PDF_ERROR_IDS)
def test_analysis_pdf_errores(filename, content, mime, status, detail):
    res = client.post("/api/v1/analysis/pdf?mock=true", files={"file": (filename, content, mime)})
    assert res.status_code == status
    assert detail in res.json()["detail"]


def test_analysis_pdf_escaneado_se_diagnostica_solo_con_reglas():
    res = client.post("/api/v1/analysis/pdf?mock=true", files={"file": ("scan.pdf", _scanned_pdf(), "application/pdf")})
    assert res.status_code == 200
    data = res.json()
    assert any(b["issue"] == "PDF escaneado sin capa de texto" for b in data["barriers"])
    assert data["estimated_score"] <= 40


def test_analysis_file_rechaza_mas_de_10mb():
    res = client.post("/api/v1/analysis/file?mock=true", files={"file": ("a.pdf", TOO_BIG, "application/pdf")})
    assert res.status_code == 413


# ---------------------------------------------------------------------------
# Recursos inexistentes y entradas inválidas en /adaptations
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/analysis/no-existe",
        "/api/v1/adaptations/final-version?analysis_id=no-existe",
        "/api/v1/adaptations/report?analysis_id=no-existe",
        "/api/v1/adaptations/decisions?analysis_id=no-existe",
        "/api/v1/adaptations/no-existe",
    ],
)
def test_recursos_inexistentes_responden_404(path):
    assert client.get(path).status_code == 404


def test_adaptacion_de_diagnostico_inexistente_responde_404():
    res = client.post(
        "/api/v1/adaptations?mock=true",
        json={"analysis_id": "no-existe", "barrier_id": "bar-1", "adaptation_type": "lenguaje_claro"},
    )
    assert res.status_code == 404


def test_adaptacion_con_tipo_invalido_responde_422():
    res = client.post(
        "/api/v1/adaptations?mock=true",
        json={"analysis_id": "x", "barrier_id": "bar-1", "adaptation_type": "resumir"},
    )
    assert res.status_code == 422


def test_health():
    res = client.get("/api/v1/health")
    assert res.status_code == 200
    assert res.json()["status"] == "online"
