"""
Historial de llamadas a la IA para las métricas del panel de administración.

Cada intento contra un proveedor (exitoso o no) queda como una fila con la
conexión, el resultado, los tokens y la latencia; las de texto a voz (kind 'tts', Issue #121),
también los caracteres y los segundos de audio. Nunca se guarda el material enviado ni la
respuesta del modelo. Las filas se borran al superar
AI_USAGE_RETENTION_DAYS.
"""
from datetime import datetime, timedelta, timezone

from app.core.config import settings
from app.db.database import get_connection

OUTCOMES = ("ok", "rate_limited", "server_error", "error")
KINDS = ("text", "vision", "tts")

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%S"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def to_timestamp(moment: datetime) -> str:
    """Formato UTC que se guarda en created_at (ordenable como texto)."""
    return moment.astimezone(timezone.utc).strftime(_TIMESTAMP_FORMAT)


def record(
    *,
    connection_id: int | None,
    connection_label: str,
    provider: str,
    model: str,
    kind: str,
    outcome: str,
    latency_ms: int,
    prompt_tokens: int = 0,
    output_tokens: int = 0,
    total_tokens: int = 0,
    characters: int = 0,
    audio_seconds: float = 0,
    created_at: datetime | None = None,
) -> None:
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome inválido: {outcome!r}")
    if kind not in KINDS:
        raise ValueError(f"kind inválido: {kind!r}")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO ai_usage_events (
                created_at, connection_id, connection_label, provider, model, kind, outcome,
                prompt_tokens, output_tokens, total_tokens, latency_ms, characters, audio_seconds
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                to_timestamp(created_at or _now()),
                connection_id,
                connection_label,
                provider,
                model,
                kind,
                outcome,
                prompt_tokens,
                output_tokens,
                total_tokens,
                latency_ms,
                characters,
                round(audio_seconds, 1),
            ),
        )


def delete_expired(retention_days: int | None = None) -> int:
    days = settings.AI_USAGE_RETENTION_DAYS if retention_days is None else retention_days
    cutoff = to_timestamp(_now() - timedelta(days=days))
    with get_connection() as conn:
        return conn.execute("DELETE FROM ai_usage_events WHERE created_at < ?", (cutoff,)).rowcount


def list_between(start: datetime, end: datetime) -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT created_at, connection_id, connection_label, provider, model, kind, outcome,
                   prompt_tokens, output_tokens, total_tokens, latency_ms, characters, audio_seconds
            FROM ai_usage_events
            WHERE created_at >= ? AND created_at < ?
            ORDER BY created_at
            """,
            (to_timestamp(start), to_timestamp(end)),
        ).fetchall()
    return [dict(r) for r in rows]
