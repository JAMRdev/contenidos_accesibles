from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field

from app.core.rate_limit import limit_ai_usage
from app.services.key_rotation_service import AllKeysExhaustedError
from app.services.llm.base import LLMUnavailableError
from app.services.llm_service import llm_service
from app.services.tts.base import TTSUnavailableError
from app.services.tts_service import tts_service

router = APIRouter()


@router.post(
    "/test-connection",
    summary="Validar conexión con el proveedor de IA",
    response_description="Conexión usada y respuesta del modelo",
    dependencies=[Depends(limit_ai_usage)],
)
@router.post(
    "/test-gemini",
    summary="Validar conexión con el proveedor de IA (obsoleto: usar /ai/test-connection)",
    deprecated=True,
    dependencies=[Depends(limit_ai_usage)],
)
async def test_ai_connection():
    """
    ## Prueba de Conectividad con el Proveedor de IA

    Envía un prompt de verificación usando la conexión de IA activa según la
    prioridad configurada en el [panel de administración](/admin). Si la
    conexión falla o está limitada, rota automáticamente a la siguiente
    (aunque sea de otro proveedor).

    ### Respuestas posibles:
    - **200 OK**: La conexión respondió; se informa proveedor, modelo y respuesta.
    - **400 Bad Request**: No hay ninguna conexión de IA habilitada (agregar una desde `/admin`).
    - **429 Too Many Requests**: Todas las conexiones alcanzaron su límite.
    - **503 Service Unavailable**: Ninguna conexión pudo responder (proveedores caídos o mal configurados).
    - **500 Internal Server Error**: Error inesperado.
    """
    try:
        return await llm_service.ping_connection()
    except AllKeysExhaustedError as e:
        raise HTTPException(status_code=429, detail=str(e))
    except LLMUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Error al comunicar con el proveedor de IA: {str(e)}",
        )


# Para probar la voz desde Swagger: un texto corto, que no gaste la cuota de audio de un material
TEST_TTS_MAX_CHARS = 1_000


class TTSTestRequest(BaseModel):
    text: str = Field(
        min_length=1, max_length=TEST_TTS_MAX_CHARS,
        examples=["Hola, chicos. Hoy vamos a ver el ciclo del agua: evaporación, condensación y precipitación."],
    )


@router.post(
    "/test-tts",
    summary="Probar el texto a voz (Issue #121)",
    response_class=Response,
    responses={
        200: {"content": {"audio/mpeg": {}}, "description": "El audio en MP3, para escucharlo en Swagger."},
        422: {"description": f"Texto vacío o de más de {TEST_TTS_MAX_CHARS} caracteres."},
        429: {"description": "Límite de uso de IA."},
        503: {"description": "Ninguna conexión de Gemini pudo generar el audio (sin conexiones, sin cuota o caído)."},
    },
    dependencies=[Depends(limit_ai_usage)],
)
async def test_tts(
    payload: TTSTestRequest,
    mock: bool = Query(default=False, description="Si es True, devuelve un tono corto sin llamar al motor (0 tokens)."),
):
    """
    Convierte un texto corto en audio con el motor elegido en #120: **Gemini 2.5 Flash TTS**, voz
    **Kore**, con acento rioplatense pedido en la instrucción de estilo. Usa las conexiones de Gemini
    de [/admin](/admin) por prioridad.

    Devuelve el MP3; los headers `X-Audio-Duration` (segundos), `X-Audio-Chunks` (fragmentos) y
    `X-TTS-Model` dicen cómo se generó.
    """
    try:
        result = await tts_service.synthesize(payload.text, mock=mock)
    except TTSUnavailableError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return Response(
        content=result.data, media_type=result.content_type,
        headers={
            "Content-Disposition": f'inline; filename="prueba_tts{result.extension}"',
            "X-Audio-Duration": str(result.duration_s), "X-Audio-Chunks": str(result.chunks),
            "X-TTS-Model": result.model,
        },
    )
