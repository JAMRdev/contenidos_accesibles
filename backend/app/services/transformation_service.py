"""
Transformar material (Issue #116): base común de los cuatro formatos.

    POST /transformations       → valida el material y responde 'pending' al instante
    (tarea en el proceso)       → 'processing' → 'ready' (o 'failed')
    GET  /transformations/{id}  → estado, resultado y URL de vista previa del archivo
    POST /transformations/{id}/decision → el docente acepta, edita o descarta

La generación corre en una tarea asyncio del proceso: alcanza porque hay una sola réplica
(railway.json), igual que el rate limit en memoria. Una generación larga no deja la request
abierta. Si el proceso se reinicia en el medio, al arrancar quedan en 'failed' (fail_interrupted).

El texto del material solo vive en memoria mientras se genera: la base guarda el resultado
estructurado y la clave en R2 del archivo generado. Cada formato es un FormatSpec con su esquema,
su mock (0 tokens) y, cuando su issue lo implementa (#117, #118, #119, #122), su generador con IA
y su renderer (el código que dibuja la imagen o genera el audio a partir del resultado).
"""
import asyncio
import io
import logging
import time
import uuid
import wave
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from pydantic import BaseModel, ValidationError

from app.api.v1.uploads import read_stored_file
from app.core.config import settings
from app.db import analysis_repository
from app.db import file_event_repository as file_events
from app.db import file_repository as files
from app.db import transformation_repository as repository
from app.db import user_activity_repository as user_activity
from app.schemas.adaptation import AdaptationMetadata
from app.schemas.transformation import (
    AudioResult,
    GeneratedFileInfo,
    InfographicResult,
    InfographicSection,
    MindMapNode,
    MindMapResult,
    NoImagesResult,
    TransformationDecisionRequest,
    TransformationError,
    TransformationRequest,
    TransformationResponse,
    TransformationSource,
)
from app.services import adaptation_service, retention_service, storage_service
from app.services.document_service import document_service
from app.services.key_rotation_service import AllKeysExhaustedError
from app.services.llm.base import LLMInvalidResponseError, LLMUnavailableError
from app.services.rules_service import split_paragraphs, split_sentences
from app.services.sensitive_data_service import ensure_no_sensitive_data
from app.services.storage_service import StorageError, new_object_key

logger = logging.getLogger(__name__)

MOCK_WARNING = "Resultado generado en modo simulación (?mock=true). No se consumieron tokens de IA."
INTERRUPTED_MESSAGE = "El servidor se reinició mientras se generaba la transformación. Pedila de nuevo."


# ---------------------------------------------------------------------------
# Formatos
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GeneratedFile:
    data: bytes
    content_type: str
    extension: str  # con el punto: ".png", ".mp3"


@dataclass(frozen=True)
class Generation:
    """Lo que devuelve el generador con IA de un formato."""

    result: BaseModel
    metadata: AdaptationMetadata
    warnings: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FormatSpec:
    schema: type[BaseModel]
    filename: str
    # Resultado fijo a partir del texto, sin IA (?mock=true)
    mock: Callable[[str], BaseModel]
    # Generador con IA real; None hasta que el issue del formato lo implementa (responde 501)
    generate: Callable[[str], Awaitable[Generation]] | None = None
    # Archivo a partir del resultado (corre en un hilo). None = el formato no genera archivo
    render: Callable[[BaseModel], GeneratedFile | None] | None = None
    mock_render: Callable[[BaseModel], GeneratedFile | None] | None = None


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _blocks(text: str) -> tuple[str, list[str]]:
    """Primer renglón (como título) y los párrafos siguientes del material."""
    paragraphs = [p.strip() for p in split_paragraphs(text) if p.strip()] or [text.strip()]
    first_line = paragraphs[0].splitlines()[0]
    rest = paragraphs[1:] or paragraphs
    return first_line, rest


def _mock_no_images(text: str) -> NoImagesResult:
    return NoImagesResult(text=text.strip(), omitted_images=[])


def _mock_mind_map(text: str) -> MindMapResult:
    title, paragraphs = _blocks(text)
    children = [
        MindMapNode(text=_clip((split_sentences(p) or [p])[0], 120)) for p in paragraphs[:4]
    ]
    return MindMapResult(root=MindMapNode(text=_clip(title, 120), children=children))


def _mock_infographic(text: str) -> InfographicResult:
    title, paragraphs = _blocks(text)
    sections = [
        InfographicSection(title=f"Idea {i}", text=_clip(p, 300), icon="info")
        for i, p in enumerate(paragraphs[:3], start=1)
    ]
    return InfographicResult(title=_clip(title, 100), subtitle="[MOCK] Infografía simulada", sections=sections)


def _mock_audio(text: str) -> AudioResult:
    return AudioResult(script=_clip(text, 2000))


def _mock_audio_file(_result: BaseModel) -> GeneratedFile:
    """Un segundo de silencio en WAV: prueba el circuito del archivo (R2, vista previa, /files) sin TTS."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(8000)
        wav.writeframes(b"\x00\x00" * 8000)
    return GeneratedFile(data=buffer.getvalue(), content_type="audio/wav", extension=".wav")


FORMATS: dict[str, FormatSpec] = {
    "no_images": FormatSpec(NoImagesResult, "material_sin_imagenes", _mock_no_images),
    "mind_map": FormatSpec(MindMapResult, "mapa_mental", _mock_mind_map),
    "infographic": FormatSpec(InfographicResult, "infografia", _mock_infographic),
    "audio": FormatSpec(AudioResult, "audio", _mock_audio, mock_render=_mock_audio_file),
}

_MOCK_METADATA = AdaptationMetadata(
    provider="mock", model="mock-transformation-v1", connection="Simulador Frontend (?mock=true)",
    latency_ms=1, prompt_tokens=0, output_tokens=0, thinking_tokens=0, total_tokens=0,
)


# ---------------------------------------------------------------------------
# Errores
# ---------------------------------------------------------------------------


class TransformationNotFoundError(LookupError):
    """No existe, venció o es de otro usuario (para el usuario, es lo mismo)."""


class TransformationStateError(ValueError):
    """La operación no corresponde al estado actual (ej. decidir antes de 'ready' o dos veces)."""


class FormatNotAvailableError(Exception):
    """El formato todavía no tiene generador con IA real: solo funciona con ?mock=true."""

    CODE = "format_not_available"


class TooManyActiveError(Exception):
    """El usuario ya tiene el máximo de transformaciones generándose a la vez."""


class EmptySourceError(ValueError):
    """El material no tiene texto para transformar."""


class InvalidEditError(ValueError):
    """El resultado editado no respeta el esquema del formato."""

    def __init__(self, error: ValidationError):
        super().__init__("El resultado editado no respeta el esquema del formato.")
        self.errors = error.errors(include_url=False, include_context=False)


# ---------------------------------------------------------------------------
# Material de origen
# ---------------------------------------------------------------------------


@dataclass
class _Source:
    type: str
    id: str
    text: str
    expires_at: str
    warnings: list[str]


def _analysis_source(analysis_id: str, owner: str) -> _Source:
    """Material del diagnóstico con los cambios que el docente aceptó (sin cambios, el original)."""
    adapted = adaptation_service.build_adapted_document(analysis_id, owner=owner)
    warnings = []
    if adapted.applied_count:
        warnings.append(
            f"Se transformó la versión adaptada del material, con {adapted.applied_count} "
            f"cambio{'s' if adapted.applied_count != 1 else ''} aceptado{'s' if adapted.applied_count != 1 else ''}."
        )
    return _Source("analysis", analysis_id, adapted.adapted_text, analysis_repository.get(analysis_id)["expires_at"], warnings)


async def _file_source(file_id: str, owner: str, dismissed: list[str]) -> _Source:
    """Archivo subido con /files: se extrae en memoria, igual que para diagnosticarlo (Issue #63)."""
    content, filename = await read_stored_file(file_id, owner)
    doc = await asyncio.to_thread(document_service.process_file, content, filename)
    # Issue #66: este texto no pasó por el detector del diagnóstico y va a la IA
    ensure_no_sensitive_data(doc.raw_text, dismissed)
    record = await asyncio.to_thread(files.get_for_user, file_id, owner)
    return _Source("file", file_id, doc.text_for_analysis, record["expires_at"], [])


async def _load_source(request: TransformationRequest, owner: str, dismissed: list[str]) -> _Source:
    if request.analysis_id:
        source = await asyncio.to_thread(_analysis_source, request.analysis_id, owner)
    else:
        source = await _file_source(request.file_id, owner, dismissed)
    if not source.text.strip():
        raise EmptySourceError(
            "El material no contiene texto para transformar. Si es un PDF escaneado, subí una versión con texto seleccionable."
        )
    return source


# ---------------------------------------------------------------------------
# Persistencia
# ---------------------------------------------------------------------------


def _now() -> str:
    return repository.now_iso()


def _record(t: TransformationResponse, owner: str, object_key: str | None) -> dict:
    stored = t.model_copy(update={"file": t.file.model_copy(update={"preview_url": None, "expires_in_seconds": None})}) if t.file else t
    return {
        "id": t.transformation_id, "user_id": owner, "format": t.format, "status": t.status,
        "review_status": t.review_status, "object_key": object_key, "payload_json": stored.model_dump_json(),
        "created_at": t.created_at, "updated_at": t.updated_at, "expires_at": t.expires_at,
    }


def _save(previous: TransformationResponse, updated: TransformationResponse, owner: str, object_key: str | None) -> bool:
    record = _record(updated.model_copy(update={"updated_at": _now()}), owner, object_key)
    return repository.update(
        previous.transformation_id,
        expected_status=previous.status, expected_review=previous.review_status,
        status=record["status"], review_status=record["review_status"],
        object_key=object_key, payload_json=record["payload_json"],
    )


def _load(transformation_id: str, owner: str) -> tuple[TransformationResponse, str | None]:
    record = repository.get(transformation_id)
    if not record or record["user_id"] != owner or record["expires_at"] <= _now():
        raise TransformationNotFoundError("Transformación no encontrada o vencida.")
    return TransformationResponse.model_validate_json(record["payload_json"]), record["object_key"]


def _with_preview(t: TransformationResponse, object_key: str | None) -> TransformationResponse:
    """URL firmada nueva en cada consulta, para revisar el archivo (no después de descartarlo)."""
    if not t.file or not object_key or t.review_status == "descartada":
        return t
    ttl = settings.FILE_DOWNLOAD_URL_TTL_S
    try:
        url = storage_service.get_storage().presign_get(object_key, t.file.filename, t.file.content_type, ttl)
    except StorageError:
        return t.model_copy(update={"warnings": [*t.warnings, "No se pudo firmar la vista previa del archivo: probá de nuevo."]})
    return t.model_copy(update={"file": t.file.model_copy(update={"preview_url": url, "expires_in_seconds": ttl})})


# ---------------------------------------------------------------------------
# Crear y generar
# ---------------------------------------------------------------------------

# Tareas en curso (referencia fuerte para que el recolector no las corte) y cuántas tiene cada usuario
_tasks: set[asyncio.Task] = set()
_active: dict[str, int] = {}


def _release(owner: str) -> None:
    _active[owner] = _active.get(owner, 1) - 1
    if _active[owner] <= 0:
        del _active[owner]


def ensure_available(format: str, *, mock: bool) -> None:
    if not mock and FORMATS[format].generate is None:
        raise FormatNotAvailableError(
            f"El formato '{format}' todavía no está disponible con IA real. Probalo con ?mock=true."
        )


async def create(
    request: TransformationRequest, *, owner: str, mock: bool = False, dismissed: list[str] | None = None,
) -> TransformationResponse:
    """Valida el pedido y el material, guarda la transformación 'pending' y lanza la generación."""
    ensure_available(request.format, mock=mock)
    spec = FORMATS[request.format]
    if _active.get(owner, 0) >= settings.TRANSFORMATION_MAX_ACTIVE_PER_USER:
        raise TooManyActiveError(
            "Ya tenés transformaciones generándose. Esperá a que terminen antes de pedir otra."
        )

    _active[owner] = _active.get(owner, 0) + 1  # reservado antes de leer el material (hay awaits en el medio)
    try:
        source = await _load_source(request, owner, dismissed or [])
        now = _now()
        retention_limit = (datetime.now(timezone.utc) + timedelta(hours=retention_service.file_retention_hours()))
        transformation = TransformationResponse(
            transformation_id=str(uuid.uuid4()),
            format=request.format,
            source=TransformationSource(type=source.type, id=source.id),
            status="pending",
            warnings=source.warnings,
            created_at=now,
            updated_at=now,
            # Como mucho, con el material de origen y con la retención de archivos (Issue #64)
            expires_at=min(source.expires_at, retention_limit.strftime("%Y-%m-%dT%H:%M:%SZ")),
        )
        await asyncio.to_thread(repository.create, _record(transformation, owner, None))
    except BaseException:
        _release(owner)
        raise

    # create_task copia el contexto: la llamada a la IA queda a nombre del usuario (Issue #104)
    task = asyncio.create_task(_run(transformation, source.text, spec, owner=owner, mock=mock))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return transformation


def _error_for(exc: Exception) -> TransformationError:
    if isinstance(exc, AllKeysExhaustedError):
        return TransformationError(code="ai_quota_exhausted", message=str(exc))
    if isinstance(exc, LLMInvalidResponseError):
        return TransformationError(code="invalid_ai_response", message=str(exc))
    if isinstance(exc, LLMUnavailableError):
        return TransformationError(code="ai_unavailable", message=str(exc))
    if isinstance(exc, StorageError):
        return TransformationError(
            code="storage_unavailable", message="El almacenamiento de archivos no respondió. Probá de nuevo en unos minutos."
        )
    return TransformationError(code="internal_error", message="Error inesperado al generar la transformación.")


async def _store_file(generated: GeneratedFile | None, spec: FormatSpec, transformation_id: str, owner: str):
    """Sube el archivo generado al almacenamiento. Devuelve (info, clave) o (None, None)."""
    if generated is None:
        return None, None
    key = new_object_key(owner, "transformations", generated.extension)
    await asyncio.to_thread(storage_service.get_storage().put, key, generated.data, generated.content_type)
    info = GeneratedFileInfo(
        filename=f"{spec.filename}_{transformation_id[:8]}{generated.extension}",
        content_type=generated.content_type,
        size_bytes=len(generated.data),
    )
    return info, key


async def _run(t: TransformationResponse, text: str, spec: FormatSpec, *, owner: str, mock: bool) -> None:
    started = time.perf_counter()
    metadata: AdaptationMetadata | None = None
    outcome = "failed"
    key = None
    try:
        processing = t.model_copy(update={"status": "processing"})
        if not await asyncio.to_thread(_save, t, processing, owner, None):
            return  # venció o se borró mientras esperaba
        t = processing

        if mock:
            generation = Generation(result=spec.mock(text), metadata=_MOCK_METADATA, warnings=[MOCK_WARNING])
        else:
            generation = await spec.generate(text)
        metadata = generation.metadata
        render = spec.mock_render if mock else spec.render
        generated = await asyncio.to_thread(render, generation.result) if render else None
        file_info, key = await _store_file(generated, spec, t.transformation_id, owner)

        ready = t.model_copy(update={
            "status": "ready",
            "result": generation.result.model_dump(mode="json"),
            "file": file_info,
            "metadata": metadata,
            "warnings": [*t.warnings, *generation.warnings],
        })
        if await asyncio.to_thread(_save, t, ready, owner, key):
            outcome = "ready"
        elif key:
            await asyncio.to_thread(storage_service.get_storage().delete, key)
    except Exception as e:  # noqa: BLE001 - cualquier falla deja la transformación en 'failed', con su motivo
        if not isinstance(e, (AllKeysExhaustedError, LLMInvalidResponseError, LLMUnavailableError, StorageError)):
            logger.exception("Falló la transformación %s (%s)", t.transformation_id, t.format)
        failed = t.model_copy(update={"status": "failed", "error": _error_for(e), "metadata": metadata})
        try:
            await asyncio.to_thread(_save, t, failed, owner, None)
            if key:
                await asyncio.to_thread(storage_service.get_storage().delete, key)
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo registrar la falla de la transformación %s", t.transformation_id)
    finally:
        _release(owner)
        # Métricas por usuario y por formato (Issue #104). Los tokens van en la fila ai_call de la llamada
        await asyncio.to_thread(
            user_activity.record, "transformation", owner, format=t.format, detail=outcome, mock=mock,
            provider=metadata.provider if metadata else None, model=metadata.model if metadata else None,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


# ---------------------------------------------------------------------------
# Consultar y decidir
# ---------------------------------------------------------------------------


def get(transformation_id: str, *, owner: str) -> TransformationResponse:
    return _with_preview(*_load(transformation_id, owner))


def list_for_user(*, owner: str) -> list[TransformationResponse]:
    return [
        _with_preview(TransformationResponse.model_validate_json(r["payload_json"]), r["object_key"])
        for r in repository.list_for_user(owner)
    ]


def decide(transformation_id: str, request: TransformationDecisionRequest, *, owner: str) -> TransformationResponse:
    """
    Revisión docente: solo lo aceptado (o editado) queda guardado. Con archivo, se registra en
    /files para descargarlo y compartirlo; al descartar, se borra del almacenamiento.
    """
    t, key = _load(transformation_id, owner)
    if t.status != "ready":
        raise TransformationStateError(
            "La transformación todavía se está generando." if t.status in ("pending", "processing")
            else "La transformación falló: pedí una nueva."
        )
    if t.review_status != "pendiente":
        raise TransformationStateError(f"La transformación ya fue {t.review_status}.")
    spec = FORMATS[t.format]
    storage = storage_service.get_storage()

    final_result = t.result
    edited_result = None
    file_info = t.file
    new_key = key
    if request.decision == "editada":
        try:
            edited_result = spec.schema.model_validate(request.edited_result).model_dump(mode="json")
        except ValidationError as e:
            raise InvalidEditError(e)
        final_result = edited_result
        # El archivo sale del resultado: con el resultado editado, se vuelve a generar
        render = spec.mock_render if t.metadata and t.metadata.provider == "mock" else spec.render
        if t.file and render:
            generated = render(spec.schema.model_validate(edited_result))
            if generated:
                new_key = new_object_key(owner, "transformations", generated.extension)
                storage.put(new_key, generated.data, generated.content_type)
                file_info = t.file.model_copy(update={"content_type": generated.content_type, "size_bytes": len(generated.data)})
    elif request.decision == "descartada":
        final_result = None

    registered = None
    if request.decision != "descartada" and file_info and new_key:
        registered = files.create(
            user_id=owner, object_key=new_key, filename=file_info.filename, content_type=file_info.content_type,
            size_bytes=file_info.size_bytes, ttl_hours=retention_service.file_retention_hours(), kind="export",
            status="uploaded", not_after=t.expires_at,
        )
        file_info = file_info.model_copy(update={"file_id": registered["id"]})

    decided = t.model_copy(update={
        "review_status": request.decision, "edited_result": edited_result, "final_result": final_result,
        "file": file_info, "decided_at": _now(),
    })
    stored_key = None if request.decision == "descartada" else new_key
    if not _save(t, decided, owner, stored_key):
        if registered:
            files.delete(registered["id"])
        if new_key != key:
            storage.delete(new_key)
        raise TransformationStateError("La transformación cambió de estado mientras se procesaba la decisión: volvé a consultarla.")

    # El archivo anterior ya no se usa (descartado, o reemplazado por el del resultado editado)
    if key and (request.decision == "descartada" or new_key != key):
        try:
            storage.delete(key)
        except StorageError:
            # Sin la clave en la fila, la limpieza no lo encuentra: lo borra la regla de lifecycle del bucket
            logger.warning("No se pudo borrar %s del almacenamiento", key)

    if registered:
        file_events.record("export", format=file_events.file_format(file_info.filename), size_bytes=file_info.size_bytes,
                           detail="transformation:link", user_id=owner)
    user_activity.record("transformation_decision", owner, format=t.format, detail=request.decision)
    return _with_preview(decided, stored_key)


def cleanup_expired(now: str) -> dict:
    """Borra las transformaciones vencidas y su archivo (corre en la limpieza de archivos, Issue #64)."""
    result = {"deleted": 0, "errors": 0}
    storage = storage_service.get_storage()
    for record in repository.list_expired(now):
        if record["object_key"]:
            try:
                storage.delete(record["object_key"])
            except StorageError as e:
                result["errors"] += 1
                logger.warning("No se pudo borrar %s del almacenamiento: %s", record["object_key"], e)
                continue
        repository.delete(record["id"])
        result["deleted"] += 1
    return result
