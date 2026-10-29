"""
Audio (Issue #122): guion para escuchar, revisión antes de generar el audio, síntesis por sección
(MP3 completo y capítulos), transcripción, progreso, falla y reintento. Nunca llama a la IA ni al
motor de TTS reales: modo mock, respuestas fijas o un proveedor de prueba.
"""
import asyncio
import io
import time
import zipfile
from dataclasses import replace
from unittest.mock import AsyncMock, patch
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core import rate_limit
from app.core.config import settings
from app.db import api_key_repository as repo
from app.db import transformation_repository
from app.db.database import init_db
from app.main import app
from app.schemas.adaptation import AdaptationMetadata
from app.schemas.transformation import AudioAIOutput, AudioResult, NoImagesResult
from app.services import storage_service, transformation_service
from app.services import tts_service as tts_module
from app.services.llm.base import LLMInvalidResponseError, Usage
from app.services.llm_service import StructuredResult
from app.services.storage_service import LocalStorage
from app.services.transformations import audio
from app.services.transformations.base import Generation, SourceMaterial
from app.services.tts import audio as tts_audio
from app.services.tts.base import Synthesis, TTSUnavailableError
from app.services.tts_service import tts_service

MATERIAL = (
    "Unidad 3: El ciclo del agua\n\n"
    "El agua de los océanos se evapora por el calor del sol y sube a la atmósfera.\n\n"
    "El vapor se enfría, se condensa y forma las nubes.\n\n"
    "Cuando las gotas pesan, caen como lluvia o nieve y vuelven a los ríos y océanos."
)
SCRIPT = {
    "title": "El ciclo del agua",
    "sections": [
        {"title": "Evaporación", "text": "El sol calienta el agua de los océanos y el vapor sube."},
        {"title": "Condensación y lluvia", "text": "El vapor se enfría, forma nubes y cae como lluvia."},
    ],
}
AI_METADATA = AdaptationMetadata(provider="groq", model="llama-test", connection="prueba", latency_ms=5,
                                 prompt_tokens=10, output_tokens=5, total_tokens=15)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "FILE_CLEANUP_INTERVAL_MINUTES", 0)
    init_db()
    storage_service.reset_storage(LocalStorage(str(tmp_path / "storage"), b"secreto-de-prueba"))
    rate_limit.reset()
    tts_module.reset()
    with TestClient(app) as c:
        yield c
    storage_service.reset_storage()
    rate_limit.reset()
    tts_module.reset()


class FakeTTS:
    """Motor de prueba: medio segundo de silencio por fragmento, o falla si `fail` está activo."""

    def __init__(self):
        self.fail = False
        self.texts: list[str] = []

    async def synthesize(self, conn, text, *, model, voice, style, timeout_s):
        if self.fail:
            raise RuntimeError("modelo de TTS no disponible para esta key")
        self.texts.append(text)
        return Synthesis(pcm=tts_audio.silence(0.5, 24_000), sample_rate=24_000, usage=Usage(total_tokens=50))


@pytest.fixture
def real(monkeypatch):
    """Guion fijo como si viniera de la IA y un motor de TTS de prueba con una conexión Gemini."""
    repo.create(label="Gemini", raw_key="AIza_prueba", provider="gemini", base_url=None, model="gemini-2.5-flash",
                priority=10, extra_params={}, rpm_threshold=None, tpm_threshold=None, rpd_threshold=None,
                tpd_threshold=None)
    spec = transformation_service.FORMATS["audio"]

    async def generate(_material):
        return Generation(result=AudioResult.model_validate(SCRIPT), metadata=AI_METADATA)
    monkeypatch.setitem(transformation_service.FORMATS, "audio", replace(spec, generate=generate))
    fake = FakeTTS()
    monkeypatch.setitem(tts_service._providers, "gemini", fake)
    return fake


def _create(client, *, mock: bool) -> dict:
    analysis_id = client.post("/api/v1/analysis/sample?mock=true", json={"text": MATERIAL}).json()["analysis_id"]
    res = client.post(f"/api/v1/transformations?mock={str(mock).lower()}", json={"format": "audio", "analysis_id": analysis_id})
    assert res.status_code == 202, res.text
    return _wait(client, res.json()["transformation_id"])


def _wait(client, transformation_id: str) -> dict:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        data = client.get(f"/api/v1/transformations/{transformation_id}").json()
        if data["status"] in ("ready", "failed"):
            return data
        time.sleep(0.02)
    raise AssertionError("La transformación no terminó a tiempo")


def _decide(client, tid: str, decision: str, edited: dict | None = None) -> dict:
    body = {"decision": decision, **({"edited_result": edited} if edited else {})}
    res = client.post(f"/api/v1/transformations/{tid}/decision", json=body)
    assert res.status_code == 200, res.text
    return res.json()


def _get_url(client, url: str):
    parts = urlsplit(url)
    return client.get(f"{parts.path}?{parts.query}")


def _download(client, file_id: str) -> bytes:
    url = client.get(f"/api/v1/files/{file_id}/download-url").json()["download"]["url"]
    return _get_url(client, url).content


# --- Esquema y salida de la IA ----------------------------------------------------------


def test_each_section_is_read_with_its_title_and_the_audio_title_goes_first():
    script = AudioResult.model_validate({**SCRIPT, "title": "  El ciclo del agua  "})
    texts = script.section_texts()
    assert texts[0] == "El ciclo del agua.\n\nEvaporación.\n\nEl sol calienta el agua de los océanos y el vapor sube."
    assert texts[1].startswith("Condensación y lluvia.\n\n")
    assert AudioResult.model_validate({**SCRIPT, "title": "¿Qué es el agua?"}).section_texts()[0].startswith("¿Qué es el agua?\n\n")


def test_script_length_is_the_audio_duration_limit(monkeypatch):
    monkeypatch.setattr(settings, "TTS_MAX_CHARS", 150)
    with pytest.raises(ValidationError, match="máximo para un audio"):
        AudioResult.model_validate(SCRIPT)
    with pytest.raises(ValidationError):
        AudioResult.model_validate({"title": "x", "sections": [{"title": "   ", "text": "algo"}]})
    with pytest.raises(ValidationError):
        AudioResult.model_validate({"title": "x", "sections": []})


def test_ai_output_is_clipped_merged_and_cut_to_the_limit(monkeypatch):
    many = {"title": "T" * 200, "sections": [{"title": f"Parte {i}", "text": f"Texto {i}."} for i in range(15)]
            + [{"title": "  ", "text": "sin título"}]}
    result, warnings = audio.from_ai(AudioAIOutput.model_validate(many))
    assert len(result.title) <= 120 and result.title.endswith("…")
    assert len(result.sections) == 12
    assert "Parte 14. Texto 14." in result.sections[-1].text  # las últimas se juntaron, sin perderlas
    assert "se juntaron" in warnings[0]

    monkeypatch.setattr(settings, "TTS_MAX_CHARS", 100)
    result, warnings = audio.from_ai(AudioAIOutput.model_validate(SCRIPT))
    assert [s.title for s in result.sections] == ["Evaporación"]
    assert "se omitieron las últimas 1 secciones" in warnings[0]

    monkeypatch.setattr(settings, "TTS_MAX_CHARS", 60)  # ni la primera sección entra: se corta su texto
    result, warnings = audio.from_ai(AudioAIOutput.model_validate(SCRIPT))
    assert result.characters() <= 60 and result.sections[0].text.endswith("…")
    assert "se acortó" in warnings[0]

    with pytest.raises(LLMInvalidResponseError):
        audio.from_ai(AudioAIOutput(title="Tema", sections=[]))


def _ai_result(output: dict, tokens: int = 100) -> StructuredResult:
    return StructuredResult(parsed=AudioAIOutput.model_validate(output), provider="gemini", model="gemini-test",
                            connection_label="Gemini", latency_ms=800,
                            usage=Usage(prompt_tokens=tokens, output_tokens=tokens, total_tokens=2 * tokens))


def test_generator_asks_for_a_script_to_listen_to():
    with patch("app.services.llm_service.llm_service.generate_structured", AsyncMock(return_value=_ai_result(SCRIPT))) as llm:
        generation = asyncio.run(audio.generate(SourceMaterial(text=MATERIAL)))
    kwargs = llm.await_args.kwargs
    assert kwargs["schema"] is AudioAIOutput
    assert "se evapora" in kwargs["contents"]
    assert "como se dicen" in kwargs["system_instruction"] and "voseo" in kwargs["system_instruction"]
    assert generation.result.title == "El ciclo del agua"
    assert generation.metadata.total_tokens == 200


def test_images_are_described_first_with_the_no_images_logic():
    described = Generation(
        result=NoImagesResult(text=f"{MATERIAL}\n\n[Imagen 1 · Página 1]\nGráfico de la lluvia por estación."),
        metadata=AI_METADATA.model_copy(update={"total_tokens": 300, "latency_ms": 900}),
        warnings=["Se omitieron 1 imagen decorativa."],
    )
    material = SourceMaterial(text=MATERIAL, alt_texts=[object()])  # el contenido lo reemplaza no_images
    with patch("app.services.transformations.no_images.generate", AsyncMock(return_value=described)), \
            patch("app.services.llm_service.llm_service.generate_structured", AsyncMock(return_value=_ai_result(SCRIPT))) as llm:
        generation = asyncio.run(audio.generate(material))
    assert "Gráfico de la lluvia por estación" in llm.await_args.kwargs["contents"]
    assert generation.metadata.total_tokens == 500  # descripción de imágenes + guion
    assert generation.metadata.latency_ms == 1_700
    assert "Se omitieron 1 imagen decorativa." in generation.warnings


# --- Circuito completo (mock) -------------------------------------------------------------


def test_script_first_then_audio_and_chapters_after_accepting(client):
    ready = _create(client, mock=True)
    assert ready["file"] is None and ready["extra_files"] == []  # nada de audio hasta aceptar el guion
    assert ready["result"]["title"] == "Unidad 3: El ciclo del agua"
    assert ready["views"]["transcript"].startswith("# Unidad 3: El ciclo del agua\n\n## 1. Parte 1")

    tid = ready["transformation_id"]
    decided = _decide(client, tid, "aceptada")
    assert decided["status"] == "processing" and decided["review_status"] == "aceptada"
    done = _wait(client, tid)
    assert done["status"] == "ready" and done["progress"] is None

    file = done["file"]
    assert file["content_type"] == "audio/mpeg" and file["filename"].endswith(".mp3") and file["file_id"]
    assert _get_url(client, file["preview_url"]).status_code == 200
    mp3 = _download(client, file["file_id"])
    assert mp3[:2] == b"\xff\xf3" or mp3[:3] == b"ID3"

    (chapters,) = done["extra_files"]
    assert chapters["content_type"] == "application/zip" and chapters["filename"].startswith("audio_capitulos_")
    with zipfile.ZipFile(io.BytesIO(_download(client, chapters["file_id"]))) as zf:
        names = zf.namelist()
    assert names == [f"{i:02d}_Parte_{i}.mp3" for i in range(1, len(ready["result"]["sections"]) + 1)]
    assert any("capítulos" in w for w in done["warnings"])
    assert client.post(f"/api/v1/files/{file['file_id']}/shares").status_code == 201


def test_transcript_exports_and_edited_script(client):
    ready = _create(client, mock=True)
    tid = ready["transformation_id"]
    done = _decide(client, tid, "editada", SCRIPT)
    assert done["views"]["transcript"].startswith("# El ciclo del agua\n\n## 1. Evaporación")
    done = _wait(client, tid)
    with zipfile.ZipFile(io.BytesIO(_download(client, done["extra_files"][0]["file_id"]))) as zf:
        assert zf.namelist() == ["01_Evaporacion.mp3", "02_Condensacion_y_lluvia.mp3"]

    md = client.get(f"/api/v1/transformations/{tid}/export?format=md")
    assert md.status_code == 200 and md.text.startswith("# El ciclo del agua")
    txt = client.get(f"/api/v1/transformations/{tid}/export?format=txt")
    assert "1. Evaporación" in txt.text and "El sol calienta" in txt.text


def test_too_long_edit_is_rejected_before_spending_tts(client, monkeypatch):
    ready = _create(client, mock=True)
    monkeypatch.setattr(settings, "TTS_MAX_CHARS", 100)
    res = client.post(f"/api/v1/transformations/{ready['transformation_id']}/decision",
                      json={"decision": "editada", "edited_result": SCRIPT})
    assert res.status_code == 422
    assert res.json()["detail"]["code"] == "invalid_edited_result"


def test_discarding_the_script_generates_no_audio(client, real):
    ready = _create(client, mock=False)
    decided = _decide(client, ready["transformation_id"], "descartada")
    assert decided["status"] == "ready" and decided["file"] is None
    assert real.texts == []


# --- Con el motor de prueba: progreso, falla y reintento --------------------------------------


def test_audio_reads_each_section_and_reports_progress(client, real, monkeypatch):
    progress = []
    save = transformation_service._save

    def spy(previous, updated, owner, key):
        if updated.progress:
            progress.append((updated.progress.done, updated.progress.total))
        return save(previous, updated, owner, key)
    monkeypatch.setattr(transformation_service, "_save", spy)

    ready = _create(client, mock=False)
    _decide(client, ready["transformation_id"], "aceptada")
    done = _wait(client, ready["transformation_id"])
    assert done["status"] == "ready", done.get("error")
    assert real.texts[0].startswith("El ciclo del agua.\n\nEvaporación.")
    assert real.texts[1].startswith("Condensación y lluvia.")
    assert progress == [(1, 2), (2, 2)]


def test_tts_failure_keeps_the_script_and_can_be_retried(client, real):
    real.fail = True
    ready = _create(client, mock=False)
    tid = ready["transformation_id"]
    _decide(client, tid, "aceptada")
    failed = _wait(client, tid)
    assert failed["status"] == "failed" and failed["review_status"] == "aceptada"
    assert failed["error"]["code"] == "tts_unavailable"
    assert failed["final_result"] == SCRIPT  # el guion aceptado no se pierde
    assert failed["file"] is None
    assert transformation_repository.get(tid)["object_key"] is None

    real.fail = False
    tts_module.reset()  # la conexión quedó en pausa por el error
    res = client.post(f"/api/v1/transformations/{tid}/file")
    assert res.status_code == 202 and res.json()["status"] == "processing"
    done = _wait(client, tid)
    assert done["status"] == "ready" and done["error"] is None and done["file"]["file_id"]

    assert client.post(f"/api/v1/transformations/{tid}/file").status_code == 409  # ya no falló


def test_retry_only_for_a_failed_audio_of_an_accepted_script(client, real):
    ready = _create(client, mock=False)
    tid = ready["transformation_id"]
    assert client.post(f"/api/v1/transformations/{tid}/file").status_code == 409  # el guion no se aceptó
    mind_map = client.post("/api/v1/transformations?mock=true", json={
        "format": "mind_map",
        "analysis_id": client.post("/api/v1/analysis/sample?mock=true", json={"text": MATERIAL}).json()["analysis_id"],
    }).json()
    assert client.post(f"/api/v1/transformations/{mind_map['transformation_id']}/file").status_code == 409
    assert client.post("/api/v1/transformations/no-existe/file").status_code == 404


def test_audio_interrupted_by_a_restart_can_be_retried(client, real):
    ready = _create(client, mock=False)
    tid = ready["transformation_id"]
    record = transformation_repository.get(tid)
    # Como si el proceso se hubiera reiniciado con el audio a medias
    payload = record["payload_json"].replace('"review_status":"pendiente"', '"review_status":"aceptada"')
    payload = payload.replace('"status":"ready"', '"status":"processing"')
    payload = payload.replace('"final_result":null', f'"final_result":{AudioResult.model_validate(SCRIPT).model_dump_json()}')
    transformation_repository.delete(tid)
    transformation_repository.create({**record, "status": "processing", "review_status": "aceptada", "payload_json": payload})
    assert transformation_repository.fail_interrupted(transformation_service.INTERRUPTED_MESSAGE) == 1

    assert client.get(f"/api/v1/transformations/{tid}").json()["error"]["code"] == "interrupted"
    assert client.post(f"/api/v1/transformations/{tid}/file").status_code == 202
    assert _wait(client, tid)["status"] == "ready"


def test_accepting_counts_against_transformations_in_progress(client, real, monkeypatch):
    monkeypatch.setattr(settings, "TRANSFORMATION_MAX_ACTIVE_PER_USER", 1)
    ready = _create(client, mock=False)
    transformation_service._active["anon"] = 1  # otra generación en curso
    try:
        res = client.post(f"/api/v1/transformations/{ready['transformation_id']}/decision", json={"decision": "aceptada"})
        assert res.status_code == 429
        assert transformation_repository.get(ready["transformation_id"])["review_status"] == "pendiente"
    finally:
        transformation_service._active.pop("anon", None)
