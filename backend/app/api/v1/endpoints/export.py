"""
Endpoints de exportación de material adaptado e informe (Issue #40).

Genera los archivos en memoria (export_service.py) y los devuelve como
StreamingResponse con el Content-Disposition correcto para descarga directa.

TODO (#62 / #64): Cuando el Issue #62 (Cloudflare R2 + URL firmada) esté
implementado, reemplazar el StreamingResponse por una llamada a:
    storage_service.upload_and_sign(data, filename, content_type)
y devolver un ExportUrlResponse con download_url + expires_in_seconds.
"""

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
import io

from app.services import adaptation_service, export_service
from app.services.adaptation_service import AdaptationNotFoundError

router = APIRouter()


@router.get(
    "/content",
    summary="Exportar material adaptado (Issue #40)",
    response_class=StreamingResponse,
    responses={
        200: {"description": "Archivo descargable con el material adaptado."},
        400: {"description": "Formato no soportado."},
        404: {"description": "Diagnóstico no encontrado o expirado."},
    },
)
async def export_content(
    analysis_id: str = Query(description="ID del diagnóstico cuyo material adaptado se desea exportar."),
    format: str = Query(default="docx", description="Formato de salida: 'docx', 'pdf', 'md' o 'txt'."),
):
    """
    ## Exportación del material adaptado (Issue #40)

    Genera el material educativo con **solo los cambios aceptados o editados**
    por el docente y lo entrega como archivo descargable:

    - **`docx`** (por defecto): documento Word con tipografía accesible (Arial, interlineado 1.5).
    - **`pdf`**: documento PDF accesible con diseño maquetado en memoria (márgenes 20mm, tipografía clara).
    - **`md`**: Markdown con metadatos de análisis y el texto adaptado.
    - **`txt`**: texto plano UTF-8.

    El documento original **no se modifica**. El archivo se genera en memoria y se
    transmite directamente al cliente.

    > **Nota técnica (#62):** En la versión de producción con Cloudflare R2,
    > este endpoint devolverá una URL firmada de descarga en lugar de transmitir
    > los bytes directamente.
    """
    try:
        adapted = adaptation_service.build_adapted_document(analysis_id)
    except AdaptationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        data, content_type = export_service.export_content(adapted, format)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    filename = export_service.build_filename("material_adaptado", analysis_id, format)
    return StreamingResponse(
        io.BytesIO(data),
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(
    "/report",
    summary="Exportar informe de accesibilidad (Issue #40)",
    response_class=StreamingResponse,
    responses={
        200: {"description": "Archivo descargable con el informe ejecutivo de accesibilidad."},
        400: {"description": "Formato no soportado."},
        404: {"description": "Diagnóstico no encontrado o expirado."},
    },
)
async def export_report(
    analysis_id: str = Query(description="ID del diagnóstico cuyo informe se desea exportar."),
    format: str = Query(default="pdf", description="Formato de salida: 'pdf', 'md' o 'txt'."),
):
    """
    ## Exportación del informe ejecutivo de accesibilidad (Issue #40)

    Genera el informe de cierre del proceso de adaptación e incluye:

    - **`pdf`** (por defecto): informe maquetado con tablas comparativas, métricas y disclaimer.
    - **`md`**: Markdown estructurado con tablas GitHub Flavored.
    - **`txt`**: texto plano legible UTF-8.

    Contenido del informe:
    - **Resumen ejecutivo** en lenguaje natural.
    - **Puntaje antes y después** de la adaptación (el puntaje posterior es
      calculado con el motor de reglas determinístico, sin consumo de tokens de IA).
    - **Detalle por barrera**: categoría, severidad y decisión docente.
    - **Métricas consolidadas** de barreras y propuestas.
    - **Disclaimer** de carácter orientativo del puntaje (WCAG).

    > **Nota técnica (#62):** En la versión de producción con Cloudflare R2,
    > este endpoint devolverá una URL firmada de descarga en lugar de transmitir
    > los bytes directamente.
    """
    try:
        report = adaptation_service.build_final_report(analysis_id)
    except AdaptationNotFoundError as e:
        raise HTTPException(status_code=404, detail=str(e))

    try:
        data, content_type = export_service.export_report(report, format)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    filename = export_service.build_filename("informe_accesibilidad", analysis_id, format)
    return StreamingResponse(
        io.BytesIO(data),
        media_type=content_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
