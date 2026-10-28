"""
Pruebas exhaustivas para el manejo de errores uniforme y estabilidad del procesamiento (Issue #42).

Verifica:
1. Formato común de error (`code`, `message`, `detail`) en todos los endpoints HTTP.
2. Degradación elegante a motor de solo-reglas ante fallas del proveedor de IA (503, 429, 502, errores de red).
3. Preservación de encabezados críticos (como Retry-After en 429).
4. No filtración de contenido del material educativo en los logs de error (privacidad).
"""
import io
import logging
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core import rate_limit
from app.core.config import settings
from app.db.database import init_db
from app.main import app
from app.services.key_rotation_service import AllKeysExhaustedError
from app.services.llm.base import LLMInvalidResponseError, LLMUnavailableError


@pytest.fixture(autouse=True)
def setup_db():
    init_db()


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# 1. Esquema uniforme de errores HTTP (code, message, detail)
# ---------------------------------------------------------------------------


def test_error_schema_404_not_found(client):
    """404 devuelve código 'not_found', mensaje y detail coherentes."""
    res = client.get("/api/v1/adaptations/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404
    data = res.json()
    assert data["code"] == "not_found"
    assert "no encontrad" in data["message"].lower() or "no existe" in data["message"].lower()
    assert "detail" in data


def test_error_schema_404_route_not_found(client):
    """Ruta inexistente devuelve el esquema uniforme con código 'not_found'."""
    res = client.get("/api/v1/ruta-inexistente-xyz")
    assert res.status_code == 404
    data = res.json()
    assert data["code"] == "not_found"
    assert "message" in data
    assert "detail" in data


def test_error_schema_422_validation_error(client):
    """Error de validación Pydantic devuelve 'validation_error', message y lista en detail."""
    res = client.post("/api/v1/documents/process-text", json={})
    assert res.status_code == 422
    data = res.json()
    assert data["code"] == "validation_error"
    assert "validación" in data["message"].lower()
    assert isinstance(data["detail"], list)
    assert len(data["detail"]) > 0


def test_error_schema_422_sensitive_data(client):
    """Detección de datos sensibles devuelve 'sensitive_data_detected' y propaga hallazgos."""
    text = "Alumno: Juan Pérez - DNI 30.123.456 - Nota: 9"
    res = client.post("/api/v1/analysis/sample?mock=true", json={"text": text})
    assert res.status_code == 422
    data = res.json()
    assert data["code"] == "sensitive_data_detected"
    assert "datos personales" in data["message"].lower()
    assert "findings" in data
    assert data["total"] >= 1
    # Compatibilidad con accesos existentes a través de data["detail"]
    assert data["detail"]["code"] == "sensitive_data_detected"
    assert len(data["detail"]["findings"]) >= 1


def test_error_schema_400_bad_request(client):
    """400 devuelve 'bad_request', mensaje y detail."""
    res = client.post(
        "/api/v1/analysis/pdf?mock=true",
        files={"file": ("invalido.txt", io.BytesIO(b"texto no pdf"), "text/plain")},
    )
    assert res.status_code == 400
    data = res.json()
    assert data["code"] == "bad_request"
    assert "pdf" in data["message"].lower()
    assert "detail" in data


def test_error_schema_429_rate_limit(client, monkeypatch):
    """429 devuelve 'rate_limit_exceeded', Retry-After header intacto y formato común."""
    rate_limit.reset()
    monkeypatch.setattr(settings, "AI_RATE_LIMIT_PER_IP_MINUTE", 1)
    monkeypatch.setattr(rate_limit, "_now", lambda: 1000.0)

    with patch("app.services.llm_service.llm_service.ping_connection", new_callable=AsyncMock) as mock_ping:
        mock_ping.return_value = {"status": "ok"}
        # Primera llamada pasa
        r1 = client.post("/api/v1/ai/test-connection")
        assert r1.status_code == 200

        # Segunda llamada excede
        r2 = client.post("/api/v1/ai/test-connection")
        assert r2.status_code == 429
        data = r2.json()
        assert data["code"] == "rate_limit_exceeded"
        assert "Retry-After" in r2.headers
        assert "message" in data
        assert "detail" in data
    rate_limit.reset()


def test_error_schema_413_payload_too_large(client):
    """Subida que excede el límite de 10 MB devuelve 413 con formato uniforme."""
    big_dummy_pdf = b"%PDF-" + b"0" * (10 * 1024 * 1024 + 10)
    res = client.post(
        "/api/v1/analysis/pdf",
        files={"file": ("grande.pdf", io.BytesIO(big_dummy_pdf), "application/pdf")},
    )
    assert res.status_code == 413
    data = res.json()
    assert data["code"] == "payload_too_large"
    assert "10 mb" in data["message"].lower() or "límite" in data["message"].lower()
    assert "detail" in data


# ---------------------------------------------------------------------------
# 2. Degradación elegante a motor de solo-reglas (Acceptance Criteria #42)
# ---------------------------------------------------------------------------

_SAMPLE_TEXT = (
    "Actividad pedagógica sobre la Revolución de Mayo de 1810 en el Río de la Plata.\n\n"
    "Esta es una oración deliberadamente muy extensa que supera con creces el límite recomendado de palabras "
    "establecido para evaluar la claridad textual, acumulando frases subordinadas, conceptos históricos diversos y "
    "detalles sobre los sucesos de la Primera Junta de Gobierno sin una puntuación intermedia adecuada que permita una pausa.\n\n"
    "1. Leé el texto y completá las actividades señaladas."
)


def test_graceful_degradation_when_llm_unavailable(client):
    """
    Si el proveedor de IA está caído (503 / red), el análisis no falla:
    degrada automáticamente a reglas con HTTP 200, calcula el puntaje y reporta advertencia.
    """
    with patch(
        "app.services.accessibility_service.llm_service.generate_structured",
        new_callable=AsyncMock,
        side_effect=LLMUnavailableError("Ningún proveedor de IA pudo responder."),
    ):
        res = client.post("/api/v1/analysis/sample", json={"text": _SAMPLE_TEXT})
        assert res.status_code == 200, f"Esperado 200 con degradación, recibido {res.status_code}: {res.text}"
        data = res.json()

        # Metadatos indican fallback al motor de reglas
        assert data["metadata"]["provider"] == "rules"
        assert data["metadata"]["model"] == "static-rules"
        assert "reglas" in data["metadata"]["connection"].lower()

        # Puntaje proviene 100% de reglas
        breakdown = data["score_breakdown"]
        assert breakdown["ai_score"] is None
        assert breakdown["ai_weight"] == 0.0
        assert breakdown["rules_weight"] == 1.0
        assert breakdown["final_score"] == breakdown["rules_score"]
        assert data["estimated_score"] == breakdown["rules_score"]

        # Advertencia clara al usuario
        assert any("motor de reglas" in w.lower() for w in data["warnings"])

        # Las barreras detectadas provienen de las reglas automáticas
        assert len(data["barriers"]) > 0
        assert all(b.get("source") == "rules" for b in data["barriers"])

        # Las 3 dimensiones están evaluadas por reglas
        dim_categories = {d["category"] for d in data["dimensions"]}
        assert {"comprension", "estructura", "accesibilidad_visual"}.issubset(dim_categories)


def test_graceful_degradation_when_keys_exhausted(client):
    """
    Si se agotaron las API keys o cuotas de rotación (429 de proveedores),
    el análisis degrada a solo reglas en lugar de responder 429 al docente.
    """
    with patch(
        "app.services.accessibility_service.llm_service.generate_structured",
        new_callable=AsyncMock,
        side_effect=AllKeysExhaustedError("Todas las conexiones de IA configuradas están agotadas."),
    ):
        res = client.post("/api/v1/analysis/sample", json={"text": _SAMPLE_TEXT})
        assert res.status_code == 200
        data = res.json()
        assert data["metadata"]["provider"] == "rules"
        assert data["score_breakdown"]["ai_score"] is None
        assert any("motor de reglas" in w.lower() for w in data["warnings"])


def test_graceful_degradation_when_model_returns_invalid_json(client):
    """
    Si el modelo devuelve un JSON inválido o que no respeta el esquema (502),
    el análisis no se interrumpe y degrada a solo reglas.
    """
    with patch(
        "app.services.accessibility_service.llm_service.generate_structured",
        new_callable=AsyncMock,
        side_effect=LLMInvalidResponseError("Respuesta no estructurada"),
    ):
        res = client.post("/api/v1/analysis/sample", json={"text": _SAMPLE_TEXT})
        assert res.status_code == 200
        data = res.json()
        assert data["metadata"]["provider"] == "rules"
        assert data["score_breakdown"]["ai_score"] is None


def test_logs_do_not_contain_material_text_on_failure(client, caplog):
    """
    Criterio de privacidad: ningún fragmento ni texto confidencial del material
    debe escribirse en los logs ante caídas o excepciones de IA.
    """
    secret_material = (
        "MATERIAL_CONFIDENCIAL_DOCENTE_X999: Este contenido educativo no debe aparecer en logs. "
        "Oración larga adicional para satisfacer el procesamiento mínimo del motor de reglas."
    )
    with caplog.at_level(logging.WARNING):
        with patch(
            "app.services.accessibility_service.llm_service.generate_structured",
            new_callable=AsyncMock,
            side_effect=LLMUnavailableError("Error simulado de conexión con el proveedor"),
        ):
            res = client.post("/api/v1/analysis/sample", json={"text": secret_material})
            assert res.status_code == 200

    # Verificar que el texto del material NUNCA fue volcado en los logs
    assert "MATERIAL_CONFIDENCIAL_DOCENTE_X999" not in caplog.text
    # Pero el log sí debe advertir de la degradación y el tipo de error
    assert "Degradando a solo reglas automáticas" in caplog.text
    assert "LLMUnavailableError" in caplog.text
