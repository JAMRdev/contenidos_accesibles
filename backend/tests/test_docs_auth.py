"""
/docs, /redoc y openapi.json fuera de development: protegidos con la sesión del panel
(Clerk o el usuario y la contraseña del .env; admin y solo lectura pueden verlos).
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.admin import session
from app.admin.router_v2 import router as admin_v2_router
from app.core.config import Settings, settings
from app.core.docs import setup_protected_docs
from app.db import admin_member_repository as members
from app.db.database import init_db

OPENAPI_URL = "/api/v1/openapi.json"
DOC_PAGES = ["/docs", "/redoc"]
USER, PASSWORD = "admin", "clave-de-prueba"


@pytest.fixture
def protected_client(tmp_path, monkeypatch):
    """App como en production: docs de FastAPI apagados y re-registrados detrás de la sesión."""
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "ADMIN_USERNAME", USER)
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", SecretStr(PASSWORD))
    monkeypatch.setattr(settings, "ADMIN_AUTH", "local")
    init_db()
    session.reset()
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok"}

    app.include_router(admin_v2_router, prefix="/adminv2")
    setup_protected_docs(app, OPENAPI_URL)
    return TestClient(app)


def _login(client):
    r = client.post("/adminv2/login", data={"username": USER, "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303


@pytest.mark.parametrize("path", DOC_PAGES)
def test_docs_sin_sesion_redirige_al_login_y_vuelve(protected_client, path):
    r = protected_client.get(path, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == f"/adminv2/login?next={path}"
    assert "www-authenticate" not in r.headers  # sin la ventana nativa de Basic Auth


def test_openapi_sin_sesion_responde_401(protected_client):
    assert protected_client.get(OPENAPI_URL).status_code == 401


def test_basic_auth_ya_no_sirve(protected_client):
    assert protected_client.get(OPENAPI_URL, auth=(USER, PASSWORD)).status_code == 401


def test_login_del_panel_devuelve_a_swagger(protected_client):
    r = protected_client.post("/adminv2/login", data={"username": USER, "password": PASSWORD, "next": "/docs"},
                              follow_redirects=False)
    assert r.headers["location"] == "/docs"
    assert "path=/" in r.headers["set-cookie"].lower()


@pytest.mark.parametrize("path", DOC_PAGES + [OPENAPI_URL])
def test_docs_con_sesion_devuelve_200(protected_client, path):
    _login(protected_client)
    assert protected_client.get(path).status_code == 200


def test_solo_lectura_tambien_ve_la_documentacion(protected_client):
    members.add("Jos022", "viewer")
    protected_client.cookies.set(session.COOKIE_NAME, session.create_token(session.github_subject("Jos022")))
    assert protected_client.get("/docs").status_code == 200


def test_swagger_apunta_al_openapi_protegido(protected_client):
    _login(protected_client)
    assert OPENAPI_URL in protected_client.get("/docs").text


def test_openapi_no_expone_rutas_de_docs_ni_de_login(protected_client):
    _login(protected_client)
    schema = protected_client.get(OPENAPI_URL).json()
    assert "/api/v1/health" in schema["paths"]
    assert not {"/docs", "/redoc", OPENAPI_URL} & set(schema["paths"])
    assert not [p for p in schema["paths"] if p.startswith("/adminv2")]


def test_api_sigue_publica(protected_client):
    assert protected_client.get("/api/v1/health").status_code == 200


def _settings(**overrides) -> Settings:
    base = {"ADMIN_PASSWORD": "admin", "DB_ENCRYPTION_KEY": "k"}
    return Settings(_env_file=None, **(base | overrides))


@pytest.mark.parametrize("env", ["development", "staging", "production"])
def test_docs_password_ya_no_es_obligatoria(monkeypatch, env):
    monkeypatch.delenv("DOCS_PASSWORD", raising=False)
    assert _settings(ENVIRONMENT=env).docs_protected is (env != "development")


def test_variables_docs_viejas_se_ignoran(monkeypatch):
    monkeypatch.setenv("DOCS_USERNAME", "docs")
    monkeypatch.setenv("DOCS_PASSWORD", "vieja")
    assert not hasattr(_settings(ENVIRONMENT="production"), "DOCS_PASSWORD")
