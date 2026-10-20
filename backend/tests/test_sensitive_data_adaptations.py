"""
Detector de datos sensibles en el fragmento que escribe el docente al pedir una adaptación (Issue #66).

El chequeo corre antes de buscar el diagnóstico, así que alcanza con un analysis_id inexistente:
si el fragmento tiene datos personales se lanza SensitiveDataError; si no los tiene (o se
descartaron), el flujo sigue y falla recién al buscar el diagnóstico (AdaptationNotFoundError).
"""

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.schemas.adaptation import AdaptationRequest
from app.services import adaptation_service
from app.services.adaptation_service import AdaptationNotFoundError
from app.services.sensitive_data_service import SensitiveDataError, detect

LLM_TARGET = "app.services.adaptation_service.llm_service.generate_structured"

SENSITIVE_FRAGMENT = "Alumno: Juan Pérez. Leé el texto y respondé las preguntas."


def _request(fragment: str | None) -> AdaptationRequest:
    # model_construct: sin validación, para no depender de un diagnóstico real en la base.
    return AdaptationRequest.model_construct(
        analysis_id=str(uuid.uuid4()),
        barrier_id="bar-1",
        adaptation_type="lenguaje_claro",
        fragment=fragment,
    )


def test_fragmento_con_datos_personales_no_llega_a_la_ia():
    async def _run():
        with patch(LLM_TARGET, new_callable=AsyncMock) as llm:
            with pytest.raises(SensitiveDataError) as exc_info:
                await adaptation_service.create_proposal(_request(SENSITIVE_FRAGMENT), owner="anon", mock=False)
        assert llm.call_count == 0
        assert [f.type for f in exc_info.value.findings] == ["nombre_rotulado"]

    asyncio.run(_run())


def test_fragmento_con_datos_personales_se_bloquea_tambien_en_mock():
    with pytest.raises(SensitiveDataError):
        asyncio.run(adaptation_service.create_proposal(_request(SENSITIVE_FRAGMENT), owner="anon", mock=True))


def test_fragmento_con_hallazgos_descartados_sigue_el_flujo_normal():
    ids = [f.id for f in detect(SENSITIVE_FRAGMENT)]
    with pytest.raises(AdaptationNotFoundError):
        asyncio.run(
            adaptation_service.create_proposal(
                _request(SENSITIVE_FRAGMENT), owner="anon", mock=True, dismissed_findings=ids
            )
        )


def test_fragmento_limpio_sigue_el_flujo_normal():
    with pytest.raises(AdaptationNotFoundError):
        asyncio.run(
            adaptation_service.create_proposal(_request("Leé el texto y respondé las preguntas."), owner="anon", mock=True)
        )


def test_sin_fragmento_no_cambia_nada():
    """Sin fragmento se usa el texto del diagnóstico, que ya pasó por el detector al analizarlo."""
    with pytest.raises(AdaptationNotFoundError):
        asyncio.run(adaptation_service.create_proposal(_request(None), owner="anon", mock=True))