"""
Transformar material (Issue #116): ciclo de vida crear → generar → listo / fallido → decisión.
Modo mock salvo donde se reemplaza el generador por uno de prueba: nunca llama a la IA real.
"""
import asyncio
import io
import time
import wave
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
from docx import Document
from fastapi.testclient import TestClient

from app.core import rate_limit
from app.core.config import settings
from app.core.owner import current_owner
from app.db import transformation_repository, user_activity_repository
from app.db.database import init_db
from app.main import app
from app.schemas.adaptation import AdaptationMetadata
from app.schemas.transformation import RESULT_SCHEMAS, MindMapNode, MindMapResult
from app.services import admin_metrics_service, storage_service, transformation_service, user_metrics_service
from app.services.llm.base import LLMUnavailableError
from app.services.storage_service import LocalStorage
from app.services.transformation_service import Generation

MATERIAL = (
    "Unidad 3: El ciclo del agua\n\n"
    "El agua de los océanos se evapora por el calor del sol y sube a la atmósfera.\n\n"
    "El vapor se enfría, se condensa y forma las nubes.\n\n"
    "Cuando las gotas pesan, caen como lluvia o nieve y vuelven a los ríos y océanos."
)
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FINAL = ("ready", "failed")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "FILE_CLEANUP_INTERVAL_MINUTES", 0)
    init_db()
    storage_service.reset_storage(LocalStorage(str(tmp_path / "storage"), b"secreto-de-prueba"))
    rate_limit.reset()
    # Con el context manager, el event loop de la app vive entre requests: las tareas siguen corriendo
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    storage_service.reset_storage()
    rate_limit.reset()


def _analyze(client, text: str = MATERIAL) -> str:
    res = client.post("/api/v1/analysis/sample?mock=true", json={"text": text})
    assert res.status_code == 200
    return res.json()["analysis_id"]


def _create(client, fmt: str, *, mock: bool = True, **source) -> dict:
    res = client.post(f"/api/v1/transformations?mock={str(mock).lower()}", json={"format": fmt, **source})
    assert res.status_code == 202, res.text
    return res.json()


def _wait(client, transformation_id: str) -> dict:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        data = client.get(f"/api/v1/transformations/{transformation_id}").json()
        if data["status"] in FINAL:
            return data
        time.sleep(0.02)
    raise AssertionError("La transformación no terminó a tiempo")


def _get_url(client, url: str):
    parts = urlsplit(url)
    return client.get(f"{parts.path}?{parts.query}")


def _docx(text: str) -> bytes:
    document = Document()
    for paragraph in text.split("\n\n"):
        document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _upload(client, content: bytes, filename="clase.docx", content_type=DOCX_TYPE) -> str:
    data = client.post("/api/v1/files/upload-url",
                       json={"filename": filename, "content_type": content_type, "size_bytes": len(content)}).json()
    url = urlsplit(data["upload"]["url"])
    assert client.put(f"{url.path}?{url.query}", content=content, headers=data["upload"]["headers"]).status_code == 200
    assert client.post(f"/api/v1/files/{data['file']['file_id']}/complete").status_code == 200
    return data["file"]["file_id"]


def _fake_generator(result_factory, *, delay: float = 0):
    async def generate(text: str) -> Generation:
        if delay:
            await asyncio.sleep(delay)
        return Generation(
            result=result_factory(text),
            metadata=AdaptationMetadata(provider="groq", model="llama-test", connection="prueba", latency_ms=5,
                                        prompt_tokens=10, output_tokens=5, total_tokens=15),
        )
    return generate


# --- Ciclo de vida (mock) ---------------------------------------------------------


@pytest.mark.parametrize("fmt", ["no_images", "mind_map", "infographic", "audio"])
def test_each_format_goes_from_pending_to_ready(client, fmt):
    analysis_id = _analyze(client)
    created = _create(client, fmt, analysis_id=analysis_id)
    assert created["status"] == "pending"
    assert created["review_status"] == "pendiente"
    assert created["source"] == {"type": "analysis", "id": analysis_id}
    assert created["result"] is None

    ready = _wait(client, created["transformation_id"])
    assert ready["status"] == "ready"
    RESULT_SCHEMAS[fmt].model_validate(ready["result"])
    assert ready["metadata"]["provider"] == "mock"
    assert any("mock" in w for w in ready["warnings"])
    assert ready["final_result"] is None  # nada se guarda sin la decisión del docente


def test_audio_generates_a_previewable_file(client):
    ready = _wait(client, _create(client, "audio", analysis_id=_analyze(client))["transformation_id"])
    file = ready["file"]
    assert file["content_type"] == "audio/wav"
    assert file["file_id"] is None
    res = _get_url(client, file["preview_url"])
    assert res.status_code == 200
    with wave.open(io.BytesIO(res.content)) as wav:
        assert wav.getnframes() > 0


def test_formats_without_file_have_no_file(client):
    ready = _wait(client, _create(client, "mind_map", analysis_id=_analyze(client))["transformation_id"])
    assert ready["file"] is None
    assert ready["result"]["root"]["text"] == "Unidad 3: El ciclo del agua"
    assert ready["result"]["root"]["children"]


def test_transforms_from_an_uploaded_file(client):
    file_id = _upload(client, _docx(MATERIAL))
    created = _create(client, "no_images", file_id=file_id)
    assert created["source"] == {"type": "file", "id": file_id}
    ready = _wait(client, created["transformation_id"])
    assert "ciclo del agua" in ready["result"]["text"]


def test_file_with_personal_data_is_not_transformed(client):
    file_id = _upload(client, _docx("Alumno: Juan Pérez, DNI 30.123.456.\n\n" + MATERIAL))
    res = client.post("/api/v1/transformations?mock=true", json={"format": "no_images", "file_id": file_id})
    assert res.status_code == 422
    detail = res.json()["detail"]
    assert detail["code"] == "sensitive_data_detected"

    ids = "&".join(f"dismissed={f['id']}" for f in detail["findings"])
    res = client.post(f"/api/v1/transformations?mock=true&{ids}", json={"format": "no_images", "file_id": file_id})
    assert res.status_code == 202


def test_list_returns_my_transformations(client):
    analysis_id = _analyze(client)
    first = _create(client, "mind_map", analysis_id=analysis_id)
    second = _create(client, "infographic", analysis_id=analysis_id)
    _wait(client, first["transformation_id"])
    _wait(client, second["transformation_id"])
    ids = [t["transformation_id"] for t in client.get("/api/v1/transformations").json()]
    assert ids == [second["transformation_id"], first["transformation_id"]]


# --- Errores del pedido ------------------------------------------------------------------


@pytest.mark.parametrize("source", [{}, {"analysis_id": "a", "file_id": "b"}], ids=["ninguno", "los-dos"])
def test_requires_exactly_one_source(client, source):
    res = client.post("/api/v1/transformations?mock=true", json={"format": "mind_map", **source})
    assert res.status_code == 422


def test_unknown_or_foreign_material_is_404(client):
    res = client.post("/api/v1/transformations?mock=true", json={"format": "mind_map", "analysis_id": "no-existe"})
    assert res.status_code == 404
    analysis_id = _analyze(client)
    app.dependency_overrides[current_owner] = lambda: "user_otro"
    res = client.post("/api/v1/transformations?mock=true", json={"format": "mind_map", "analysis_id": analysis_id})
    assert res.status_code == 404


def test_other_users_cannot_see_or_decide(client):
    ready = _wait(client, _create(client, "mind_map", analysis_id=_analyze(client))["transformation_id"])
    app.dependency_overrides[current_owner] = lambda: "user_otro"
    tid = ready["transformation_id"]
    assert client.get(f"/api/v1/transformations/{tid}").status_code == 404
    assert client.post(f"/api/v1/transformations/{tid}/decision", json={"decision": "aceptada"}).status_code == 404
    assert client.get("/api/v1/transformations").json() == []


def test_format_without_real_generator_is_501_without_mock(client):
    res = client.post("/api/v1/transformations", json={"format": "audio", "analysis_id": _analyze(client)})
    assert res.status_code == 501
    assert res.json()["detail"]["code"] == "format_not_available"


# --- Generación con IA (generador de prueba) -----------------------------------------------------


def test_real_generator_result_and_metrics(client, monkeypatch):
    spec = transformation_service.FORMATS["mind_map"]
    monkeypatch.setitem(transformation_service.FORMATS, "mind_map", replace(
        spec, generate=_fake_generator(lambda _t: MindMapResult(root=MindMapNode(text="Ciclo del agua", children=[MindMapNode(text="Evaporación")])))
    ))
    ready = _wait(client, _create(client, "mind_map", mock=False, analysis_id=_analyze(client))["transformation_id"])
    assert ready["status"] == "ready"
    assert ready["result"] == {"root": {"text": "Ciclo del agua", "children": [{"text": "Evaporación", "children": []}]}}
    assert ready["metadata"]["provider"] == "groq"
    assert not any("mock" in w for w in ready["warnings"])

    rows = [r for r in user_activity_repository.list_recent("anon") if r["action"] == "transformation"]
    assert rows[0]["format"] == "mind_map"
    assert rows[0]["detail"] == "ready"
    assert rows[0]["mock"] == 0
    assert rows[0]["model"] == "llama-test"


def test_ai_failure_leaves_failed_with_reason(client, monkeypatch):
    async def failing(_text):
        raise LLMUnavailableError("Ninguna conexión de IA pudo responder.")

    monkeypatch.setitem(transformation_service.FORMATS, "mind_map",
                        replace(transformation_service.FORMATS["mind_map"], generate=failing))
    failed = _wait(client, _create(client, "mind_map", mock=False, analysis_id=_analyze(client))["transformation_id"])
    assert failed["status"] == "failed"
    assert failed["error"]["code"] == "ai_unavailable"
    assert failed["result"] is None

    res = client.post(f"/api/v1/transformations/{failed['transformation_id']}/decision", json={"decision": "aceptada"})
    assert res.status_code == 409


def test_cannot_decide_while_generating_and_active_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "TRANSFORMATION_MAX_ACTIVE_PER_USER", 1)
    spec = transformation_service.FORMATS["mind_map"]
    monkeypatch.setitem(transformation_service.FORMATS, "mind_map", replace(
        spec, generate=_fake_generator(lambda _t: MindMapResult(root=MindMapNode(text="x", children=[MindMapNode(text="y")])), delay=0.5)
    ))
    analysis_id = _analyze(client)
    created = _create(client, "mind_map", mock=False, analysis_id=analysis_id)

    res = client.post(f"/api/v1/transformations/{created['transformation_id']}/decision", json={"decision": "aceptada"})
    assert res.status_code == 409
    res = client.post("/api/v1/transformations", json={"format": "mind_map", "analysis_id": analysis_id})
    assert res.status_code == 429

    _wait(client, created["transformation_id"])
    assert _create(client, "mind_map", mock=False, analysis_id=analysis_id)["status"] == "pending"


def test_daily_limit_per_format_is_lower_for_audio(client, monkeypatch):
    monkeypatch.setattr(settings, "TRANSFORMATION_AUDIO_RATE_LIMIT_PER_DAY", 1)
    for fmt in ("audio", "mind_map"):
        spec = transformation_service.FORMATS[fmt]
        result = RESULT_SCHEMAS[fmt]
        sample = {"audio": {"script": "Hola"}, "mind_map": {"root": {"text": "x", "children": [{"text": "y"}]}}}[fmt]
        monkeypatch.setitem(transformation_service.FORMATS, fmt,
                            replace(spec, generate=_fake_generator(lambda _t, r=result, s=sample: r.model_validate(s))))
    analysis_id = _analyze(client)
    _wait(client, _create(client, "audio", mock=False, analysis_id=analysis_id)["transformation_id"])

    res = client.post("/api/v1/transformations", json={"format": "audio", "analysis_id": analysis_id})
    assert res.status_code == 429
    assert "Retry-After" in res.headers
    # Otro formato y el modo mock no cuentan para el límite del audio
    assert _create(client, "mind_map", mock=False, analysis_id=analysis_id)["status"] == "pending"
    assert _create(client, "audio", analysis_id=analysis_id)["status"] == "pending"


def test_rejected_requests_do_not_consume_the_daily_limit(client, monkeypatch):
    monkeypatch.setattr(settings, "TRANSFORMATION_AUDIO_RATE_LIMIT_PER_DAY", 1)
    spec = transformation_service.FORMATS["audio"]
    monkeypatch.setitem(transformation_service.FORMATS, "audio",
                        replace(spec, generate=_fake_generator(lambda _t: RESULT_SCHEMAS["audio"].model_validate({"script": "Hola"}))))
    # Material inexistente y archivo con datos personales: se rechazan antes de contar
    res = client.post("/api/v1/transformations", json={"format": "audio", "analysis_id": "no-existe"})
    assert res.status_code == 404
    file_id = _upload(client, _docx("Alumno: Juan Pérez, DNI 30.123.456.\n\n" + MATERIAL))
    res = client.post("/api/v1/transformations", json={"format": "audio", "file_id": file_id})
    assert res.status_code == 422

    assert _create(client, "audio", mock=False, analysis_id=_analyze(client))["status"] == "pending"


def test_file_source_keeps_the_whole_text_with_its_paragraphs(client, monkeypatch):
    received = []
    spec = transformation_service.FORMATS["mind_map"]
    monkeypatch.setitem(transformation_service.FORMATS, "mind_map", replace(
        spec, generate=_fake_generator(lambda t: received.append(t) or MindMapResult(root=MindMapNode(text="x", children=[MindMapNode(text="y")])))
    ))
    long_material = MATERIAL + "\n\n" + " ".join(["palabra"] * 3500) + "\n\nCierre de la unidad."
    file_id = _upload(client, _docx(long_material))
    _wait(client, _create(client, "mind_map", mock=False, file_id=file_id)["transformation_id"])

    assert "\n" in received[0].text  # sin el recorte del diagnóstico, que junta todo en una línea
    assert "Cierre de la unidad." in received[0].text


# --- Revisión docente ---------------------------------------------------------------


def test_accept_audio_registers_the_file_to_download_and_share(client):
    ready = _wait(client, _create(client, "audio", analysis_id=_analyze(client))["transformation_id"])
    res = client.post(f"/api/v1/transformations/{ready['transformation_id']}/decision", json={"decision": "aceptada"})
    assert res.status_code == 200
    decided = res.json()
    assert decided["review_status"] == "aceptada"
    assert decided["final_result"] == ready["result"]
    file_id = decided["file"]["file_id"]
    assert file_id

    download = client.get(f"/api/v1/files/{file_id}/download-url").json()
    assert download["file"]["kind"] == "export"
    assert _get_url(client, download["download"]["url"]).status_code == 200
    assert client.post(f"/api/v1/files/{file_id}/shares").status_code == 201

    again = client.post(f"/api/v1/transformations/{ready['transformation_id']}/decision", json={"decision": "descartada"})
    assert again.status_code == 409


def test_edit_validates_against_the_format_schema(client):
    ready = _wait(client, _create(client, "mind_map", analysis_id=_analyze(client))["transformation_id"])
    url = f"/api/v1/transformations/{ready['transformation_id']}/decision"

    res = client.post(url, json={"decision": "editada", "edited_result": {"root": {"text": ""}}})
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "invalid_edited_result"
    assert client.post(url, json={"decision": "editada"}).status_code == 422

    edited = {"root": {"text": "Ciclo del agua", "children": [{"text": "Evaporación", "children": []}]}}
    res = client.post(url, json={"decision": "editada", "edited_result": edited})
    assert res.status_code == 200
    data = res.json()
    assert data["review_status"] == "editada"
    assert data["edited_result"] == edited
    assert data["final_result"] == edited
    assert data["result"] == ready["result"]  # la propuesta original no se toca


def test_editing_audio_regenerates_the_file(client):
    ready = _wait(client, _create(client, "audio", analysis_id=_analyze(client))["transformation_id"])
    old_key = transformation_repository.get(ready["transformation_id"])["object_key"]
    res = client.post(f"/api/v1/transformations/{ready['transformation_id']}/decision",
                      json={"decision": "editada", "edited_result": {"script": "Guion corregido por el docente."}})
    assert res.status_code == 200
    new_key = transformation_repository.get(ready["transformation_id"])["object_key"]
    assert new_key != old_key
    storage = storage_service.get_storage()
    assert storage.head(old_key) is None
    assert storage.head(new_key) is not None
    assert res.json()["file"]["file_id"]


def test_editing_audio_does_not_leave_an_orphan_file_if_registering_fails(client, monkeypatch):
    ready = _wait(client, _create(client, "audio", analysis_id=_analyze(client))["transformation_id"])
    storage = storage_service.get_storage()
    put = storage.put
    stored = []
    monkeypatch.setattr(storage, "put", lambda key, *a, **k: stored.append(key) or put(key, *a, **k))

    def failing_create(**_kwargs):
        raise RuntimeError("la base no respondió")
    monkeypatch.setattr(transformation_service.files, "create", failing_create)

    with pytest.raises(RuntimeError):
        client.post(f"/api/v1/transformations/{ready['transformation_id']}/decision",
                    json={"decision": "editada", "edited_result": {"script": "Guion corregido."}})
    assert len(stored) == 1
    assert storage.head(stored[0]) is None


def test_discard_deletes_the_generated_file(client):
    ready = _wait(client, _create(client, "audio", analysis_id=_analyze(client))["transformation_id"])
    key = transformation_repository.get(ready["transformation_id"])["object_key"]
    res = client.post(f"/api/v1/transformations/{ready['transformation_id']}/decision", json={"decision": "descartada"})
    assert res.status_code == 200
    data = res.json()
    assert data["review_status"] == "descartada"
    assert data["final_result"] is None
    assert data["file"]["preview_url"] is None
    assert data["file"]["file_id"] is None
    assert storage_service.get_storage().head(key) is None
    assert transformation_repository.get(ready["transformation_id"])["object_key"] is None


# --- Reinicio, vencimiento y métricas --------------------------------------------------------


def test_interrupted_transformations_fail_on_startup(client):
    analysis_id = _analyze(client)
    created = _create(client, "mind_map", analysis_id=analysis_id)
    _wait(client, created["transformation_id"])
    # Simula una que estaba generándose cuando se reinició el proceso
    record = transformation_repository.get(created["transformation_id"])
    transformation_repository.create({**record, "id": "a-medias", "status": "processing",
                                      "payload_json": record["payload_json"].replace(created["transformation_id"], "a-medias")})
    assert transformation_repository.fail_interrupted(transformation_service.INTERRUPTED_MESSAGE) == 1
    data = client.get("/api/v1/transformations/a-medias").json()
    assert data["status"] == "failed"
    assert data["error"]["code"] == "interrupted"
    assert client.get(f"/api/v1/transformations/{created['transformation_id']}").json()["status"] == "ready"


def test_expired_transformations_are_cleaned_with_their_file(client):
    ready = _wait(client, _create(client, "audio", analysis_id=_analyze(client))["transformation_id"])
    key = transformation_repository.get(ready["transformation_id"])["object_key"]
    later = (datetime.now(timezone.utc) + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    assert transformation_service.cleanup_expired(later) == {"deleted": 1, "errors": 0}
    assert transformation_repository.get(ready["transformation_id"]) is None
    assert storage_service.get_storage().head(key) is None


def test_expires_with_the_source_material(client):
    analysis_id = _analyze(client)
    analysis = client.get(f"/api/v1/analysis/{analysis_id}").json()
    created = _create(client, "mind_map", analysis_id=analysis_id)
    assert created["expires_at"] <= analysis["expires_at"]


def test_metrics_by_user_and_format(client):
    analysis_id = _analyze(client)
    audio = _wait(client, _create(client, "audio", analysis_id=analysis_id)["transformation_id"])
    _wait(client, _create(client, "mind_map", analysis_id=analysis_id)["transformation_id"])
    client.post(f"/api/v1/transformations/{audio['transformation_id']}/decision", json={"decision": "aceptada"})

    detail = user_metrics_service.build_user_detail("anon")
    assert detail["transformations"]["audio"] == {"pedidas": 1, "fallidas": 0, "aceptada": 1}
    assert detail["transformations"]["mind_map"]["pedidas"] == 1
    stats = detail["user"]["all_time"]
    assert stats["transformations"] == 2
    assert stats["mock_transformations"] == 2
    assert stats["transformation_decisions"]["aceptada"] == 1

    product = admin_metrics_service.build_metrics()["product"]
    assert product["transformations"]["audio"]["aceptada"] == 1
    assert product["transformations"]["mind_map"]["pendiente"] == 1
    exports = admin_metrics_service.build_metrics()["files"]["exports"]
    assert exports["transformation"]["link"] == 1
