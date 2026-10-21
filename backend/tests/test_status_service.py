"""
Sección Estado de /admin: chequeos de los servicios conectados, caché corta y URLs.
Sin red: las URLs externas responden con un httpx.MockTransport.
"""
import asyncio

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.admin import session
from app.admin.router import PREFIX
from app.admin.router import router as admin_router
from app.core.config import settings
from app.db import api_key_repository as repo
from app.db.database import init_db
from app.services import status_service

USER, PASSWORD = "admin", "clave-de-prueba"


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Servicios configurados y respuestas externas controladas por `routes`."""
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "test.db"))
    init_db()
    monkeypatch.setattr(settings, "D1_GATEWAY_URL", "https://gw.example.workers.dev")
    monkeypatch.setattr(settings, "TEST_WEB_URL", "https://pruebas.onrender.com")
    # FRONTEND_URL repetido en CORS (con barra final): se chequea una sola vez
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://app.vercel.app/")
    monkeypatch.setattr(settings, "BACKEND_CORS_ORIGINS", ["https://app.vercel.app", "http://localhost:5173"])
    monkeypatch.setattr(settings, "ADMIN_CLERK_PUBLISHABLE_KEY", None)
    monkeypatch.setattr(settings, "USERS_CLERK_SECRET_KEY", None)
    routes: dict[str, httpx.Response | Exception] = {
        "gw.example.workers.dev": httpx.Response(200, json={"ok": True}),
        "pruebas.onrender.com": httpx.Response(200, text="ok"),
        "app.vercel.app": httpx.Response(200, text="ok"),
    }
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        answer = routes[request.url.host]
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(status_service, "_transport", httpx.MockTransport(handler))
    monkeypatch.setattr(status_service, "_cache", None)
    monkeypatch.setattr(status_service, "_since", {})
    return routes, calls


def _status() -> dict:
    return asyncio.run(status_service.get_status())


def _by_id(data: dict) -> dict:
    return {s["id"]: s for s in data["services"]}


def _connection(label="Groq", **kwargs) -> int:
    fields = dict(label=label, raw_key="gsk_prueba_123456", provider="groq", base_url=None, model="modelo-x",
                  priority=10, extra_params={}, rpm_threshold=None, tpm_threshold=None, rpd_threshold=None, tpd_threshold=None)
    fields.update(kwargs)
    return repo.create(**fields)


def test_todo_en_orden(env):
    _connection()
    data = _status()
    s = _by_id(data)
    assert [x["id"] for x in data["services"]] == ["api", "database", "d1-gateway", "storage", "ai", "clerk", "clerk-users", "test-web", "frontend"]
    assert {k: s[k]["state"] for k in s} == {
        "api": "ok", "database": "ok", "d1-gateway": "ok", "storage": "ok", "ai": "ok",
        "clerk": "unconfigured", "clerk-users": "unconfigured", "test-web": "ok", "frontend": "ok",
    }
    assert data["overall"] == "ok" and data["counts"]["unconfigured"] == 2
    assert s["test-web"]["platform"] == "Render" and s["frontend"]["platform"] == "Vercel"
    assert s["frontend"]["url"] == "https://app.vercel.app"  # localhost no se chequea
    assert {"label": "En uso", "value": "Groq · modelo-x"} in s["ai"]["facts"]


def test_un_servicio_caido_o_con_error(env):
    routes, _ = env
    routes["gw.example.workers.dev"] = httpx.ConnectError("sin red")
    routes["pruebas.onrender.com"] = httpx.Response(404)
    routes["app.vercel.app"] = httpx.Response(503)
    s = _by_id(_status())
    assert s["d1-gateway"]["state"] == "down" and "ConnectError" in s["d1-gateway"]["detail"]
    assert s["test-web"]["state"] == "degraded" and s["test-web"]["detail"] == "Respondió 404"
    assert s["frontend"]["state"] == "down"


def test_worker_sin_ok_y_timeout(env):
    routes, _ = env
    routes["gw.example.workers.dev"] = httpx.Response(200, json={"ok": False})
    routes["pruebas.onrender.com"] = httpx.ReadTimeout("lento")
    s = _by_id(_status())
    assert s["d1-gateway"]["state"] == "degraded"
    assert s["test-web"]["state"] == "down" and "No respondió" in s["test-web"]["detail"]


def test_ia_sin_conexiones_o_agotadas(env):
    assert _by_id(_status())["ai"]["state"] == "unconfigured"
    key_id = _connection()
    repo.set_enabled(key_id, False)
    status_service._cache = None
    ai = _by_id(_status())["ai"]
    assert ai["state"] == "down" and ai["detail"].startswith("Todas las conexiones")


def test_sin_configurar_no_cuenta_para_el_estado_general(env, monkeypatch):
    monkeypatch.setattr(settings, "D1_GATEWAY_URL", None)
    monkeypatch.setattr(settings, "TEST_WEB_URL", None)
    monkeypatch.setattr(settings, "FRONTEND_URL", "")
    monkeypatch.setattr(settings, "BACKEND_CORS_ORIGINS", ["http://localhost:5173"])
    s = _by_id(_status())
    assert s["d1-gateway"]["state"] == s["test-web"]["state"] == s["frontend"]["state"] == "unconfigured"


def test_login_de_docentes_con_la_api_de_clerk(env, monkeypatch):
    """Issue #104: el panel lee nombre y email de los docentes con USERS_CLERK_SECRET_KEY."""
    routes, calls = env
    assert "USERS_CLERK_SECRET_KEY" in _by_id(_status())["clerk-users"]["detail"]

    monkeypatch.setattr(settings, "USERS_CLERK_SECRET_KEY", SecretStr("sk_test_docentes"))
    routes["api.clerk.com"] = httpx.Response(200, json={"keys": []})
    status_service._cache = None
    ok = _by_id(_status())["clerk-users"]
    assert ok["state"] == "ok" and ok["url"] == status_service.CLERK_API_URL
    assert any(f["label"] == "Login exigido" for f in ok["facts"])

    routes["api.clerk.com"] = httpx.Response(401)
    status_service._cache = None
    bad = _by_id(_status())["clerk-users"]
    assert bad["state"] == "degraded" and "USERS_CLERK_SECRET_KEY" in bad["detail"]
    assert "sk_test_docentes" not in str(bad)


def test_un_chequeo_que_explota_no_tira_la_seccion(env, monkeypatch):
    async def broken(_client):
        raise ValueError("bug")

    monkeypatch.setattr(status_service, "check_clerk", broken)
    clerk = _by_id(_status())["clerk"]
    assert clerk["state"] == "down" and "ValueError" in clerk["detail"]


def test_la_cache_evita_repetir_los_chequeos(env):
    _, calls = env
    first = _status()
    n = len(calls)
    assert _status() is first and len(calls) == n
    status_service._cache = (status_service._cache[0] - status_service.CACHE_TTL_S - 1, first)
    _status()
    assert len(calls) == 2 * n


def test_since_cambia_solo_cuando_cambia_el_estado(env):
    routes, _ = env
    since = _by_id(_status())["test-web"]["since"]
    status_service._cache = None
    assert _by_id(_status())["test-web"]["since"] == since
    status_service._since["test-web"] = ("ok", "2026-01-01T00:00:00Z")
    routes["pruebas.onrender.com"] = httpx.Response(500)
    status_service._cache = None
    assert _by_id(_status())["test-web"]["since"] != "2026-01-01T00:00:00Z"


def test_frontend_url_mas_otros_origenes_de_cors(env, monkeypatch):
    monkeypatch.setattr(settings, "FRONTEND_URL", "https://contenidos-accesibles.vercel.app/")
    assert status_service.frontend_origins() == ["https://contenidos-accesibles.vercel.app", "https://app.vercel.app"]


def test_links_sin_secretos(env):
    links = status_service.links("https://api.example.com/", PREFIX)
    urls = [link["url"] for link in links]
    assert "https://api.example.com/docs" in urls and "https://api.example.com/admin" in urls
    assert "https://gw.example.workers.dev/health" in urls and "https://app.vercel.app" in urls
    assert not any("localhost" in u for u in urls)


def test_endpoint_requiere_sesion_y_la_ven_todos_los_roles(env, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_USERNAME", USER)
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", SecretStr(PASSWORD))
    session.reset()
    app = FastAPI()
    app.include_router(admin_router, prefix=PREFIX)
    client = TestClient(app)
    assert client.get(PREFIX + "/api/status").status_code == 401
    client.post(PREFIX + "/login", data={"username": USER, "password": PASSWORD}, follow_redirects=False)
    r = client.get(PREFIX + "/api/status")
    assert r.status_code == 200
    body = r.json()
    assert body["overall"] in {"ok", "degraded", "down"} and body["links"]
    assert "gsk_prueba" not in r.text and "D1_GATEWAY_TOKEN" not in r.text
