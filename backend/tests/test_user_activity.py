"""
Historial y estadísticas de uso por usuario en /admin (Issue #104).

Todo con la IA en modo mock (?mock=true) y el almacenamiento local: ningún test llama a un
proveedor ni a Clerk. Los tokens de docente se firman con la clave de prueba de test_permissions.
"""
import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.admin import session
from app.core import rate_limit, teacher_auth
from app.core.config import settings
from app.core.permissions import ADMIN, Caller
from app.db import user_activity_repository as user_activity
from app.db.database import get_connection, init_db
from app.main import app
from app.services import retention_service, storage_service, user_metrics_service, user_profile_service
from app.services.llm.base import AdapterResult, Usage
from app.services.llm_service import LLMService
from app.services.storage_service import LocalStorage
from tests.test_permissions import FRONT, PUBLIC_PEM, bearer, clerk_token

USER, PASSWORD = "admin", "clave-de-prueba"
SECRET_TEXT = "Leé el texto de la página 8 sobre los volcanes de Mendoza y respondé las preguntas. " * 6
SECRET_NAME = "Lista de Juan Pérez 3B.pdf"
PDF = b"%PDF-1.7\n% material\n" + b"0" * 900


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    monkeypatch.setattr(settings, "BACKEND_CORS_ORIGINS", [FRONT])
    monkeypatch.setattr(settings, "TEST_WEB_URL", None)
    monkeypatch.setattr(settings, "USERS_CLERK_JWT_KEY", None)
    monkeypatch.setattr(settings, "USERS_CLERK_SECRET_KEY", None)
    monkeypatch.setattr(settings, "ADMIN_USERNAME", USER)
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", SecretStr(PASSWORD))
    init_db()
    session.reset()
    rate_limit.reset()
    storage_service.reset_storage(LocalStorage(str(tmp_path / "storage"), b"secreto-de-prueba"))
    yield TestClient(app, follow_redirects=False)
    storage_service.reset_storage()
    rate_limit.reset()


@pytest.fixture
def teachers(client, monkeypatch):
    """Login de docentes activo: cada request va a nombre del sub del token."""
    monkeypatch.setattr(settings, "USERS_CLERK_JWT_KEY", PUBLIC_PEM)
    return client


def _admin(client) -> TestClient:
    assert client.post("/admin/login", data={"username": USER, "password": PASSWORD}).status_code == 303
    return client


def _path(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}" if parts.query else parts.path


def _rows(user_id: str | None = None) -> list[dict]:
    query = "SELECT * FROM user_activity" + (" WHERE user_id = ?" if user_id else "") + " ORDER BY id"
    with get_connection() as conn:
        return [dict(r) for r in conn.execute(query, (user_id,) if user_id else ())]


def _flow(client, headers: dict | None = None) -> dict:
    """Diagnóstico, propuesta, decisión, subida, enlace, apertura, exportación: todo en modo mock."""
    headers = headers or {}
    analysis = client.post("/api/v1/analysis/sample?mock=true", json={"text": SECRET_TEXT}, headers=headers).json()
    barrier = analysis["barriers"][0]["id"]
    proposal = client.post("/api/v1/adaptations?mock=true", headers=headers, json={
        "analysis_id": analysis["analysis_id"], "barrier_id": barrier, "adaptation_type": "lenguaje_claro",
    }).json()
    decided = client.post(f"/api/v1/adaptations/{proposal['proposal_id']}/decision", headers=headers,
                          json={"decision": "editada", "edited_text": "Texto editado secreto"})
    assert decided.status_code == 200, decided.text
    data = client.post("/api/v1/files/upload-url", headers=headers,
                       json={"filename": SECRET_NAME, "content_type": "application/pdf", "size_bytes": len(PDF)}).json()
    client.put(_path(data["upload"]["url"]), content=PDF, headers=data["upload"]["headers"])
    file_id = data["file"]["file_id"]
    assert client.post(f"/api/v1/files/{file_id}/complete", headers=headers).status_code == 200
    share = client.post(f"/api/v1/files/{file_id}/shares", headers=headers).json()
    client.get(_path(share["url"]))  # sin login: lo abre otra persona
    client.get("/api/v1/export/content", headers=headers, params={"analysis_id": analysis["analysis_id"], "format": "md"})
    return {"analysis": analysis, "proposal": proposal}


# ---------------------------------------------------------------------------
# Registro
# ---------------------------------------------------------------------------

def test_cada_accion_queda_a_nombre_del_usuario_sin_contenido(teachers):
    _flow(teachers, bearer(clerk_token("user_ana")))

    rows = _rows("user_ana")
    assert [r["action"] for r in rows] == [
        "analysis", "proposal", "decision", "upload", "share_created", "share_opened", "export",
    ]
    analysis, proposal, decision = rows[0], rows[1], rows[2]
    assert analysis["format"] == "text" and analysis["mock"] == 1 and analysis["provider"] == "mock"
    assert analysis["score"] is not None and analysis["barriers"] >= 1
    assert proposal["adaptation_type"] == "lenguaje_claro" and proposal["mock"] == 1 and proposal["format"] == "text"
    assert decision["detail"] == "editada" and decision["adaptation_type"] == "lenguaje_claro"
    assert rows[3]["format"] == "pdf" and rows[3]["size_bytes"] == len(PDF)
    assert rows[6]["detail"] == "content:file" and rows[6]["format"] == "md"
    assert all(r["tokens"] == 0 for r in rows)  # mock: sin tokens

    # Nunca el material, el texto editado ni el nombre del archivo
    dump = str(rows)
    assert "volcanes" not in dump and "editado secreto" not in dump and "Juan" not in dump


def test_api_users_primer_y_ultimo_uso_y_rol(teachers):
    token = bearer(clerk_token("user_ana", role=ADMIN))
    teachers.post("/api/v1/analysis/sample?mock=true", json={"text": SECRET_TEXT}, headers=token)
    first = user_activity.get_user("user_ana")
    assert first["source"] == "frontend" and first["role"] == "admin"
    assert first["first_seen_at"] == first["last_seen_at"]

    with get_connection() as conn:
        conn.execute("UPDATE api_users SET first_seen_at = '2026-01-01T00:00:00Z', last_seen_at = '2026-01-01T00:00:00Z'")
    teachers.post("/api/v1/analysis/sample?mock=true", json={"text": SECRET_TEXT}, headers=bearer(clerk_token("user_ana")))
    again = user_activity.get_user("user_ana")
    assert again["first_seen_at"] == "2026-01-01T00:00:00Z" and again["last_seen_at"] > "2026-10-01"
    assert again["role"] == "docente"  # el rol sigue al token


def test_abrir_un_enlace_no_cuenta_como_uso_del_docente(teachers):
    token = bearer(clerk_token("user_ana"))
    data = teachers.post("/api/v1/files/upload-url", headers=token,
                         json={"filename": "clase.pdf", "content_type": "application/pdf", "size_bytes": len(PDF)}).json()
    teachers.put(_path(data["upload"]["url"]), content=PDF, headers=data["upload"]["headers"])
    teachers.post(f"/api/v1/files/{data['file']['file_id']}/complete", headers=token)
    share = teachers.post(f"/api/v1/files/{data['file']['file_id']}/shares", headers=token).json()
    with get_connection() as conn:
        conn.execute("UPDATE api_users SET last_seen_at = '2026-01-01T00:00:00Z'")
    teachers.get(_path(share["url"]))
    assert _rows("user_ana")[-1]["action"] == "share_opened"
    assert user_activity.get_user("user_ana")["last_seen_at"] == "2026-01-01T00:00:00Z"


def test_web_de_pruebas_y_anonimo(client):
    client.post("/api/v1/analysis/sample?mock=true", json={"text": SECRET_TEXT})
    anon = user_activity.get_user("anon")
    assert anon["source"] == "anon" and anon["role"] == "docente"
    assert user_activity.source_of(teacher_auth.owner_for_test_web("github", "JAMRdev")) == "test_web"


def test_llamadas_a_la_ia_a_nombre_de_quien_llama(client, monkeypatch):
    from app.db import api_key_repository as repo

    repo.create(label="Groq", raw_key="gsk_prueba_123456", provider="gemini", base_url=None, model="modelo-x",
                priority=10, extra_params={}, rpm_threshold=None, tpm_threshold=None, rpd_threshold=None, tpd_threshold=None)
    service = LLMService()

    class Adapter:
        async def generate(self, *args, **kwargs):
            return AdapterResult(text="CONECTADO", usage=Usage(prompt_tokens=70, output_tokens=30, total_tokens=100))

    monkeypatch.setattr(service, "_adapter", lambda conn: Adapter())
    asyncio.run(service.ping_connection())  # fuera de un request de la API (p. ej. el panel): no va al historial
    assert _rows() == []

    reset = teacher_auth._caller.set(Caller("user_ana", authenticated=True))
    try:
        asyncio.run(service.ping_connection())
    finally:
        teacher_auth._caller.reset(reset)
    (row,) = _rows("user_ana")
    assert row["action"] == "ai_call" and row["detail"] == "ok" and row["tokens"] == 100
    assert row["provider"] == "gemini" and row["model"] == "modelo-x" and row["format"] == "text"
    assert "gsk_" not in str(row)


def test_cada_429_queda_en_el_historial(teachers, monkeypatch):
    monkeypatch.setattr(settings, "AI_RATE_LIMIT_PER_USER_MINUTE", 1)
    rate_limit._hits["user:user_ana"] = deque([rate_limit._now()])  # ya usó su cupo: no llega a la IA
    r = teachers.post("/api/v1/analysis/sample", json={"text": SECRET_TEXT}, headers=bearer(clerk_token("user_ana")))
    assert r.status_code == 429
    (row,) = _rows("user_ana")
    assert row["action"] == "rate_limited" and row["detail"] == "user"


def test_registrar_nunca_rompe_la_operacion(client, monkeypatch):
    def broken():
        raise RuntimeError("base caída")

    monkeypatch.setattr("app.db.user_activity_repository.get_connection", broken)
    assert client.post("/api/v1/analysis/sample?mock=true", json={"text": SECRET_TEXT}).status_code == 200
    with pytest.raises(ValueError):
        user_activity.record("otra", "anon")


# ---------------------------------------------------------------------------
# Permanencia
# ---------------------------------------------------------------------------

def test_las_estadisticas_sobreviven_al_vencimiento_y_a_las_limpiezas(teachers):
    _flow(teachers, bearer(clerk_token("user_ana")))
    before = len(_rows())
    # Vence todo: diagnósticos, propuestas, decisiones y archivos
    with get_connection() as conn:
        for table in ("analyses", "adaptation_proposals", "adaptation_decisions", "files"):
            conn.execute(f"UPDATE {table} SET expires_at = '2020-01-01T00:00:00Z'")
    from app.db import adaptation_repository, ai_usage_repository, analysis_repository, file_event_repository

    analysis_repository.delete_expired()
    adaptation_repository.delete_expired()
    retention_service.cleanup_files()
    ai_usage_repository.delete_expired(retention_days=0)
    file_event_repository.delete_expired(retention_days=0)

    assert len(_rows()) == before
    stats = next(u for u in user_metrics_service.build_users("24h")["users"] if u["user_id"] == "user_ana")
    assert stats["all_time"]["analyses"] == 1 and stats["all_time"]["decisions"]["editada"] == 1
    assert stats["all_time"]["uploads"] == 1 and stats["all_time"]["share_opens"] == 1


def test_lo_viejo_sigue_en_el_historico(client):
    old = datetime.now(timezone.utc) - timedelta(days=400)
    user_activity.record("analysis", "user_viejo", format="pdf", created_at=old)
    users = {u["user_id"]: u for u in user_metrics_service.build_users("30d")["users"]}
    assert users["user_viejo"]["all_time"]["analyses"] == 1 and users["user_viejo"]["range"]["analyses"] == 0
    assert users["user_viejo"]["first_seen_at"].startswith(str(old.year))


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------

def _seed(now: datetime) -> None:
    rec = user_activity.record
    for minutes in (10, 20, 30):
        rec("analysis", "user_ana", format="pdf", created_at=now - timedelta(minutes=minutes))
    rec("proposal", "user_ana", adaptation_type="lenguaje_claro", created_at=now - timedelta(minutes=9))
    rec("decision", "user_ana", detail="aceptada", adaptation_type="lenguaje_claro", created_at=now - timedelta(minutes=8))
    rec("upload", "user_ana", format="docx", size_bytes=10, created_at=now - timedelta(minutes=7))
    rec("ai_call", "user_ana", format="text", detail="ok", provider="groq", model="m", tokens=500, created_at=now - timedelta(minutes=6))
    rec("ai_call", "user_ana", format="text", detail="server_error", provider="groq", model="m", created_at=now - timedelta(minutes=6))
    rec("analysis", "test-web_github_jamrdev", format="text", mock=True, created_at=now - timedelta(minutes=5))
    rec("ai_call", "test-web_github_jamrdev", format="text", detail="ok", provider="groq", model="m", tokens=2000,
        created_at=now - timedelta(minutes=5))
    rec("rate_limited", "anon", detail="ip", created_at=now - timedelta(minutes=4))
    rec("share_opened", "user_beto", format="pdf", created_at=now - timedelta(minutes=3))  # lo abrió otra persona
    # Período anterior
    rec("analysis", "user_ana", format="pdf", created_at=now - timedelta(hours=30))


def test_estadisticas_del_periodo_y_rankings(client):
    now = datetime.now(timezone.utc)
    _seed(now)
    u = user_metrics_service.build_users("24h", now=now)
    k = u["kpis"]
    assert k["active_users"]["value"] == 3 and k["active_users"]["previous"] == 1  # beto no actuó
    assert k["actions"]["value"] == 7 and k["actions"]["previous"] == 1
    assert k["tokens"]["value"] == 2500 and k["rate_limited"]["value"] == 1
    assert k["total_users"] == 4 and len(k["active_users"]["spark"]) == 24
    assert u["by_source"]["frontend"]["actions"] == 6 and u["by_source"]["test_web"]["actions"] == 1

    users = {x["user_id"]: x for x in u["users"]}
    ana = users["user_ana"]["range"]
    assert (ana["analyses"], ana["proposals"], ana["uploads"], ana["actions"]) == (3, 1, 1, 6)
    assert ana["decisions"]["aceptada"] == 1 and ana["ai_calls"] == 2 and ana["ai_errors"] == 1 and ana["tokens"] == 500
    assert users["user_ana"]["all_time"]["analyses"] == 4
    web = users["test-web_github_jamrdev"]
    assert web["name"] == "jamrdev" and web["login"] == "github" and web["source"] == "test_web"
    assert web["range"]["mock_analyses"] == 1
    assert users["anon"]["name"] == "Usuario anónimo" and users["anon"]["range"]["rate_limited"] == 1

    assert [r["user_id"] for r in u["rankings"]["actions"]] == ["user_ana", "test-web_github_jamrdev"]
    assert [r["user_id"] for r in u["rankings"]["tokens"]] == ["test-web_github_jamrdev", "user_ana"]


def test_detalle_de_un_usuario(client):
    now = datetime.now(timezone.utc)
    _seed(now)
    d = user_metrics_service.build_user_detail("user_ana", "7d", now=now)
    assert len(d["buckets"]) == 7 and d["unit"] == "day"
    assert sum(d["activity"]["analyses"]) == 4 and sum(d["activity"]["files"]) == 1 and sum(d["tokens"]) == 500
    assert d["formats"] == {"analysis": {"pdf": 4}, "upload": {"docx": 1}, "export": {}}
    assert d["adaptations"] == {"lenguaje_claro": {"propuestas": 1, "aceptada": 1}}
    assert d["models"] == [{"provider": "groq", "model": "m", "calls": 2, "errors": 1, "tokens": 500}]
    assert d["recent"][0]["action"] == "ai_call" and len(d["recent"]) == 9
    assert d["user"]["all_time"]["analyses"] == 4
    assert user_metrics_service.build_user_detail("user_nadie", "7d", now=now) is None


def test_endpoints_del_panel(client):
    _seed(datetime.now(timezone.utc))
    assert client.get("/admin/api/users/user_ana").status_code == 401
    _admin(client)
    data = client.get("/admin/api/metrics?range=7d").json()
    assert data["users"]["kpis"]["actions"]["value"] == 8
    assert any(u["user_id"] == "user_ana" for u in data["users"]["users"])
    detail = client.get("/admin/api/users/user_ana?range=30d")
    assert detail.status_code == 200 and detail.json()["user"]["user_id"] == "user_ana"
    assert client.get("/admin/api/users/user_nadie").status_code == 404
    assert client.get("/admin/api/users/user_ana?range=1y").status_code == 400
    html = client.get("/admin/").text
    assert 'id="panel-uso-usuarios"' in html and 'id="users-body"' in html


def test_sin_actividad(client):
    u = user_metrics_service.build_users("7d")
    assert u["users"] == [] and u["kpis"]["active_users"]["value"] == 0
    assert u["rankings"] == {"actions": [], "tokens": []}
    assert len(u["activity"]["actions_by_source"]["frontend"]) == 7


# ---------------------------------------------------------------------------
# Perfiles de Clerk
# ---------------------------------------------------------------------------

def _clerk_user(user_id: str):
    email = SimpleNamespace(id="em_1", email_address=f"{user_id}@escuela.edu.ar")
    return SimpleNamespace(id=user_id, first_name="Ana", last_name="Gómez", username=None, image_url="https://img/ana.png",
                           email_addresses=[SimpleNamespace(id="em_0", email_address="otra@x"), email],
                           primary_email_address_id="em_1")


class _FakeUsers:
    def __init__(self, missing=()):
        self.calls: list[str] = []
        self.missing = set(missing)

    def get(self, user_id, timeout_ms=None):
        self.calls.append(user_id)
        if user_id in self.missing:
            raise type("ClerkErrors", (Exception,), {"status_code": 404})()
        return _clerk_user(user_id)


def _with_users(*user_ids):
    for uid in user_ids:
        reset = teacher_auth._caller.set(Caller(uid, authenticated=True))
        try:
            user_activity.record("analysis", uid)
        finally:
            teacher_auth._caller.reset(reset)


def test_perfiles_de_clerk_cacheados(client, monkeypatch):
    _with_users("user_ana", "user_borrado", "test-web_github_jamrdev")
    fake = _FakeUsers(missing={"user_borrado"})
    monkeypatch.setattr(user_profile_service, "_client", lambda: SimpleNamespace(users=fake))

    assert user_profile_service.refresh_stale_profiles() == 0  # sin USERS_CLERK_SECRET_KEY no consulta
    monkeypatch.setattr(settings, "USERS_CLERK_SECRET_KEY", SecretStr("sk_test_docentes"))
    assert user_profile_service.refresh_stale_profiles() == 1
    assert sorted(fake.calls) == ["user_ana", "user_borrado"]  # la web de pruebas no está en Clerk
    ana = user_activity.get_user("user_ana")
    assert (ana["name"], ana["email"], ana["avatar_url"]) == ("Ana Gómez", "user_ana@escuela.edu.ar", "https://img/ana.png")
    assert user_activity.get_user("user_borrado")["profile_fetched_at"] is not None

    # Con el caché vigente no se vuelve a consultar; vencido, sí
    assert user_profile_service.refresh_stale_profiles() == 0 and len(fake.calls) == 2
    later = datetime.now(timezone.utc) + user_profile_service.PROFILE_TTL + timedelta(hours=1)
    assert user_profile_service.refresh_stale_profiles(now=later) == 1

    name = next(u for u in user_metrics_service.build_users("24h")["users"] if u["user_id"] == "user_ana")["name"]
    assert name == "Ana Gómez"


def test_clerk_caido_no_rompe_nada(client, monkeypatch):
    _with_users("user_ana", "user_beto")
    calls = []

    class Down:
        def get(self, user_id, timeout_ms=None):
            calls.append(user_id)
            raise ConnectionError("sin red")

    monkeypatch.setattr(settings, "USERS_CLERK_SECRET_KEY", SecretStr("sk_test_docentes"))
    monkeypatch.setattr(user_profile_service, "_client", lambda: SimpleNamespace(users=Down()))
    assert user_profile_service.refresh_stale_profiles() == 0
    assert len(calls) == 1  # corta en el primer error: se reintenta en la próxima ronda
    assert user_activity.get_user("user_ana")["profile_fetched_at"] is None


# ---------------------------------------------------------------------------
# Privacidad
# ---------------------------------------------------------------------------

def test_politica_de_privacidad_documenta_el_historial(client):
    usage = client.get("/api/v1/privacy/policy").json()["usage_statistics"]
    assert usage["enabled"] is True and usage["deleted_with_materials"] is False
    assert "Indefinida" in usage["retention"]
    assert any("nombres de los archivos" in item for item in usage["never_recorded"])
