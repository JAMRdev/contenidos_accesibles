"""
Login de la web de pruebas (Render): mismo acceso que el panel, token Bearer de 8 h y
exigencia del backend para toda llamada cuyo Origin sea el de TEST_WEB_URL.
"""
import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from pydantic import SecretStr
from starlette.middleware.base import BaseHTTPMiddleware

from app.admin import clerk_auth, session, test_web_auth
from app.api.v1.router import api_router
from app.core import rate_limit
from app.core.config import settings
from app.db import admin_member_repository as members
from app.db.database import init_db

WEB = "https://innovalab-web-pruebas.onrender.com"
OTHER = "https://frontend.example"
USER, PASSWORD = "admin", "clave-de-prueba"
SAMPLE = {"text": "Leé el texto, subrayá las ideas principales y respondé las preguntas del final."}

_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
PUBLIC_PEM = _key.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo).decode()


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "ADMIN_USERNAME", USER)
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", SecretStr(PASSWORD))
    monkeypatch.setattr(settings, "ADMIN_AUTH", "ambos")
    monkeypatch.setattr(settings, "TEST_WEB_URL", WEB + "/")
    monkeypatch.setattr(settings, "ADMIN_CLERK_PUBLISHABLE_KEY", None)
    monkeypatch.setattr(settings, "ADMIN_CLERK_SECRET_KEY", None)
    monkeypatch.setattr(settings, "ADMIN_CLERK_JWT_KEY", None)
    init_db()
    session.reset()
    rate_limit.reset()
    # Mismo orden que app/main.py: el login de la web adentro, CORS por fuera
    app = FastAPI()
    app.add_middleware(BaseHTTPMiddleware, dispatch=test_web_auth.require_test_web_session)
    app.add_middleware(CORSMiddleware, allow_origins=[WEB, OTHER], allow_credentials=True,
                       allow_methods=["*"], allow_headers=["*"])
    app.include_router(api_router, prefix=settings.API_V1_STR)
    app.include_router(test_web_auth.router, prefix=settings.API_V1_STR)
    return TestClient(app)


@pytest.fixture
def clerk_on(monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_CLERK_PUBLISHABLE_KEY", "pk_test_ZGVtby5jbGVyay5hY2NvdW50cy5kZXYk")
    monkeypatch.setattr(settings, "ADMIN_CLERK_SECRET_KEY", SecretStr("sk_test_prueba"))
    monkeypatch.setattr(settings, "ADMIN_CLERK_JWT_KEY", PUBLIC_PEM)


def web(token=None):
    headers = {"Origin": WEB}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def local_login(client) -> dict:
    r = client.post("/api/v1/test-web/auth/login", json={"username": USER, "password": PASSWORD}, headers=web())
    assert r.status_code == 200
    return r.json()


def clerk_token(azp=WEB, exp_in=60):
    now = int(time.time())
    return jwt.encode({"sub": "user_1", "azp": azp, "iat": now, "nbf": now, "exp": now + exp_in}, PRIVATE_PEM, algorithm="RS256")


# ---------------------------------------------------------------------------
# Configuración y login
# ---------------------------------------------------------------------------

def test_config_sin_clerk(client):
    r = client.get("/api/v1/test-web/auth/config", headers=web())
    assert r.status_code == 200
    assert r.json() == {"required": True, "clerk": None, "local": True}


def test_config_con_clerk(client, clerk_on):
    clerk = client.get("/api/v1/test-web/auth/config", headers=web()).json()["clerk"]
    assert clerk == {"publishable_key": settings.ADMIN_CLERK_PUBLISHABLE_KEY, "frontend_api": "demo.clerk.accounts.dev"}


def test_sin_test_web_url_no_se_exige_login(client, monkeypatch):
    monkeypatch.setattr(settings, "TEST_WEB_URL", None)
    assert client.get("/api/v1/test-web/auth/config").json()["required"] is False
    assert client.post("/api/v1/analysis/sample?mock=true", json=SAMPLE, headers=web()).status_code == 200


def test_login_local_entrega_token_de_8_horas(client):
    data = local_login(client)
    assert data["user"]["username"] == USER and data["user"]["role"] == "admin"
    assert 0 < data["expires_at"] - time.time() <= session.SESSION_TTL_S
    me = client.get("/api/v1/test-web/auth/me", headers=web(data["token"]))
    assert me.status_code == 200 and me.json()["user"]["can_edit"] is True


def test_login_local_incorrecto_y_bloqueo(client):
    for _ in range(session.MAX_FAILED_LOGINS):
        r = client.post("/api/v1/test-web/auth/login", json={"username": USER, "password": "mala"}, headers=web())
        assert r.status_code == 401
    r = client.post("/api/v1/test-web/auth/login", json={"username": USER, "password": PASSWORD}, headers=web())
    assert r.status_code == 429


def test_modo_solo_clerk_desactiva_el_login_local(client, clerk_on, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_AUTH", "clerk")
    r = client.post("/api/v1/test-web/auth/login", json={"username": USER, "password": PASSWORD}, headers=web())
    assert r.status_code == 404


def test_login_con_clerk_valida_el_origen_de_la_web(client, clerk_on, monkeypatch):
    members.add("JAMRdev", "admin")
    identity = clerk_auth.ClerkIdentity("github", "JAMRdev", "Jose", None, "user_1")
    monkeypatch.setattr(clerk_auth, "fetch_identity", lambda user_id: identity)
    r = client.post("/api/v1/test-web/auth/clerk", json={"token": clerk_token()}, headers=web())
    assert r.status_code == 200 and r.json()["user"]["username"] == "JAMRdev"
    # Un token emitido para el panel (otro origen) no sirve en la web de pruebas
    r = client.post("/api/v1/test-web/auth/clerk", json={"token": clerk_token(azp="http://testserver")}, headers=web())
    assert r.status_code == 401


def test_login_con_clerk_de_alguien_sin_acceso(client, clerk_on, monkeypatch):
    identity = clerk_auth.ClerkIdentity("github", "intruso", None, None, "user_1")
    monkeypatch.setattr(clerk_auth, "fetch_identity", lambda user_id: identity)
    r = client.post("/api/v1/test-web/auth/clerk", json={"token": clerk_token()}, headers=web())
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Lo que hace cumplir el backend
# ---------------------------------------------------------------------------

def test_desde_la_web_sin_token_responde_401_con_cors(client):
    r = client.post("/api/v1/analysis/sample?mock=true", json=SAMPLE, headers=web())
    assert r.status_code == 401
    assert r.json()["code"] == "test_web_login_required"
    assert r.headers["access-control-allow-origin"] == WEB  # el navegador puede leer el error


def test_desde_la_web_con_token_funciona(client):
    token = local_login(client)["token"]
    assert client.post("/api/v1/analysis/sample?mock=true", json=SAMPLE, headers=web(token)).status_code == 200


def test_preflight_y_health_no_piden_token(client):
    r = client.options("/api/v1/analysis/sample", headers={
        "Origin": WEB, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type",
    })
    assert r.status_code == 200
    assert client.get("/api/v1/health", headers=web()).status_code == 200


def test_otros_origenes_siguen_como_antes(client):
    assert client.post("/api/v1/analysis/sample?mock=true", json=SAMPLE, headers={"Origin": OTHER}).status_code == 200
    assert client.post("/api/v1/analysis/sample?mock=true", json=SAMPLE).status_code == 200


def test_la_cookie_del_panel_no_sirve_como_token_ni_al_reves(client):
    panel_token = session.create_token(session.local_subject())
    r = client.post("/api/v1/analysis/sample?mock=true", json=SAMPLE, headers=web(panel_token))
    assert r.status_code == 401
    web_token = session.create_token(session.local_subject(), purpose=session.TEST_WEB)
    assert session.read_token(web_token) is None  # como cookie del panel no vale


def test_token_vencido_o_de_un_usuario_quitado(client):
    expired = session.create_token(session.local_subject(), now=0, purpose=session.TEST_WEB)
    assert client.post("/api/v1/analysis/sample?mock=true", json=SAMPLE, headers=web(expired)).status_code == 401
    member = members.add("Jos022", "viewer")
    token = session.create_token(session.github_subject("Jos022"), purpose=session.TEST_WEB)
    assert client.post("/api/v1/analysis/sample?mock=true", json=SAMPLE, headers=web(token)).status_code == 200
    members.delete(member["id"])
    assert client.post("/api/v1/analysis/sample?mock=true", json=SAMPLE, headers=web(token)).status_code == 401


def test_solo_lectura_solo_puede_usar_mock(client):
    members.add("Silvanam1", "viewer")
    token = session.create_token(session.github_subject("Silvanam1"), purpose=session.TEST_WEB)
    assert client.post("/api/v1/analysis/sample?mock=true", json=SAMPLE, headers=web(token)).status_code == 200
    r = client.post("/api/v1/analysis/sample", json=SAMPLE, headers=web(token))
    assert r.status_code == 403 and "solo lectura" in r.json()["detail"]
    assert client.post("/api/v1/ai/test-gemini", headers=web(token)).status_code == 403


def test_admin_puede_usar_la_ia_real(client):
    token = local_login(client)["token"]
    r = client.post("/api/v1/analysis/sample", json=SAMPLE, headers=web(token))
    assert r.status_code not in (401, 403)  # sin conexiones de IA en los tests: falla después, no por permisos
