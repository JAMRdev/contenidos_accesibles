"""
Manejo de errores uniforme para toda la API (Issue #42).

Estandariza las respuestas de error HTTP para que el Frontend y los clientes
reciban siempre una estructura consistente:
- `code`: Identificador estable en snake_case (ej. 'not_found', 'validation_error').
- `message`: Mensaje legible para el usuario en español.
- `detail`: Detalle técnico, payload o lista de errores de validación.
"""
import logging
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

STATUS_TO_ERROR_CODE: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    408: "request_timeout",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "unprocessable_entity",
    429: "rate_limit_exceeded",
    500: "internal_server_error",
    502: "bad_gateway",
    503: "service_unavailable",
    504: "gateway_timeout",
}

DEFAULT_STATUS_MESSAGES: dict[int, str] = {
    400: "Solicitud inválida.",
    401: "No autorizado. Se requiere iniciar sesión.",
    403: "Acceso denegado. No tenés permisos para realizar esta acción.",
    404: "El recurso solicitado no fue encontrado.",
    405: "Método HTTP no permitido para este recurso.",
    408: "Tiempo de espera de la solicitud agotado.",
    409: "Conflicto con el estado actual del recurso.",
    413: "El contenido de la solicitud supera el tamaño máximo permitido.",
    415: "Tipo de medio no compatible.",
    422: "La solicitud contiene datos no procesables o inválidos.",
    429: "Demasiadas solicitudes. Límite de tasa excedido.",
    500: "Error interno en el servidor.",
    502: "Falla de comunicación con un servicio externo.",
    503: "Servicio no disponible temporalmente.",
    504: "Tiempo de respuesta del servicio externo agotado.",
}


def format_error_payload(status_code: int, detail: Any) -> dict[str, Any]:
    """
    Construye un payload JSON uniforme para cualquier error HTTP.

    Conserva compatibilidad total con tests y clientes existentes manteniendo
    el campo `detail` (sea str, dict o list), al mismo tiempo que garantiza
    los campos de primer nivel `code` y `message`.
    """
    default_code = STATUS_TO_ERROR_CODE.get(status_code, "error")
    default_message = DEFAULT_STATUS_MESSAGES.get(
        status_code, "Ha ocurrido un error al procesar la solicitud."
    )

    payload: dict[str, Any] = {}

    if isinstance(detail, dict):
        code = detail.get("code") or default_code
        message = detail.get("message") or detail.get("detail") or default_message
        payload["code"] = str(code)
        payload["message"] = str(message)
        payload["detail"] = detail
        # Propagar campos clave adicionales al nivel raíz (ej. findings, proposal_id, total)
        for k, v in detail.items():
            if k not in payload:
                payload[k] = v
    elif isinstance(detail, list):
        payload["code"] = "validation_error"
        payload["message"] = "Error de validación en los datos enviados."
        payload["detail"] = detail
    else:
        msg_str = str(detail) if detail is not None and str(detail).strip() else default_message
        payload["code"] = default_code
        payload["message"] = msg_str
        payload["detail"] = msg_str

    return payload


HTTP_422_CODE = getattr(status, "HTTP_422_UNPROCESSABLE_CONTENT", 422)


def setup_exception_handlers(app: FastAPI) -> None:
    """Registra manejadores de excepciones globales para garantizar un formato de error uniforme."""

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        content = format_error_payload(exc.status_code, exc.detail)
        return JSONResponse(
            status_code=exc.status_code,
            content=jsonable_encoder(content),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        content = format_error_payload(HTTP_422_CODE, exc.errors())
        return JSONResponse(
            status_code=HTTP_422_CODE,
            content=jsonable_encoder(content),
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # NUNCA registrar datos del material por privacidad y confidencialidad
        logger.exception(
            "Error interno no controlado al procesar %s %s: %s",
            request.method,
            request.url.path,
            type(exc).__name__,
        )
        content = format_error_payload(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Error interno inesperado en el servidor.",
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content=jsonable_encoder(content),
        )
