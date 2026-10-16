import uuid
from datetime import datetime, timezone, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import adaptation_repository, analysis_repository
from app.db.database import init_db
from app.main import app

MATERIAL_SECTIONS = (
    "Sección 1: Introducción a la Fotosíntesis.\n"
    "Las plantas utilizan agua y dióxido de carbono para producir glucosa y liberar oxígeno a la atmósfera.\n\n"
    "Sección 2: Actividad Práctica.\n"
    "Leé el texto de la página 8, subrayá las ideas principales y respondé las preguntas del final.\n\n"
    "Sección 3: Conclusión.\n"
    "Este proceso es fundamental para el sostenimiento de todos los ecosistemas terrestres y acuáticos."
)


@pytest.fixture(autouse=True)
def ensure_db():
    init_db()


@pytest.fixture
def client():
    return TestClient(app)


def _analyze(client, text: str = MATERIAL_SECTIONS) -> dict:
    res = client.post("/api/v1/analysis/sample?mock=true", json={"text": text})
    assert res.status_code == 200
    return res.json()


def _create_proposal(client, analysis_id: str, barrier_id: str, adaptation_type: str, fragment: str | None = None) -> dict:
    payload = {
        "analysis_id": analysis_id,
        "barrier_id": barrier_id,
        "adaptation_type": adaptation_type,
    }
    if fragment:
        payload["fragment"] = fragment
    res = client.post("/api/v1/adaptations?mock=true", json=payload)
    assert res.status_code == 201
    return res.json()


def _decide(client, proposal_id: str, decision: str, edited_text: str | None = None) -> dict:
    body = {"decision": decision}
    if edited_text is not None:
        body["edited_text"] = edited_text
    res = client.post(f"/api/v1/adaptations/{proposal_id}/decision", json=body)
    assert res.status_code == 200
    return res.json()


def _get_final(client, analysis_id: str) -> dict:
    res = client.get("/api/v1/adaptations/final-version", params={"analysis_id": analysis_id})
    return res


# --- Tests de generación de versión final (Issue #38) -------------------------


def test_final_version_with_no_proposals(client):
    analysis = _analyze(client)
    res = _get_final(client, analysis["analysis_id"])
    assert res.status_code == 200
    data = res.json()
    assert data["analysis_id"] == analysis["analysis_id"]
    assert data["original_text"] == MATERIAL_SECTIONS
    assert data["adapted_text"] == MATERIAL_SECTIONS
    assert data["total_proposals"] == 0
    assert data["applied_count"] == 0
    assert data["discarded_count"] == 0
    assert data["pending_count"] == 0
    assert data["applied_proposals"] == []
    assert data["warnings"] == []


def test_apply_only_accepted_proposals(client):
    analysis = _analyze(client)
    aid = analysis["analysis_id"]

    # Creamos 2 propuestas para fragmentos distintos
    frag1 = "Leé el texto de la página 8, subrayá las ideas principales y respondé las preguntas del final."
    p1 = _create_proposal(client, aid, "bar-1", "consigna_en_pasos", fragment=frag1)
    # Aceptamos p1 (una sola pendiente por barrera) y dejamos p2 en pendiente
    _decide(client, p1["proposal_id"], "aceptada")

    frag2 = "Las plantas utilizan agua y dióxido de carbono para producir glucosa y liberar oxígeno a la atmósfera."
    p2 = _create_proposal(client, aid, "bar-1", "lenguaje_claro", fragment=frag2)

    res = _get_final(client, aid)
    assert res.status_code == 200
    data = res.json()

    assert data["total_proposals"] == 2
    assert data["applied_count"] == 1
    assert data["pending_count"] == 1
    assert data["discarded_count"] == 0
    assert data["applied_proposals"] == [p1["proposal_id"]]

    # p1 debe estar aplicada
    assert p1["proposed_text"] in data["adapted_text"]
    assert frag1 not in data["adapted_text"]

    # p2 estaba pendiente: frag2 debe mantenerse intacto
    assert frag2 in data["adapted_text"]


def test_apply_edited_proposals_verbatim(client):
    analysis = _analyze(client)
    aid = analysis["analysis_id"]

    frag = "Leé el texto de la página 8, subrayá las ideas principales y respondé las preguntas del final."
    p = _create_proposal(client, aid, "bar-1", "consigna_en_pasos", fragment=frag)

    teacher_custom = "Paso A: Leer pág 8.\nPaso B: Resaltar ideas.\nPaso C: Contestar preguntas."
    _decide(client, p["proposal_id"], "editada", edited_text=teacher_custom)

    res = _get_final(client, aid)
    assert res.status_code == 200
    data = res.json()

    assert data["applied_count"] == 1
    assert teacher_custom in data["adapted_text"]
    assert frag not in data["adapted_text"]
    assert p["proposed_text"] not in data["adapted_text"]


def test_discarded_proposals_are_not_applied(client):
    analysis = _analyze(client)
    aid = analysis["analysis_id"]

    frag = "Leé el texto de la página 8, subrayá las ideas principales y respondé las preguntas del final."
    p = _create_proposal(client, aid, "bar-1", "consigna_en_pasos", fragment=frag)

    _decide(client, p["proposal_id"], "descartada")

    res = _get_final(client, aid)
    assert res.status_code == 200
    data = res.json()

    assert data["total_proposals"] == 1
    assert data["applied_count"] == 0
    assert data["discarded_count"] == 1
    assert data["pending_count"] == 0
    assert data["adapted_text"] == MATERIAL_SECTIONS
    assert data["applied_proposals"] == []


def test_mixed_decisions_combination(client):
    analysis = _analyze(client)
    aid = analysis["analysis_id"]

    frag1 = "Sección 1: Introducción a la Fotosíntesis."
    frag2 = "Las plantas utilizan agua y dióxido de carbono para producir glucosa y liberar oxígeno a la atmósfera."
    frag3 = "Leé el texto de la página 8, subrayá las ideas principales y respondé las preguntas del final."
    frag4 = "Este proceso es fundamental para el sostenimiento de todos los ecosistemas terrestres y acuáticos."

    # 1 aceptada, 1 editada, 1 descartada, 1 pendiente (cada una se decide antes de pedir la siguiente)
    p1 = _create_proposal(client, aid, "bar-1", "mejorar_estructura", fragment=frag1)
    _decide(client, p1["proposal_id"], "aceptada")
    p2 = _create_proposal(client, aid, "bar-1", "lenguaje_claro", fragment=frag2)
    teacher_p2 = "Las plantas usan agua y dióxido de carbono. Fabrican glucosa y liberan oxígeno."
    _decide(client, p2["proposal_id"], "editada", edited_text=teacher_p2)
    p3 = _create_proposal(client, aid, "bar-1", "consigna_en_pasos", fragment=frag3)
    _decide(client, p3["proposal_id"], "descartada")
    p4 = _create_proposal(client, aid, "bar-1", "reorganizar", fragment=frag4)
    # p4 se deja pendiente

    res = _get_final(client, aid)
    assert res.status_code == 200
    data = res.json()

    assert data["total_proposals"] == 4
    assert data["applied_count"] == 2
    assert data["discarded_count"] == 1
    assert data["pending_count"] == 1
    assert set(data["applied_proposals"]) == {p1["proposal_id"], p2["proposal_id"]}

    # p1 (aceptada) y p2 (editada) se aplicaron
    assert p1["proposed_text"] in data["adapted_text"]
    assert teacher_p2 in data["adapted_text"]

    # p3 (descartada) y p4 (pendiente) se conservan intactos
    assert frag3 in data["adapted_text"]
    assert frag4 in data["adapted_text"]


def test_overlapping_proposals_teacher_edit_takes_precedence(client):
    analysis = _analyze(client)
    aid = analysis["analysis_id"]

    # Frag completo y un sub-fragmento
    frag_full = "Leé el texto de la página 8, subrayá las ideas principales y respondé las preguntas del final."
    frag_sub = "subrayá las ideas principales y respondé las preguntas del final."

    # p_full fue aceptada (por IA), pero p_sub fue editada explícitamente por el docente
    p_full = _create_proposal(client, aid, "bar-1", "consigna_en_pasos", fragment=frag_full)
    _decide(client, p_full["proposal_id"], "aceptada")
    p_sub = _create_proposal(client, aid, "bar-1", "lenguaje_claro", fragment=frag_sub)
    _decide(client, p_sub["proposal_id"], "editada", edited_text="marcar ideas centrales y contestar preguntas.")

    res = _get_final(client, aid)
    assert res.status_code == 200
    data = res.json()

    # La editada por docente debe tener precedencia sobre la aceptada
    assert data["applied_count"] == 1
    assert data["applied_proposals"] == [p_sub["proposal_id"]]
    assert "marcar ideas centrales y contestar preguntas." in data["adapted_text"]
    assert len(data["warnings"]) >= 1
    assert any("solapamiento" in w for w in data["warnings"])


def test_same_fragment_in_two_proposals_is_an_overlap_not_missing(client):
    """Dos barreras que citan la misma oración (reglas + IA): gana la edición del docente."""
    analysis = _analyze(client)
    aid = analysis["analysis_id"]

    frag = "Leé el texto de la página 8, subrayá las ideas principales y respondé las preguntas del final."
    p_accepted = _create_proposal(client, aid, "bar-1", "consigna_en_pasos", fragment=frag)
    _decide(client, p_accepted["proposal_id"], "aceptada")
    p_edited = _create_proposal(client, aid, "bar-1", "lenguaje_claro", fragment=frag)
    _decide(client, p_edited["proposal_id"], "editada", edited_text="Leé la página 8 y respondé las preguntas.")

    data = _get_final(client, aid).json()

    assert data["applied_proposals"] == [p_edited["proposal_id"]]
    assert "Leé la página 8 y respondé las preguntas." in data["adapted_text"]
    assert frag not in data["adapted_text"]
    assert any("solapamiento" in w for w in data["warnings"])
    assert not any("No se encontró" in w for w in data["warnings"])


def test_overlapping_proposals_same_status_recent_decision_takes_precedence(client):
    analysis = _analyze(client)
    aid = analysis["analysis_id"]

    frag1 = "Leé el texto de la página 8, subrayá las ideas principales y respondé las preguntas del final."
    frag2 = "subrayá las ideas principales y respondé las preguntas del final."

    p1 = _create_proposal(client, aid, "bar-1", "consigna_en_pasos", fragment=frag1)
    # Decidimos p1 primero (una sola pendiente por barrera)
    _decide(client, p1["proposal_id"], "aceptada")
    p2 = _create_proposal(client, aid, "bar-1", "lenguaje_claro", fragment=frag2)

    # Simulamos que p2 se decidió con timestamp posterior
    rec2 = adaptation_repository.get(p2["proposal_id"])
    now_plus = (datetime.now(timezone.utc) + timedelta(minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    adaptation_repository.save(
        proposal_id=rec2["id"],
        analysis_id=rec2["analysis_id"],
        barrier_id=rec2["barrier_id"],
        status="aceptada",
        proposal_json=rec2["proposal_json"],
        created_at=rec2["created_at"],
        expires_at=rec2["expires_at"],
    )
    # Actualizamos el json interno con decided_at más reciente
    p2_obj = client.get(f"/api/v1/adaptations/{p2['proposal_id']}").json()
    p2_obj["status"] = "aceptada"
    p2_obj["decided_at"] = now_plus
    p2_obj["final_text"] = p2_obj["proposed_text"]
    import json
    adaptation_repository.save(
        proposal_id=rec2["id"],
        analysis_id=rec2["analysis_id"],
        barrier_id=rec2["barrier_id"],
        status="aceptada",
        proposal_json=json.dumps(p2_obj),
        created_at=rec2["created_at"],
        expires_at=rec2["expires_at"],
    )

    res = _get_final(client, aid)
    assert res.status_code == 200
    data = res.json()

    assert data["applied_count"] == 1
    assert data["applied_proposals"] == [p2["proposal_id"]]
    assert any("solapamiento" in w for w in data["warnings"])


def test_undo_reverts_decision_in_final_version(client):
    analysis = _analyze(client)
    aid = analysis["analysis_id"]

    frag = "Leé el texto de la página 8, subrayá las ideas principales y respondé las preguntas del final."
    p = _create_proposal(client, aid, "bar-1", "consigna_en_pasos", fragment=frag)

    _decide(client, p["proposal_id"], "aceptada")
    res1 = _get_final(client, aid).json()
    assert res1["applied_count"] == 1
    assert p["proposed_text"] in res1["adapted_text"]

    # El docente deshace la decisión
    client.post(f"/api/v1/adaptations/{p['proposal_id']}/undo")

    res2 = _get_final(client, aid).json()
    assert res2["applied_count"] == 0
    assert res2["pending_count"] == 1
    assert res2["adapted_text"] == MATERIAL_SECTIONS


def test_unknown_analysis_returns_404(client):
    res = client.get("/api/v1/adaptations/final-version", params={"analysis_id": str(uuid.uuid4())})
    assert res.status_code == 404
    assert res.json()["detail"] == "Diagnóstico no encontrado"


def test_expired_analysis_returns_404(client):
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
    res = client.get("/api/v1/adaptations/final-version", params={"analysis_id": aid})
    assert res.status_code == 404
    assert res.json()["detail"] == "El diagnóstico ha expirado"
