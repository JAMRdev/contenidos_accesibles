"""
Gemini TTS (Issue #120: gemini-2.5-flash-preview-tts, voz Kore, acento rioplatense pedido en la
instrucción de estilo). Devuelve PCM de 16 bits mono a 24 kHz (lo indica el mimeType).
"""
import re

import httpx
from google import genai
from google.genai import errors, types

from app.services.llm.base import AIConnection, ProviderRateLimitError, ProviderServerError, Usage
from app.services.llm.gemini_adapter import _rate_limit_until
from app.services.tts.base import Synthesis

DEFAULT_SAMPLE_RATE = 24_000
_RATE = re.compile(r"rate=(\d+)")


class GeminiTTSProvider:
    def __init__(self):
        # Como en GeminiAdapter: si se edita la key desde /admin, se crea un cliente nuevo
        self._clients: dict[tuple[int, str], genai.Client] = {}

    def _client(self, conn: AIConnection) -> genai.Client:
        cache_key = (conn.id, conn.api_key)
        if cache_key not in self._clients:
            self._clients[cache_key] = genai.Client(api_key=conn.api_key)
        return self._clients[cache_key]

    async def synthesize(
        self, conn: AIConnection, text: str, *, model: str, voice: str, style: str, timeout_s: int,
    ) -> Synthesis:
        config = types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice))
            ),
            # Los reintentos los maneja tts_service, que también rota de conexión
            http_options=types.HttpOptions(timeout=timeout_s * 1000),
        )
        prompt = f"{style}\n\n{text}" if style else text
        try:
            response = await self._client(conn).aio.models.generate_content(model=model, contents=prompt, config=config)
        except errors.APIError as e:
            if e.code == 429:
                raise ProviderRateLimitError(str(e), _rate_limit_until(e)) from e
            if e.code and e.code >= 500:
                raise ProviderServerError(str(e)) from e
            raise
        except httpx.TransportError as e:  # incluye el timeout
            raise ProviderServerError(f"Error de red con Gemini TTS: {e}") from e

        part = next(
            (p for c in response.candidates or [] for p in (c.content.parts if c.content else []) or [] if p.inline_data),
            None,
        )
        if part is None or not part.inline_data.data:
            raise ProviderServerError("Gemini TTS respondió sin audio.")
        pcm = part.inline_data.data
        if pcm[:4] == b"RIFF":  # algunos modelos lo mandan como WAV
            pcm = pcm[pcm.index(b"data") + 8:]
        match = _RATE.search(part.inline_data.mime_type or "")
        meta = response.usage_metadata
        usage = Usage(
            prompt_tokens=meta.prompt_token_count or 0,
            output_tokens=meta.candidates_token_count or 0,
            total_tokens=meta.total_token_count or 0,
        ) if meta is not None else None
        return Synthesis(pcm=pcm, sample_rate=int(match.group(1)) if match else DEFAULT_SAMPLE_RATE, usage=usage)
