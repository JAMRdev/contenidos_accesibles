import uuid

import pytest
from fastapi.testclient import TestClient

from app.db import analysis_repository
from app.db.database import init_db
from app.main import app

# ---------------------------------------------------------------------------
# Material con varias secciones para verificar el informe
# ---------------------------------------------------------------------------
MATERIAL = (
    "Sección 1: Introducción.\n"
    "Las plantas realizan la fotosíntesis utilizando luz solar, agua y dióxido de carbono para producir glucosa.\n\n"
    "Sección 2: Actividad.\n"
    "Leé el texto de la página 8, subrayá las ideas principales y respondé las preguntas del final.\n\n"
    "Sección 3: Conclusión.\n"
    "Este proceso es fundamental para todos los ecosistemas terrestres y acuáticos del planeta."
)


@pytest.fixture(autouse=True)
def ensure_db():
    init_db()


@pytest.fixture
def client():
    return TestClient(app)


def _analyze(client, text: str = MATERIAL) -> dict:
    res = client.post("/api/v1/analysis/sample?mock=true", json={"text": text})
    assert res.status_code == 200
    return res.json()


def _propose(client, analysis_id: str, barrier_id: str, fragment: str) -> dict:
    res = client.post(
        "/api/v1/adaptations?mock=true",
        json={"analysis_id": analysis_id, "barrier_id": barrier_id,
              "adaptation_type": "consigna_en_pasos", "fragment": fragment},
    )
    assert res.status_code == 201
    return res.json()


def _decide(client, proposal_id: str, decision: str, edited_text: str | None = None) -> dict:
    body = {"decision": decision}
    if edited_text:
        body["edited_text"] = edited_text
    res = client.post(f"/api/v1/adaptations/{proposal_id}/decision", json=body)
    assert res.status_code == 200
    return res.json()


def _report(client, analysis_id: str) -> dict:
    res = client.get("/api/v1/adaptations/report", params={"analysis_id": analysis_id})
    return res


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_report_structure_with_no_proposals(client):
    analysis = _analyze(client)
    res = _report(client, analysis["analysis_id"])
    assert res.status_code == 200
    data = res.json()

    assert data["analysis_id"] == analysis["analysis_id"]
    assert "generated_at" in data
    assert isinstance(data["score_before"], int)
    assert isinstance(data["score_after"], int)
    assert isinstance(data["score_delta"], int)
    assert data["score_delta"] == data["score_after"] - data["score_before"]
    assert "disclaimer" in data
    assert "WCAG" in data["disclaimer"]
    assert isinstance(data["executive_summary"], list)
    assert isinstance(data["barriers_detail"], list)
    assert data["total_proposals"] == 0
    assert data["resolved_count"] == 0


def test_report_all_barriers_pending_when_no_proposals(client):
    analysis = _analyze(client)
    res = _report(client, analysis["analysis_id"])
    data = res.json()

    assert data["total_barriers"] == len(analysis["barriers"])
    assert data["pending_count"] == len(analysis["barriers"])
    assert data["resolved_count"] == 0
    assert data["discarded_count"] == 0

    for detail in data["barriers_detail"]:
        assert detail["resolution_status"] == "pendiente"
        assert detail["final_text"] is None
        assert detail["proposal_id"] is None


def test_report_accepted_barrier_shows_as_resolved(client):
    analysis = _analyze(client)
    aid = analysis["analysis_id"]
    frag = "Leé el texto de la página 8, subrayá las ideas principales y respondé las preguntas del final."
    p = _propose(client, aid, "bar-1", frag)
    _decide(client, p["proposal_id"], "aceptada")

    data = _report(client, aid).json()
    assert data["resolved_count"] == 1
    bar1 = next(b for b in data["barriers_detail"] if b["barrier_id"] == "bar-1")
    assert bar1["resolution_status"] == "aceptada"
    assert bar1["final_text"] == p["proposed_text"]
    assert bar1["proposal_id"] == p["proposal_id"]


def test_report_edited_barrier_uses_teacher_text(client):
    analysis = _analyze(client)
    aid = analysis["analysis_id"]
    frag = "Leé el texto de la página 8, subrayá las ideas principales y respondé las preguntas del final."
    p = _propose(client, aid, "bar-1", frag)
    teacher_text = "Paso 1: Leer pág 8.\nPaso 2: Marcar ideas.\nPaso 3: Contestar."
    _decide(client, p["proposal_id"], "editada", edited_text=teacher_text)

    data = _report(client, aid).json()
    bar1 = next(b for b in data["barriers_detail"] if b["barrier_id"] == "bar-1")
    assert bar1["resolution_status"] == "editada"
    assert bar1["final_text"] == teacher_text


def test_report_discarded_barrier_shows_no_final_text(client):
    analysis = _analyze(client)
    aid = analysis["analysis_id"]
    frag = "Leé el texto de la página 8, subrayá las ideas principales y respondé las preguntas del final."
    p = _propose(client, aid, "bar-1", frag)
    _decide(client, p["proposal_id"], "descartada")

    data = _report(client, aid).json()
    assert data["discarded_count"] == 1
    assert data["resolved_count"] == 0
    bar1 = next(b for b in data["barriers_detail"] if b["barrier_id"] == "bar-1")
    assert bar1["resolution_status"] == "descartada"
    assert bar1["final_text"] is None


def test_report_mixed_decisions(client):
    analysis = _analyze(client)
    aid = analysis["analysis_id"]
    frag1 = "Leé el texto de la página 8, subrayá las ideas principales y respondé las preguntas del final."
    frag2 = "Las plantas realizan la fotosíntesis utilizando luz solar, agua y dióxido de carbono para producir glucosa."

    # Una propuesta pendiente por barrera: se decide cada una antes de pedir la siguiente
    p1 = _propose(client, aid, "bar-1", frag1)
    _decide(client, p1["proposal_id"], "aceptada")
    p2 = _propose(client, aid, "bar-1", frag2)
    _decide(client, p2["proposal_id"], "descartada")

    data = _report(client, aid).json()
    assert data["total_proposals"] == 2
    # la propuesta aceptada (editada > aceptada > descartada) gana para bar-1
    bar1 = next(b for b in data["barriers_detail"] if b["barrier_id"] == "bar-1")
    assert bar1["resolution_status"] == "aceptada"


def test_report_executive_summary_with_applied_proposals(client):
    analysis = _analyze(client)
    aid = analysis["analysis_id"]
    frag = "Leé el texto de la página 8, subrayá las ideas principales y respondé las preguntas del final."
    p = _propose(client, aid, "bar-1", frag)
    _decide(client, p["proposal_id"], "aceptada")

    data = _report(client, aid).json()
    assert len(data["executive_summary"]) >= 1
    # Al menos una frase menciona la adaptación aplicada
    joined = " ".join(data["executive_summary"])
    assert "adaptación" in joined or "adaptaciones" in joined


def test_report_executive_summary_no_proposals(client):
    analysis = _analyze(client)
    data = _report(client, analysis["analysis_id"]).json()
    joined = " ".join(data["executive_summary"])
    # Con barreras detectadas pero sin propuestas, el resumen menciona barreras sin decisión
    assert "barrera" in joined.lower() or "No se aplicaron" in joined


def test_report_score_delta_equals_after_minus_before(client):
    analysis = _analyze(client)
    aid = analysis["analysis_id"]
    frag = "Leé el texto de la página 8, subrayá las ideas principales y respondé las preguntas del final."
    p = _propose(client, aid, "bar-1", frag)
    _decide(client, p["proposal_id"], "aceptada")

    data = _report(client, aid).json()
    assert data["score_delta"] == data["score_after"] - data["score_before"]


def test_report_score_after_is_deterministic(client):
    """El score_after no vuelve a consultar la IA: dos llamadas al informe devuelven el mismo valor."""
    analysis = _analyze(client)
    aid = analysis["analysis_id"]
    frag = "Leé el texto de la página 8, subrayá las ideas principales y respondé las preguntas del final."
    p = _propose(client, aid, "bar-1", frag)
    _decide(client, p["proposal_id"], "aceptada")

    data1 = _report(client, aid).json()
    data2 = _report(client, aid).json()
    assert data1["score_after"] == data2["score_after"]
    assert data1["score_before"] == data2["score_before"]


LONG_SENTENCE = (
    "El ciclo del agua es el proceso de circulación del agua entre los distintos compartimentos de la "
    "hidrosfera, en el cual se producen transformaciones de estado físico que dependen de la temperatura y "
    "la presión, y que permiten que el agua pase de la superficie terrestre a la atmósfera mediante la "
    "evaporación y la transpiración de las plantas, para luego condensarse formando nubes."
)


def test_report_without_changes_keeps_the_same_score(client):
    """Sin cambios aplicados no hay mejora ni caída: antes se comparaba IA + reglas contra solo reglas."""
    analysis = _analyze(client, MATERIAL + "\n\n" + LONG_SENTENCE)
    data = _report(client, analysis["analysis_id"]).json()

    assert data["score_after"] == data["score_before"]
    assert data["score_delta"] == 0
    assert data["score_breakdown_after"]["ai_score"] == data["score_breakdown_before"]["ai_score"]


def test_report_shortening_a_long_sentence_improves_the_score(client):
    analysis = _analyze(client, MATERIAL + "\n\n" + LONG_SENTENCE)
    aid = analysis["analysis_id"]
    p = _propose(client, aid, "bar-1", LONG_SENTENCE)
    _decide(client, p["proposal_id"], "editada", edited_text=(
        "El ciclo del agua es la circulación del agua en la naturaleza. "
        "El agua se evapora y sube a la atmósfera. Después forma nubes."
    ))

    data = _report(client, aid).json()
    rules_before = {x["rule"] for x in data["score_breakdown_before"]["penalties"]}
    rules_after = {x["rule"] for x in data["score_breakdown_after"]["penalties"]}
    assert "oraciones_extensas" in rules_before and "oraciones_extensas" not in rules_after
    assert data["score_delta"] > 0


def test_report_keeps_file_penalties_that_text_cannot_recompute(client):
    """Imágenes de un Word: adaptar el texto no las resuelve, así que su penalización se conserva."""
    from tests.test_office_documents import _docx, _guia_docx

    res = client.post(
        "/api/v1/analysis/file?mock=true",
        files={"file": ("guia.docx", _docx(_guia_docx), "application/octet-stream")},
    )
    data = _report(client, res.json()["analysis_id"]).json()

    assert any(x["rule"] == "imagenes_sin_verificar" for x in data["score_breakdown_after"]["penalties"])
    assert data["score_delta"] == 0


def test_report_disclaimer_always_present(client):
    analysis = _analyze(client)
    data = _report(client, analysis["analysis_id"]).json()
    assert data["disclaimer"]
    assert len(data["disclaimer"]) > 10


def test_report_score_breakdown_before_present(client):
    analysis = _analyze(client)
    data = _report(client, analysis["analysis_id"]).json()
    # score_breakdown_before puede ser None si el mock no lo provee, pero score_before siempre existe
    assert isinstance(data["score_before"], int)
    assert data["score_before"] >= 0


def test_report_score_breakdown_after_always_present(client):
    """breakdown_after siempre tiene valor porque se calcula con el motor de reglas."""
    analysis = _analyze(client)
    data = _report(client, analysis["analysis_id"]).json()
    assert data["score_breakdown_after"] is not None
    assert "final_score" in data["score_breakdown_after"]
    assert "rules_score" in data["score_breakdown_after"]


def test_report_unknown_analysis_returns_404(client):
    res = _report(client, str(uuid.uuid4()))
    assert res.status_code == 404
    assert res.json()["detail"] == "Diagnóstico no encontrado"


def test_report_expired_analysis_returns_404(client):
    analysis = _analyze(client)
    aid = analysis["analysis_id"]
    rec = analysis_repository.get(aid)
    analysis_repository.save(
        analysis_id=rec["id"],
        source_type=rec["source_type"],
        filename=rec["filename"],
        extracted_text=rec["extracted_text"],
        diagnostic_json=rec["diagnostic_json"],
        created_at=rec["created_at"],
        expires_at="2020-01-01T00:00:00Z",
    )
    res = _report(client, aid)
    assert res.status_code == 404
    assert res.json()["detail"] == "El diagnóstico ha expirado"
