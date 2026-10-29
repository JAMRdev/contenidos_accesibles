"""
Interfaz común de los motores de texto a voz (Issue #121), como los adaptadores de services/llm/.

Cada proveedor devuelve PCM de 16 bits mono y traduce los errores de su SDK a los de
services/llm/base.py (ProviderRateLimitError, ProviderServerError): así tts_service rota entre
conexiones, y suma otro motor de fallback, sin conocer a ningún proveedor.
"""
from dataclasses import dataclass
from typing import Protocol

from app.services.llm.base import AIConnection, Usage


@dataclass(frozen=True)
class Synthesis:
    pcm: bytes  # PCM lineal de 16 bits, mono
    sample_rate: int
    usage: Usage | None = None


class TTSProvider(Protocol):
    async def synthesize(
        self, conn: AIConnection, text: str, *, model: str, voice: str, style: str, timeout_s: int,
    ) -> Synthesis: ...


class TTSUnavailableError(Exception):
    """Ninguna conexión pudo generar el audio: sin conexiones, sin cuota o con el proveedor caído."""


class TTSTextTooLongError(ValueError):
    """El texto supera TTS_MAX_CHARS."""
