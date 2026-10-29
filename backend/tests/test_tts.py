"""
Texto a voz (Issue #121): fragmentos, MP3, rotación entre conexiones Gemini con cooldowns propios,
métricas y el endpoint de prueba. Nunca llama al motor real: modo mock o un proveedor de prueba.
"""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from google.genai import errors

from app.core import rate_limit
from app.core.config import settings
from app.db import ai_usage_repository
from app.db import api_key_repository as repo
from app.db.database import init_db
from app.main import app
from app.services import admin_metrics_service, tts_service as tts_module
from app.services.llm.base import ProviderRateLimitError, ProviderServerError, Usage
from app.services.tts import audio
from app.services.tts.base import Synthesis, TTSTextTooLongError, TTSUnavailableError
from app.services.tts.gemini_provider import GeminiTTSProvider
from app.services.tts_service import tts_service

RATE = 24_000
PARAGRAPHS = [
    "El agua de los océanos se evapora por el calor del sol.",
    "El vapor se enfría, se condensa y forma las nubes.",
    "Cuando las gotas pesan, caen como lluvia o nieve.",
]


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "test.db"))
    init_db()
    tts_module.reset()
    rate_limit.reset()
    yield
    tts_module.reset()
    rate_limit.reset()


@pytest.fixture
def no_sleep(monkeypatch):
    waits: list[float] = []

    async def sleep(seconds):
        waits.append(seconds)
    monkeypatch.setattr(tts_module.asyncio, "sleep", sleep)
    return waits


def _gemini(label="Gemini 1", priority=10) -> int:
    return repo.create(label=label, raw_key=f"AIza_{label}", provider="gemini", base_url=None, model="gemini-2.5-flash",
                       priority=priority, extra_params={}, rpm_threshold=5, tpm_threshold=None, rpd_threshold=20,
                       tpd_threshold=None)


class FakeProvider:
    """Responde según una lista de resultados por llamada: segundos de audio o una excepción."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls: list[tuple[str, str, str]] = []

    async def synthesize(self, conn, text, *, model, voice, style, timeout_s):
        self.calls.append((conn.label, text, voice))
        answer = self.answers.pop(0) if self.answers else 0.5
        if isinstance(answer, Exception):
            raise answer
        return Synthesis(pcm=audio.silence(answer, RATE), sample_rate=RATE,
                         usage=Usage(prompt_tokens=10, output_tokens=100, total_tokens=110))


@pytest.fixture
def provider(monkeypatch):
    def install(*answers) -> FakeProvider:
        fake = FakeProvider(*answers)
        monkeypatch.setitem(tts_service._providers, "gemini", fake)
        return fake
    return install


def _run(text: str, **kwargs):
    return asyncio.run(tts_service.synthesize(text, **kwargs))


def _later(seconds: float) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=seconds)


# --- Fragmentos y audio --------------------------------------------------------------


def test_short_paragraphs_go_together_up_to_the_limit():
    text = "\n\n".join(PARAGRAPHS)
    assert audio.chunks(text, 1_000) == ["\n\n".join(PARAGRAPHS)]
    parts = audio.chunks(text, 110)
    assert parts == [f"{PARAGRAPHS[0]}\n\n{PARAGRAPHS[1]}", PARAGRAPHS[2]]


def test_long_paragraphs_are_cut_by_sentence_and_then_by_word():
    sentence = "Esta es una oración de prueba con varias palabras."
    long_paragraph = " ".join([sentence] * 10)
    endless = " ".join(["palabra"] * 60)  # una oración sin puntos
    parts = audio.chunks(f"{long_paragraph}\n{endless}", 120)
    assert all(len(p) <= 120 for p in parts)
    assert all(p.endswith(".") for p in parts[:4])  # el párrafo con puntos se corta en oraciones
    assert " ".join(" ".join(parts).split()) == " ".join(f"{long_paragraph} {endless}".split())  # no se pierde nada


def _level(pcm: bytes) -> tuple[float, int]:
    from array import array
    import math
    samples = array("h")
    samples.frombytes(pcm)
    voice = [s for s in samples if abs(s) > 500]
    return math.sqrt(sum(s * s for s in voice) / len(voice)), max(abs(s) for s in samples)


def _tone(amplitude: int, seconds: float = 1.0) -> bytes:
    import math
    import struct
    return b"".join(struct.pack("<h", int(amplitude * math.sin(2 * math.pi * 220 * i / RATE)))
                    for i in range(int(seconds * RATE)))


def test_quiet_and_loud_chunks_end_at_the_same_voice_level():
    quiet = _tone(2_000) + audio.silence(1.0, RATE)  # los silencios no cuentan para el nivel
    loud = _tone(9_000)
    for pcm in (quiet, loud):
        rms, peak = _level(audio.normalize(pcm))
        assert rms == pytest.approx(audio.TARGET_VOICE_RMS, rel=0.08)
        assert peak <= audio.PEAK_LIMIT
    assert len(audio.normalize(quiet)) == len(quiet)
    assert audio.normalize(audio.silence(0.5, RATE)) == audio.silence(0.5, RATE)


def test_a_chunk_is_not_pushed_past_the_peak_limit():
    spiky = _tone(1_000) + _tone(29_000, 0.01)  # voz baja con un pico: subirla saturaría
    rms, peak = _level(audio.normalize(spiky))
    assert peak <= audio.PEAK_LIMIT and rms < audio.TARGET_VOICE_RMS


def test_the_same_detailed_style_goes_with_every_chunk(db, provider, monkeypatch):
    monkeypatch.setattr(settings, "TTS_CHUNK_CHARS", 200)
    _gemini()
    styles = []

    class StyleSpy(FakeProvider):
        async def synthesize(self, conn, text, *, model, voice, style, timeout_s):
            styles.append(style)
            return await super().synthesize(conn, text, model=model, voice=voice, style=style, timeout_s=timeout_s)
    monkeypatch.setitem(tts_service._providers, "gemini", StyleSpy())
    _run("\n\n".join([PARAGRAPHS[0] * 3, PARAGRAPHS[1] * 3]))
    assert len(styles) == 2 and styles[0] == styles[1] == settings.TTS_STYLE
    for cue in ("rioplatense", "ritmo pausado y constante", "volumen parejo", "mismo ánimo", "exactamente igual"):
        assert cue in settings.TTS_STYLE


def test_mp3_is_smaller_than_wav():
    pcm = audio.mock_pcm(3.0)
    mp3 = audio.to_mp3(pcm, RATE)
    assert mp3[:2] == b"\xff\xf3" or mp3[:3] == b"ID3"
    assert len(mp3) < len(pcm) / 4


# --- Servicio ------------------------------------------------------------------------


def test_mock_does_not_call_the_engine_nor_log_usage(db, provider):
    fake = provider()
    result = _run("\n\n".join(PARAGRAPHS), mock=True)
    assert fake.calls == []
    assert result.provider == "mock" and result.model == "mock"
    assert result.content_type == "audio/mpeg" and result.extension == ".mp3"
    assert result.duration_s == 1.0  # un fragmento de 1 s
    assert ai_usage_repository.list_between(_later(-60), _later(60)) == []


def test_synthesizes_by_chunks_with_a_pause_and_reports_progress(db, provider, monkeypatch):
    monkeypatch.setattr(settings, "TTS_CHUNK_CHARS", 200)
    _gemini()
    fake = provider(1.0, 2.0)
    progress = []

    async def on_progress(done, total):
        progress.append((done, total))

    text = "\n\n".join([PARAGRAPHS[0] * 3, PARAGRAPHS[1] * 3])
    result = _run(text, on_progress=on_progress)
    assert len(fake.calls) == 2 and result.chunks == 2
    assert {voice for _, _, voice in fake.calls} == {"Kore"}
    assert result.duration_s == pytest.approx(3.0 + audio.PAUSE_S, abs=0.05)
    assert progress == [(1, 2), (2, 2)]
    assert result.usage.total_tokens == 220
    assert result.model == "gemini-2.5-flash-preview-tts"


def test_usage_goes_to_metrics_but_not_to_the_text_quota(db, provider):
    key_id = _gemini()
    provider(1.5)
    _run(PARAGRAPHS[0])
    rows = ai_usage_repository.list_between(_later(-60), _later(60))
    assert len(rows) == 1
    row = rows[0]
    assert (row["kind"], row["outcome"], row["model"]) == ("tts", "ok", "gemini-2.5-flash-preview-tts")
    assert row["characters"] == len(PARAGRAPHS[0]) and row["audio_seconds"] == 1.5
    # El modelo de TTS tiene otra cuota en Google: los contadores de la conexión no cambian
    row = next(r for r in repo.list_all_ordered_by_priority() if r["id"] == key_id)
    assert row["request_count"] == 0

    tts = admin_metrics_service.build_metrics("24h")["tts"]
    assert tts["totals"] == {"calls": 1, "characters": len(PARAGRAPHS[0]), "minutes": 0.0, "errors": 0}
    assert sum(tts["minutes"]) == pytest.approx(0.0)  # 1,5 s se redondea a 0,0 min


def test_rate_limited_connection_rotates_to_the_next(db, provider):
    first = _gemini("Gemini 1", 10)
    _gemini("Gemini 2", 20)
    fake = provider(ProviderRateLimitError("429 diario", _later(3_600)), 1.0, 1.0)
    _run(PARAGRAPHS[0])
    _run(PARAGRAPHS[1])
    assert [label for label, _, _ in fake.calls] == ["Gemini 1", "Gemini 2", "Gemini 2"]  # la primera quedó en pausa
    assert first in tts_module.cooldowns()
    outcomes = [r["outcome"] for r in ai_usage_repository.list_between(_later(-60), _later(60))]
    assert outcomes == ["rate_limited", "ok", "ok"]


def test_per_minute_limit_waits_and_retries(db, provider, monkeypatch):
    _gemini()
    fake = provider(ProviderRateLimitError("429 por minuto", _later(0.05)), 1.0)
    real_sleep, waits = asyncio.sleep, []

    async def sleep(seconds):
        waits.append(seconds)
        await real_sleep(seconds)
    monkeypatch.setattr(tts_module.asyncio, "sleep", sleep)

    result = _run(PARAGRAPHS[0])
    assert result.duration_s == 1.0 and len(fake.calls) == 2
    assert len(waits) == 1 and 0 < waits[0] <= tts_module.MAX_WAIT_S


def test_daily_limit_without_other_connections_fails(db, provider, no_sleep):
    _gemini()
    provider(ProviderRateLimitError("429 diario", _later(3_600)))
    with pytest.raises(TTSUnavailableError, match="Probá de nuevo más tarde"):
        _run(PARAGRAPHS[0])
    assert no_sleep == []


def test_server_errors_are_retried_and_then_fail_without_waiting_for_the_cooldown(db, provider, no_sleep):
    _gemini()
    fake = provider(*[ProviderServerError("503")] * 3)
    with pytest.raises(TTSUnavailableError, match="503"):
        _run(PARAGRAPHS[0])
    assert len(fake.calls) == 3
    assert no_sleep == [2, 4]  # solo las esperas de los reintentos


def test_without_gemini_connections_or_with_too_much_text(db, provider, monkeypatch):
    provider()
    with pytest.raises(TTSUnavailableError, match="Agregá una desde /admin"):
        _run(PARAGRAPHS[0])
    monkeypatch.setattr(settings, "TTS_MAX_CHARS", 100)
    with pytest.raises(TTSTextTooLongError):
        _run("x" * 101, mock=True)
    with pytest.raises(ValueError):
        _run("   ", mock=True)


# --- Adaptador de Gemini ---------------------------------------------------------------


def _gemini_response(data: bytes, mime_type: str):
    part = SimpleNamespace(inline_data=SimpleNamespace(data=data, mime_type=mime_type))
    return SimpleNamespace(
        candidates=[SimpleNamespace(content=SimpleNamespace(parts=[part]))],
        usage_metadata=SimpleNamespace(prompt_token_count=12, candidates_token_count=300, total_token_count=312),
    )


def _adapter_with(monkeypatch, answer):
    sent = {}

    async def generate_content(*, model, contents, config):
        sent.update(model=model, contents=contents, config=config)
        if isinstance(answer, Exception):
            raise answer
        return answer

    client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate_content)))
    adapter = GeminiTTSProvider()
    monkeypatch.setattr(adapter, "_client", lambda _conn: client)
    return adapter, sent


def _conn():
    return SimpleNamespace(id=1, label="Gemini", api_key="AIza", provider="gemini")


def test_gemini_adapter_sends_voice_and_style_and_reads_the_pcm(monkeypatch):
    pcm = audio.silence(0.5, RATE)
    adapter, sent = _adapter_with(monkeypatch, _gemini_response(pcm, "audio/L16;codec=pcm;rate=24000"))
    result = asyncio.run(adapter.synthesize(_conn(), "Hola, chicos.", model="gemini-2.5-flash-preview-tts",
                                            voice="Kore", style="Leé con acento rioplatense:", timeout_s=30))
    assert result.pcm == pcm and result.sample_rate == 24_000 and result.usage.total_tokens == 312
    assert sent["contents"] == "Leé con acento rioplatense:\n\nHola, chicos."
    config = sent["config"]
    assert config.response_modalities == ["AUDIO"]
    assert config.speech_config.voice_config.prebuilt_voice_config.voice_name == "Kore"
    assert config.http_options.timeout == 30_000


def test_gemini_adapter_strips_a_wav_header(monkeypatch):
    pcm = audio.silence(0.1, 16_000)
    wav = b"RIFF\x00\x00\x00\x00WAVEfmt " + b"\x00" * 20 + b"data\x00\x00\x00\x00" + pcm
    adapter, _ = _adapter_with(monkeypatch, _gemini_response(wav, "audio/wav;rate=16000"))
    result = asyncio.run(adapter.synthesize(_conn(), "x", model="m", voice="Kore", style="", timeout_s=30))
    assert result.pcm == pcm and result.sample_rate == 16_000


def test_gemini_adapter_maps_errors(monkeypatch):
    adapter, _ = _adapter_with(monkeypatch, errors.APIError(429, {"error": {"details": []}}))
    with pytest.raises(ProviderRateLimitError):
        asyncio.run(adapter.synthesize(_conn(), "x", model="m", voice="Kore", style="", timeout_s=30))
    adapter, _ = _adapter_with(monkeypatch, errors.APIError(503, {"error": {}}))
    with pytest.raises(ProviderServerError):
        asyncio.run(adapter.synthesize(_conn(), "x", model="m", voice="Kore", style="", timeout_s=30))
    adapter, _ = _adapter_with(monkeypatch, SimpleNamespace(candidates=[], usage_metadata=None))
    with pytest.raises(ProviderServerError, match="sin audio"):
        asyncio.run(adapter.synthesize(_conn(), "x", model="m", voice="Kore", style="", timeout_s=30))


# --- Endpoint de prueba ------------------------------------------------------------------


def test_test_endpoint_returns_the_mp3(db):
    with TestClient(app) as client:
        res = client.post("/api/v1/ai/test-tts?mock=true", json={"text": PARAGRAPHS[0]})
        assert res.status_code == 200
        assert res.headers["content-type"] == "audio/mpeg"
        assert res.headers["x-audio-duration"] == "1.0" and res.headers["x-tts-model"] == "mock"
        assert client.post("/api/v1/ai/test-tts?mock=true", json={"text": "x" * 1_001}).status_code == 422
        # Sin conexiones de Gemini, sin mock
        res = client.post("/api/v1/ai/test-tts", json={"text": PARAGRAPHS[0]})
        assert res.status_code == 503 and "/admin" in res.json()["detail"]
