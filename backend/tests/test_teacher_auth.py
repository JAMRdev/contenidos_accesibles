"""
Login de docentes con Clerk (Issue #60): /analysis y /adaptations exigen el token de sesión
solo si CLERK_JWT_KEY está configurada.

Los tokens se firman con una clave RSA de prueba (como en test_admin_auth.py): ningún test
hace llamadas de red.
"""
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.middleware.base import BaseHTTPMiddleware

from app.admin import session, test_web_auth
from app.api.v1.router import api_router
from app.core import rate_limit
from app.core.config import settings
from app.db.database import init_db

FRONT = "https://frontend.example"
WEB = "https://innovalab-web-pruebas.onrender.com"
SAMPLE = {"text": "Leé el texto, subrayá las ideas principales y respondé las preguntas del final."}
ANALYSIS = "/api/v1/analysis/sample?mock=true"
ADAPTATIONS = "/api/v1/adaptations?analysis_id=inexistente"


def _keypair():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    private = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    public = key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return private, public


PRIVATE_PEM, PUBLIC_PEM = _keypair()
OTHER_PRIVATE_PEM, _ = _keypair()


def clerk_token(sub="user_docente", azp=FRONT, exp_in=60, key=PRIVATE_PEM) -> str:
    now = int(time.time())
    claims = {"sub": sub, "azp": azp, "iat": now, "nbf": now, "exp": now + exp_in, "sid": "sess_1"}
    return jwt.encode(claims, key, algorithm="RS256")


def bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "BACKEND_CORS_ORIGINS", [FRONT])
    monkeypatch.setattr(settings, "TEST_WEB_URL", None)
    monkeypatch.setattr(settings, "CLERK_JWT_KEY", None)
    monkeypatch.setattr(settings, "CLERK_SECRET_KEY", None)
    init_db()
    session.reset()
    rate_limit.reset()
    app = FastAPI()
    app.add_middleware(BaseHTTPMiddleware, dispatch=test_web_auth.require_test_web_session)
    app.include_router(api_router, prefix=settings.API_V1_STR)
    return TestClient(app)


@pytest.fixture
def teacher_auth_on(monkeypatch):
    monkeypatch.setattr(settings, "CLERK_JWT_KEY", PUBLIC_PEM)
    monkeypatch.setattr(settings, "CLERK_SECRET_KEY", SecretStr("sk_test_prueba"))


# ---------------------------------------------------------------------------
# Sin CLERK_JWT_KEY: la API sigue anónima (local y hasta que el Frontend mande el token)
# ---------------------------------------------------------------------------

def test_sin_clave_la_api_sigue_anonima(client):
    assert not settings.teacher_auth_enabled
    assert client.post(ANALYSIS, json=SAMPLE).status_code == 200
    assert client.get(ADAPTATIONS).status_code == 404  # pasó el login: el diagnóstico no existe


# ---------------------------------------------------------------------------
# Con CLERK_JWT_KEY
# ---------------------------------------------------------------------------

def test_token_valido_puede_usar_la_api(client, teacher_auth_on):
    headers = bearer(clerk_token())
    assert client.post(ANALYSIS, json=SAMPLE, headers=headers).status_code == 200
    assert client.get(ADAPTATIONS, headers=headers).status_code == 404  # pasó el login: el diagnóstico no existe


def test_sin_token_responde_401_tambien_en_modo_mock(client, teacher_auth_on):
    response = client.post(ANALYSIS, json=SAMPLE)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "Iniciá sesión" in response.json()["detail"]
    assert client.get(ADAPTATIONS).status_code == 401


@pytest.mark.parametrize(
    "token",
    [
        pytest.param(clerk_token(exp_in=-60), id="vencido"),
        pytest.param(clerk_token(azp="https://otro-sitio.example"), id="otro-origen"),
        pytest.param(clerk_token(key=OTHER_PRIVATE_PEM), id="firmado-con-otra-clave"),
        pytest.param("no-es-un-jwt", id="mal-formado"),
        pytest.param("oat_token_de_maquina", id="no-es-de-sesion"),
    ],
)
def test_token_invalido_responde_401(client, teacher_auth_on, token):
    response = client.post(ANALYSIS, json=SAMPLE, headers=bearer(token))
    assert response.status_code == 401
    assert "Volvé a iniciar sesión" in response.json()["detail"]


def test_esquema_distinto_de_bearer_cuenta_como_sin_token(client, teacher_auth_on):
    response = client.post(ANALYSIS, json=SAMPLE, headers={"Authorization": f"Basic {clerk_token()}"})
    assert response.status_code == 401


def test_health_sigue_publico(client, teacher_auth_on):
    assert client.get("/api/v1/health").status_code == 200


def test_la_web_de_pruebas_sigue_con_su_propio_login(client, teacher_auth_on, monkeypatch):
    monkeypatch.setattr(settings, "TEST_WEB_URL", WEB + "/")
    web_token = session.create_token(session.local_subject(), purpose=session.TEST_WEB)
    response = client.post(ANALYSIS, json=SAMPLE, headers={"Origin": WEB, **bearer(web_token)})
    assert response.status_code == 200
