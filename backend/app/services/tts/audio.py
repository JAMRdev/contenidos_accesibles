"""
Fragmentos de texto y codificación del audio (Issue #121).

- El texto se corta por párrafo y, si un párrafo es muy largo, por oración: con textos largos
  Gemini TTS omite partes (#120).
- El audio se guarda en MP3 mono (lameenc, sin depender de ffmpeg en Railway): ≈ 240 KB por minuto
  a 32 kbps, unas 3 veces menos que el WAV de 24 kHz, y se reproduce en cualquier navegador.
"""
import math
import re
import struct

import lameenc

MP3_BITRATE_KBPS = 32
CONTENT_TYPE = "audio/mpeg"
EXTENSION = ".mp3"
# Pausa entre fragmentos, para que no se peguen dos párrafos
PAUSE_S = 0.4

_SENTENCE_END = re.compile(r"(?<=[.!?…:;])\s+")


def _pack(pieces: list[str], limit: int, sep: str) -> list[str]:
    """Junta piezas seguidas mientras entren en `limit` (una pieza más larga queda sola)."""
    packed: list[str] = []
    for piece in pieces:
        if packed and len(packed[-1]) + len(sep) + len(piece) <= limit:
            packed[-1] += sep + piece
        else:
            packed.append(piece)
    return packed


def _units(paragraph: str, limit: int) -> list[str]:
    """El párrafo, o sus oraciones si no entra, o las palabras de una oración que no entra."""
    if len(paragraph) <= limit:
        return [paragraph]
    units: list[str] = []
    for sentence in _SENTENCE_END.split(paragraph):
        units += [sentence] if len(sentence) <= limit else _pack([w[:limit] for w in sentence.split()], limit, " ")
    return _pack(units, limit, " ")


def chunks(text: str, limit: int) -> list[str]:
    """Fragmentos de hasta `limit` caracteres que respetan los párrafos y, si no entran, las oraciones."""
    paragraphs = [" ".join(p.split()) for p in text.splitlines() if p.strip()]
    return _pack([u for p in paragraphs for u in _units(p, limit)], limit, "\n\n")


def silence(seconds: float, sample_rate: int) -> bytes:
    return b"\x00\x00" * int(seconds * sample_rate)


def duration_s(pcm: bytes, sample_rate: int) -> float:
    return len(pcm) / 2 / sample_rate


def to_mp3(pcm: bytes, sample_rate: int) -> bytes:
    """PCM de 16 bits mono a MP3. Es CPU: llamarla fuera del event loop (asyncio.to_thread)."""
    encoder = lameenc.Encoder()
    encoder.set_bit_rate(MP3_BITRATE_KBPS)
    encoder.set_in_sample_rate(sample_rate)
    encoder.set_out_sample_rate(sample_rate)  # si no, LAME baja a 22,05 kHz con bitrates bajos
    encoder.set_channels(1)
    encoder.set_quality(5)
    return bytes(encoder.encode(pcm) + encoder.flush())


def mock_pcm(seconds: float = 1.0, sample_rate: int = 24_000) -> bytes:
    """Un tono suave de 440 Hz para el modo mock: sin llamar al motor."""
    return b"".join(
        struct.pack("<h", int(3000 * math.sin(2 * math.pi * 440 * i / sample_rate)))
        for i in range(int(seconds * sample_rate))
    )
