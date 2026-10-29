"""
Transformar material (Issue #116): base común de los cuatro formatos.

    POST /transformations       → valida el material y responde 'pending' al instante
    (tarea en el proceso)       → 'processing' → 'ready' (o 'failed')
    GET  /transformations/{id}  → estado, resultado y URL de vista previa del archivo
    POST /transformations/{id}/decision → el docente acepta, edita o descarta

La generación corre en una tarea asyncio del proceso: alcanza porque hay una sola réplica
(railway.json), igual que el rate limit en memoria. Una generación larga no deja la request
abierta. Si el proceso se reinicia en el medio, al arrancar quedan en 'failed' (fail_interrupted).

Los formatos con un archivo largo y con costo (audio, #122) lo generan recién cuando el docente
acepta o edita el resultado (render_after_decision): la transformación vuelve a 'processing', con
`progress`, y termina en 'ready' con el archivo o en 'failed' con el resultado aceptado intacto,
que se reintenta con retry_file.

El texto del material solo vive en memoria mientras se genera: la base guarda el resultado
estructurado y la clave en R2 del archivo generado. Cada formato es un FormatSpec con su esquema,
su mock (0 tokens) y, cuando su issue lo implementa (#117, #118, #119, #122), su generador con IA
y su renderer (el código que dibuja la imagen o genera el audio a partir del resultado).
"""
import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
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
from app.schemas.analysis import AccessibilityAnalysisResponse
from app.schemas.transformation import (
    GeneratedFileInfo,
    TransformationDecisionRequest,
    TransformationError,
    TransformationProgress,
    TransformationRequest,
    TransformationResponse,
    TransformationSource,
)
from app.services import adaptation_service, retention_service, storage_service
from app.services.document_service import document_service
from app.services.key_rotation_service import AllKeysExhaustedError
from app.services.llm.base import LLMInvalidResponseError, LLMUnavailableError
from app.services.sensitive_data_service import ensure_no_sensitive_data
from app.services.storage_service import StorageError, new_object_key
from app.services.transformations import audio, infographic, mind_map, no_images
from app.services.transformations.base import FormatSpec, GeneratedFile, Generation, SourceMaterial
from app.services.tts.base import TTSTextTooLongError, TTSUnavailableError

logger = logging.getLogger(__name__)

MOCK_WARNING = "Resultado generado en modo simulación (?mock=true). No se consumieron tokens de IA."
INTERRUPTED_MESSAGE = "El servidor se reinició mientras se generaba la transformación. Pedila de nuevo."


# ---------------------------------------------------------------------------
# Formatos: cada uno vive en app/services/transformations/ con su esquema, mock y generador con IA
# ---------------------------------------------------------------------------


FORMATS: dict[str, FormatSpec] = {
    "no_images": no_images.SPEC,
    "mind_map": mind_map.SPEC,
    "infographic": infographic.SPEC,
    "audio": audio.SPEC,
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
    material: SourceMaterial
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
    record = analysis_repository.get(analysis_id)
    # El diagnóstico no guarda las imágenes, pero sí los textos alternativos que propuso para ellas
    alt_texts = AccessibilityAnalysisResponse.model_validate_json(record["diagnostic_json"]).alt_text_proposals
    material = SourceMaterial(text=adapted.adapted_text, alt_texts=alt_texts)
    return _Source("analysis", analysis_id, material, record["expires_at"], warnings)


async def _file_source(file_id: str, owner: str, dismissed: list[str]) -> _Source:
    """Archivo subido con /files: se extrae en memoria, igual que para diagnosticarlo (Issue #63)."""
    content, filename = await read_stored_file(file_id, owner)
    doc = await asyncio.to_thread(document_service.process_file, content, filename)
    # Issue #66: este texto no pasó por el detector del diagnóstico y va a la IA
    ensure_no_sensitive_data(doc.raw_text, dismissed)
    record = await asyncio.to_thread(files.get_for_user, file_id, owner)
    # Avisos de la extracción sobre imágenes (p. ej. más de las que se procesan, o una que no se pudo leer).
    # El de "el escáner de accesibilidad verificará…" es del diagnóstico y acá no corresponde.
    warnings = [
        w for w in doc.warnings
        if ("imagen" in w.lower() or "imágenes" in w.lower()) and "escáner de accesibilidad" not in w.lower()
    ]
    # raw_text y no text_for_analysis: ese viene recortado a 3.000 palabras y sin saltos de línea ni puntuación
    material = SourceMaterial(text=doc.raw_text, images=doc.extracted_images)
    return _Source("file", file_id, material, record["expires_at"], warnings)


async def _load_source(request: TransformationRequest, owner: str, dismissed: list[str]) -> _Source:
    if request.analysis_id:
        source = await asyncio.to_thread(_analysis_source, request.analysis_id, owner)
    else:
        source = await _file_source(request.file_id, owner, dismissed)
    if not source.material.text.strip():
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


def _with_views(t: TransformationResponse) -> TransformationResponse:
    """Representaciones que arma el backend (Markdown, Mermaid) del resultado vigente: el final o el propuesto."""
    spec = FORMATS[t.format]
    current = t.final_result or (t.result if t.review_status == "pendiente" else None)
    if spec.views is None or current is None:
        return t
    try:
        result = spec.schema.model_validate(current)
    except ValidationError:
        # Un resultado guardado antes de que se achicaran los límites del esquema: se devuelve sin vistas, no con 500
        logger.warning("El resultado de la transformación %s no cumple el esquema actual", t.transformation_id)
        return t
    return t.model_copy(update={"views": spec.views(result)})


def _with_preview(t: TransformationResponse, object_key: str | None) -> TransformationResponse:
    """Vistas derivadas y URL firmada nueva en cada consulta, para revisar el archivo (no después de descartarlo)."""
    t = _with_views(t)
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
    limit: Callable[[bool], Awaitable[None]] | None = None,
) -> TransformationResponse:
    """
    Valida el pedido y el material, guarda la transformación 'pending' y lanza la generación.
    `limit` cuenta el pedido para los límites de uso recién con el material validado: un pedido
    rechazado (404, 422, 429 por transformaciones en curso) no descuenta cuota. Recibe si la
    generación va a usar IA.
    """
    ensure_available(request.format, mock=mock)
    spec = FORMATS[request.format]
    if _active.get(owner, 0) >= settings.TRANSFORMATION_MAX_ACTIVE_PER_USER:
        raise TooManyActiveError(
            "Ya tenés transformaciones generándose. Esperá a que terminen antes de pedir otra."
        )

    _active[owner] = _active.get(owner, 0) + 1  # reservado antes de leer el material (hay awaits en el medio)
    try:
        source = await _load_source(request, owner, dismissed or [])
        if limit:
            await limit(spec.uses_ai(source.material))
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
    task = asyncio.create_task(_run(transformation, source.material, spec, owner=owner, mock=mock))
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
    if isinstance(exc, TTSUnavailableError):
        return TransformationError(code="tts_unavailable", message=str(exc))
    if isinstance(exc, TTSTextTooLongError):
        return TransformationError(code="too_long", message=str(exc))
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


async def _run(t: TransformationResponse, material: SourceMaterial, spec: FormatSpec, *, owner: str, mock: bool) -> None:
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
            generation = Generation(result=spec.mock(material), metadata=_MOCK_METADATA, warnings=[MOCK_WARNING])
        else:
            generation = await spec.generate(material)
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


def _deferred(spec: FormatSpec, decision: str) -> bool:
    """Si la decisión lanza la generación del archivo (audio, #122)."""
    return spec.render_after_decision is not None and decision != "descartada"


def _reserve(owner: str) -> None:
    if _active.get(owner, 0) >= settings.TRANSFORMATION_MAX_ACTIVE_PER_USER:
        raise TooManyActiveError("Ya tenés transformaciones generándose. Esperá a que terminen antes de pedir otra.")
    _active[owner] = _active.get(owner, 0) + 1


def _start_render(t: TransformationResponse, owner: str) -> None:
    mock = bool(t.metadata and t.metadata.provider == "mock")
    task = asyncio.create_task(_render_deferred(t, owner=owner, mock=mock))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def decide(transformation_id: str, request: TransformationDecisionRequest, *, owner: str) -> TransformationResponse:
    """
    Revisión docente. En los formatos con archivo diferido (audio), aceptar o editar deja la
    transformación en 'processing' y genera el archivo en una tarea, como la generación inicial.
    """
    t, _key = await asyncio.to_thread(_load, transformation_id, owner)
    deferred = _deferred(FORMATS[t.format], request.decision)
    if deferred:
        _reserve(owner)
    try:
        decided = await asyncio.to_thread(_decide, transformation_id, request, owner=owner)
    except BaseException:
        if deferred:
            _release(owner)
        raise
    if deferred:
        _start_render(decided, owner)
    return decided


async def retry_file(transformation_id: str, *, owner: str, limit: Callable[[bool], Awaitable[None]] | None = None,
                     ) -> TransformationResponse:
    """Vuelve a generar el archivo diferido (audio) de una transformación aceptada cuyo archivo falló."""
    t, _key = await asyncio.to_thread(_load, transformation_id, owner)
    spec = FORMATS[t.format]
    if spec.render_after_decision is None:
        raise TransformationStateError("Este formato no genera un archivo después de aceptarlo.")
    if t.status != "failed" or t.review_status not in ("aceptada", "editada"):
        raise TransformationStateError("Solo se reintenta el archivo de una transformación aceptada cuyo archivo falló.")
    _reserve(owner)
    try:
        if limit:
            await limit(not (t.metadata and t.metadata.provider == "mock"))
        processing = t.model_copy(update={"status": "processing", "error": None, "progress": None})
        if not await asyncio.to_thread(_save, t, processing, owner, None):
            raise TransformationStateError("La transformación cambió de estado: volvé a consultarla.")
    except BaseException:
        _release(owner)
        raise
    _start_render(processing, owner)
    return processing


async def _render_deferred(t: TransformationResponse, *, owner: str, mock: bool) -> None:
    """Genera el archivo del resultado final, lo sube y lo registra en /files. Informa el progreso."""
    spec = FORMATS[t.format]
    storage = storage_service.get_storage()
    started = time.perf_counter()
    outcome = "failed"
    current = t
    keys: list[str] = []
    registered: list[str] = []

    async def on_progress(done: int, total: int) -> None:
        nonlocal current
        updated = current.model_copy(update={"progress": TransformationProgress(done=done, total=total)})
        if await asyncio.to_thread(_save, current, updated, owner, None):
            current = updated

    def register(info: GeneratedFileInfo, key: str) -> GeneratedFileInfo:
        record = files.create(
            user_id=owner, object_key=key, filename=info.filename, content_type=info.content_type,
            size_bytes=info.size_bytes, ttl_hours=retention_service.file_retention_hours(), kind="export",
            status="uploaded", not_after=t.expires_at,
        )
        registered.append(record["id"])
        return info.model_copy(update={"file_id": record["id"]})

    try:
        rendered = await spec.render_after_decision(spec.schema.model_validate(t.final_result), mock, on_progress)
        main_info, main_key = await _store_file(rendered.main, spec, t.transformation_id, owner)
        keys.append(main_key)
        stored = [(main_info, main_key)]
        for suffix, extra in rendered.extras:
            key = new_object_key(owner, "transformations", extra.extension)
            await asyncio.to_thread(storage.put, key, extra.data, extra.content_type)
            keys.append(key)
            stored.append((GeneratedFileInfo(
                filename=f"{spec.filename}{suffix}_{t.transformation_id[:8]}{extra.extension}",
                content_type=extra.content_type, size_bytes=len(extra.data),
            ), key))
        infos = [await asyncio.to_thread(register, info, key) for info, key in stored]

        ready = current.model_copy(update={
            "status": "ready", "file": infos[0], "extra_files": infos[1:], "progress": None,
            "warnings": [*current.warnings, *rendered.warnings],
        })
        if not await asyncio.to_thread(_save, current, ready, owner, main_key):
            raise TransformationStateError("La transformación venció o se borró mientras se generaba el archivo.")
        outcome = "ready"
        for info in infos:
            await asyncio.to_thread(
                file_events.record, "export", format=file_events.file_format(info.filename),
                size_bytes=info.size_bytes, detail="transformation:link", user_id=owner,
            )
    except Exception as e:  # noqa: BLE001 - cualquier falla deja la transformación en 'failed', con el resultado aceptado
        if not isinstance(e, (TTSUnavailableError, StorageError, TransformationStateError)):
            logger.exception("Falló el archivo de la transformación %s (%s)", t.transformation_id, t.format)
        try:
            for file_id in registered:
                await asyncio.to_thread(files.delete, file_id)
            for key in keys:
                await asyncio.to_thread(storage.delete, key)
            failed = current.model_copy(update={"status": "failed", "error": _error_for(e), "progress": None})
            await asyncio.to_thread(_save, current, failed, owner, None)
        except Exception:  # noqa: BLE001
            logger.exception("No se pudo registrar la falla del archivo de la transformación %s", t.transformation_id)
    finally:
        # Sin otra fila en user_activity: el pedido ya se contó al crearla, y cada fragmento de TTS
        # queda como ai_call (format 'tts')
        _release(owner)
        logger.info("Archivo de la transformación %s: %s en %d ms", t.transformation_id, outcome,
                    int((time.perf_counter() - started) * 1000))


def _decide(transformation_id: str, request: TransformationDecisionRequest, *, owner: str) -> TransformationResponse:
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
        try:
            registered = files.create(
                user_id=owner, object_key=new_key, filename=file_info.filename, content_type=file_info.content_type,
                size_bytes=file_info.size_bytes, ttl_hours=retention_service.file_retention_hours(), kind="export",
                status="uploaded", not_after=t.expires_at,
            )
        except Exception:
            if new_key != key:  # el archivo regenerado no quedó registrado en ningún lado
                storage.delete(new_key)
            raise
        file_info = file_info.model_copy(update={"file_id": registered["id"]})

    decided = t.model_copy(update={
        "review_status": request.decision, "edited_result": edited_result, "final_result": final_result,
        "file": file_info, "decided_at": _now(),
        # El archivo diferido (audio) se genera ahora: decide() lanza la tarea
        **({"status": "processing"} if _deferred(spec, request.decision) else {}),
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


class ExportFormatError(ValueError):
    """El formato no se puede exportar en esa extensión."""


def export(transformation_id: str, extension: str, *, owner: str) -> tuple[bytes, str, str, str]:
    """
    Exporta el resultado aceptado o editado (solo lo aceptado se exporta). Devuelve
    (bytes, content type, nombre de archivo, vencimiento de la transformación).
    """
    t, _key = _load(transformation_id, owner)
    if t.review_status not in ("aceptada", "editada"):
        raise TransformationStateError("Solo se exporta una transformación aceptada o editada.")
    spec = FORMATS[t.format]
    exporter = spec.exports.get(extension)
    if exporter is None:
        available = ", ".join(spec.exports) or "ninguno (el archivo generado se descarga desde /files)"
        raise ExportFormatError(f"Formato de exportación no disponible para '{t.format}'. Opciones: {available}.")
    data, content_type = exporter(spec.schema.model_validate(t.final_result))
    return data, content_type, f"{spec.filename}_{t.transformation_id[:8]}.{extension}", t.expires_at


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
