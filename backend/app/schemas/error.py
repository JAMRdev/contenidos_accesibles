"""
Esquema estandarizado de respuestas de error de la API (Issue #42).

Todos los endpoints devuelven este esquema ante errores HTTP (4xx y 5xx),
garantizando consistencia para el Frontend y cualquier cliente consumidor.
"""
from typing import Any
from pydantic import BaseModel, Field


class ErrorResponse(BaseModel):
    """
    Formato uniforme para todas las respuestas de error de la API.

    - `code`: Identificador en snake_case para lógica programática en el frontend.
    - `message`: Mensaje descriptivo y comprensible en español para el usuario.
    - `detail`: Información técnica adicional, lista de errores de validación o diccionario extendido.
    """

    code: str = Field(
        description="Código de error estandarizado en snake_case (ej. 'not_found', 'validation_error', 'rate_limit_exceeded').",
        examples=["not_found", "validation_error", "rate_limit_exceeded", "sensitive_data_detected"],
    )
    message: str = Field(
        description="Mensaje explicativo para el usuario o frontend.",
        examples=["El recurso solicitado no fue encontrado."],
    )
    detail: Any | None = Field(
        default=None,
        description="Detalle técnico adicional, lista de errores de validación o payload extendido.",
    )
