"""
Texto a voz (Issue #121): genera un MP3 en español a partir de un texto.

- Motor (#120): Gemini 2.5 Flash TTS, voz Kore, acento rioplatense pedido en la instrucción de
  estilo. Usa las conexiones Gemini de /admin en orden de prioridad.
- El modelo de TTS tiene otra cuota en Google que el de texto: no suma a los contadores de la
  conexión (RPM, RPD) ni la marca agotada para el análisis. Lleva sus propios cooldowns.
- El texto se genera por fragmentos (párrafos u oraciones) y se une con una pausa corta.
- Ante un 429 por minuto con todas las conexiones en cooldown, espera hasta MAX_WAIT_S y sigue;
  con la cuota diaria agotada o el proveedor caído, falla con TTSUnavailableError. Sin fallback a
  otro motor por ahora: TTSProvider (services/tts/base.py) deja lugar para sumarlo.
- La síntesis es una llamada de red asíncrona y la codificación a MP3 corre en un hilo: el resto
  de la API sigue respondiendo mientras se genera.
- Cada intento queda en ai_usage_events (kind 'tts', con caracteres y segundos de audio) para las
  métricas de /admin. Nunca se guarda el texto.
"""
import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from app.core.config import settings
from app.db import ai_usage_repository
from app.db import user_activity_repository as user_activity
from app.services import key_rotation_service
from app.services.llm.base import AIConnection, ProviderRateLimitError, ProviderServerError, Usage
from app.services.tts import audio
from app.services.tts.base import Synthesis, TTSProvider, TTSTextTooLongError, TTSUnavailableError
from app.services.tts.gemini_provider import GeminiTTSProvider

logger = logging.getLogger(__name__)

MAX_SERVER_RETRIES = 2
SERVER_ERROR_COOLDOWN_S = 60
CONFIG_ERROR_COOLDOWN_S = 300
# Espera máxima por un 429 por minuto cuando ninguna conexión está libre, y cuántas veces por fragmento
MAX_WAIT_S = 65
MAX_WAITS_PER_CHUNK = 3
MOCK_SECONDS_PER_CHUNK = 1.0

# Cooldowns del TTS por id de conexión (en memoria, como los del texto en key_rotation_service)
_cooldowns: dict[int, datetime] = {}

Progress = Callable[[int, int], Awaitable[None]]


@dataclass(frozen=True)
class AudioFile:
    data: bytes
    content_type: str
    extension: str
    duration_s: float
    characters: int
    chunks: int
    provider: str
    model: str
    voice: str
    latency_ms: int
    usage: Usage


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cooldown(conn_id: int, until: datetime) -> None:
    _cooldowns[conn_id] = until


def _free(conn_id: int) -> bool:
    until = _cooldowns.get(conn_id)
    if until is not None and _now() >= until:
        del _cooldowns[conn_id]
        return True
    return until is None


def cooldowns() -> dict[int, datetime]:
    """Conexiones fuera de rotación para el TTS (para la sección Estado de /admin)."""
    return {conn_id: until for conn_id, until in list(_cooldowns.items()) if not _free(conn_id)}


def reset() -> None:
    """Vacía los cooldowns (para tests)."""
    _cooldowns.clear()


def _log(conn: AIConnection, outcome: str, started: float, *, characters: int = 0,
         audio_seconds: float = 0, usage: Usage | None = None) -> None:
    latency_ms = int((time.perf_counter() - started) * 1000)
    try:
        ai_usage_repository.record(
            connection_id=conn.id, connection_label=conn.label, provider=conn.provider, model=settings.TTS_MODEL,
            kind="tts", outcome=outcome, latency_ms=latency_ms,
            prompt_tokens=usage.prompt_tokens if usage else 0, output_tokens=usage.output_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0, characters=characters, audio_seconds=audio_seconds,
        )
    except Exception:
        logger.exception("No se pudo registrar el uso de TTS de la conexión '%s'", conn.label)
    user_activity.record_for_caller(
        "ai_call", format="tts", detail=outcome, provider=conn.provider, model=settings.TTS_MODEL,
        latency_ms=latency_ms, tokens=usage.total_tokens if usage else 0,
    )


class TTSService:
    def __init__(self):
        self._providers: dict[str, TTSProvider] = {"gemini": GeminiTTSProvider()}

    async def synthesize(self, text: str, *, mock: bool = False, on_progress: Progress | None = None) -> AudioFile:
        """
        MP3 del texto completo. `on_progress(hechos, total)` se llama después de cada fragmento.
        Errores: ValueError (texto vacío), TTSTextTooLongError, TTSUnavailableError.
        """
        text = text.strip()
        if not text:
            raise ValueError("El texto para convertir en audio está vacío.")
        if len(text) > settings.TTS_MAX_CHARS:
            raise TTSTextTooLongError(
                f"El texto tiene {len(text):,} caracteres y el máximo para un audio es {settings.TTS_MAX_CHARS:,}."
                .replace(",", ".")
            )
        parts = audio.chunks(text, settings.TTS_CHUNK_CHARS)
        started = time.perf_counter()
        usage = Usage()
        sample_rate = None
        pcm: list[bytes] = []
        provider = "mock" if mock else settings.TTS_PROVIDER
        for i, part in enumerate(parts):
            if mock:
                synthesis = Synthesis(pcm=audio.mock_pcm(MOCK_SECONDS_PER_CHUNK), sample_rate=24_000)
            else:
                synthesis = await self._synthesize_chunk(part)
            if sample_rate is None:
                sample_rate = synthesis.sample_rate
            elif synthesis.sample_rate != sample_rate:  # no pasa con un solo motor: por las dudas
                raise TTSUnavailableError("El motor devolvió fragmentos con frecuencias de muestreo distintas.")
            if pcm:
                pcm.append(audio.silence(audio.PAUSE_S, sample_rate))
            pcm.append(synthesis.pcm)
            if synthesis.usage:
                usage.prompt_tokens += synthesis.usage.prompt_tokens
                usage.output_tokens += synthesis.usage.output_tokens
                usage.total_tokens += synthesis.usage.total_tokens
            if on_progress:
                await on_progress(i + 1, len(parts))

        joined = b"".join(pcm)
        data = await asyncio.to_thread(audio.to_mp3, joined, sample_rate)
        return AudioFile(
            data=data, content_type=audio.CONTENT_TYPE, extension=audio.EXTENSION,
            duration_s=round(audio.duration_s(joined, sample_rate), 1), characters=len(text), chunks=len(parts),
            provider=provider, model="mock" if mock else settings.TTS_MODEL, voice=settings.TTS_VOICE,
            latency_ms=int((time.perf_counter() - started) * 1000), usage=usage,
        )

    async def _synthesize_chunk(self, text: str) -> Synthesis:
        provider = self._providers[settings.TTS_PROVIDER]
        last_error: Exception | None = None
        waits = 0
        while True:
            conns = await asyncio.to_thread(key_rotation_service.list_enabled_connections, settings.TTS_PROVIDER)
            if not conns:
                raise TTSUnavailableError(
                    "No hay ninguna conexión de Gemini habilitada para generar audio. Agregá una desde /admin."
                )
            free = [c for c in conns if _free(c.id)]
            if not free:
                wait = min((_cooldowns[c.id] - _now()).total_seconds() for c in conns)
                # Se espera solo por un límite por minuto: con el proveedor caído o mal configurado, se informa ya
                rate_limited = last_error is None or isinstance(last_error, ProviderRateLimitError)
                if wait > MAX_WAIT_S or waits >= MAX_WAITS_PER_CHUNK or not rate_limited:
                    detail = f" Último error: {last_error}" if last_error else ""
                    raise TTSUnavailableError(
                        "Ninguna conexión de Gemini puede generar audio ahora: alcanzaron su límite o el "
                        f"servicio no responde. Probá de nuevo más tarde.{detail}"
                    )
                waits += 1
                await asyncio.sleep(max(wait, 0))
                continue

            conn = free[0]
            for attempt in range(MAX_SERVER_RETRIES + 1):
                attempt_started = time.perf_counter()
                try:
                    synthesis = await provider.synthesize(
                        conn, text, model=settings.TTS_MODEL, voice=settings.TTS_VOICE, style=settings.TTS_STYLE,
                        timeout_s=settings.TTS_TIMEOUT_S,
                    )
                except ProviderRateLimitError as e:
                    await asyncio.to_thread(_log, conn, "rate_limited", attempt_started)
                    last_error = e
                    _cooldown(conn.id, e.until)
                    break
                except ProviderServerError as e:
                    await asyncio.to_thread(_log, conn, "server_error", attempt_started)
                    last_error = e
                    if attempt < MAX_SERVER_RETRIES:
                        await asyncio.sleep(2 ** (attempt + 1))
                        continue
                    _cooldown(conn.id, _now() + timedelta(seconds=SERVER_ERROR_COOLDOWN_S))
                    break
                except Exception as e:
                    # Key inválida, modelo inexistente o sin acceso al modelo de TTS
                    await asyncio.to_thread(_log, conn, "error", attempt_started)
                    logger.warning("Conexión '%s' falló al generar audio: %s", conn.label, e)
                    last_error = e
                    _cooldown(conn.id, _now() + timedelta(seconds=CONFIG_ERROR_COOLDOWN_S))
                    break
                await asyncio.to_thread(
                    _log, conn, "ok", attempt_started, characters=len(text),
                    audio_seconds=audio.duration_s(synthesis.pcm, synthesis.sample_rate), usage=synthesis.usage,
                )
                return synthesis


tts_service = TTSService()
