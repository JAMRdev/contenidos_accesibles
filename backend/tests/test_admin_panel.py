"""
Panel /admin: historial de uso de la IA, métricas agregadas y rutas protegidas.
"""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr

from app.admin import session
from app.admin.router import LEGACY_PREFIX, PREFIX
from app.admin.router import legacy_router
from app.admin.router import router as admin_router
from app.core.config import settings
from app.db import ai_usage_repository
from app.db import api_key_repository as repo
from app.db.database import get_connection, init_db
from app.services import admin_metrics_service
from app.services.llm.base import AdapterResult, ProviderServerError, Usage
from app.services.llm_service import LLMService

USER, PASSWORD = "admin", "clave-de-prueba"
NOW = datetime(2026, 10, 14, 15, 30, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "test.db"))
    init_db()


@pytest.fixture
def client(db, monkeypatch):
    monkeypatch.setattr(settings, "ADMIN_USERNAME", USER)
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", SecretStr(PASSWORD))
    session.reset()
    app = FastAPI()
    app.include_router(admin_router, prefix=PREFIX)
    app.include_router(legacy_router, prefix=LEGACY_PREFIX)
    return TestClient(app)


@pytest.fixture
def logged(client):
    r = client.post(PREFIX + "/login", data={"username": USER, "password": PASSWORD}, follow_redirects=False)
    assert r.status_code == 303
    return client


def _connection(label="Groq principal", provider="groq", **kwargs) -> int:
    fields = dict(
        label=label, raw_key="gsk_prueba_123456", provider=provider, base_url=None, model="modelo-x",
        priority=10, extra_params={}, rpm_threshold=30, tpm_threshold=None, rpd_threshold=1000, tpd_threshold=None,
    )
    fields.update(kwargs)
    return repo.create(**fields)


def _event(minutes_ago: int, *, outcome="ok", provider="groq", tokens=100, latency=1000, connection_id=None):
    ai_usage_repository.record(
        connection_id=connection_id, connection_label="c", provider=provider, model="modelo-x", kind="text",
        outcome=outcome, latency_ms=latency, prompt_tokens=tokens // 2, output_tokens=tokens // 2,
        total_tokens=tokens, created_at=NOW - timedelta(minutes=minutes_ago),
    )


# ---------------------------------------------------------------------------
# Repositorio
# ---------------------------------------------------------------------------

def test_registra_y_lista_eventos_por_rango(db):
    _event(10)
    _event(60 * 30)  # fuera de las últimas 24 h
    rows = ai_usage_repository.list_between(NOW - timedelta(hours=24), NOW)
    assert len(rows) == 1
    assert rows[0]["total_tokens"] == 100


def test_rechaza_outcome_o_kind_invalidos(db):
    with pytest.raises(ValueError):
        ai_usage_repository.record(connection_id=None, connection_label="c", provider="p", model="m",
                                   kind="text", outcome="raro", latency_ms=1)
    with pytest.raises(ValueError):
        ai_usage_repository.record(connection_id=None, connection_label="c", provider="p", model="m",
                                   kind="audio", outcome="ok", latency_ms=1)


def test_borra_eventos_viejos_segun_la_retencion(db):
    ai_usage_repository.record(connection_id=None, connection_label="c", provider="p", model="m", kind="text",
                               outcome="ok", latency_ms=1, created_at=datetime.now(timezone.utc) - timedelta(days=40))
    ai_usage_repository.record(connection_id=None, connection_label="c", provider="p", model="m", kind="text",
                               outcome="ok", latency_ms=1)
    assert ai_usage_repository.delete_expired(30) == 1


# ---------------------------------------------------------------------------
# Métricas
# ---------------------------------------------------------------------------

def test_metricas_24h_agrupan_por_hora_y_calculan_kpis(db):
    _event(5, tokens=300, latency=1000)
    _event(6, tokens=100, latency=3000)
    _event(7, outcome="rate_limited", tokens=0, latency=200)
    _event(60 * 24 + 30, tokens=50)  # período anterior

    m = admin_metrics_service.build_metrics("24h", 0, now=NOW)

    assert m["unit"] == "hour"
    assert len(m["buckets"]) == 24
    assert m["buckets"][-1] == "2026-10-14T15:00:00Z"
    assert m["usage"]["calls"][-1] == 3
    assert m["usage"]["outcomes"]["rate_limited"][-1] == 1
    assert m["kpis"]["calls"] == {"value": 3, "previous": 1, "spark": m["usage"]["calls"]}
    assert m["kpis"]["tokens"]["value"] == 400
    assert m["kpis"]["latency_p95"]["value"] == 3000  # solo cuenta las exitosas
    assert m["kpis"]["error_rate"]["value"] == pytest.approx(33.3)


def test_metricas_alinean_los_dias_a_la_zona_horaria_del_navegador(db):
    # 01:00 UTC del 14 es todavía el 13 en Argentina (UTC-3, getTimezoneOffset = 180)
    ai_usage_repository.record(connection_id=None, connection_label="c", provider="groq", model="m", kind="text",
                               outcome="ok", latency_ms=1, total_tokens=10,
                               created_at=datetime(2026, 10, 14, 1, 0, tzinfo=timezone.utc))
    m = admin_metrics_service.build_metrics("7d", 180, now=NOW)
    assert m["unit"] == "day"
    assert m["buckets"][-1] == "2026-10-14T03:00:00Z"
    assert m["usage"]["calls"][-2:] == [1, 0]


def test_mas_de_tres_proveedores_se_agrupan_en_otros(db):
    for i, provider in enumerate(["a", "b", "c", "d", "e"]):
        _event(5, provider=provider, tokens=100 * (5 - i))
    series = admin_metrics_service.build_metrics("24h", 0, now=NOW)["usage"]["tokens_by_provider"]
    assert [s["key"] for s in series] == ["a", "b", "c", "otros"]
    assert series[-1]["values"][-1] == 200 + 100


def test_metricas_de_conexiones_y_alertas(db):
    key_id = _connection()
    with get_connection() as conn:
        conn.execute("UPDATE api_keys SET request_count = 900, prompt_token_count = 70, candidates_token_count = 30 WHERE id = ?", (key_id,))
    _event(5, connection_id=key_id, latency=800)
    _event(6, connection_id=key_id, outcome="server_error")

    m = admin_metrics_service.build_metrics("24h", 0, now=NOW)
    c = m["connections"][0]
    assert c["status"] == "near-limit"
    assert (c["prompt_tokens_today"], c["output_tokens_today"]) == (70, 30)
    assert (c["calls_in_range"], c["errors_in_range"], c["latency_p50"]) == (2, 1, 800)
    assert "encrypted_key" not in c and "gsk_prueba_123456" not in json.dumps(m)
    assert m["alerts"] == [{"label": "Groq principal", "status": "near-limit", "usage_pct": 90.0}]


def test_metricas_de_producto_sin_contenido_del_material(db):
    now = datetime.now(timezone.utc)
    expires = (now + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    diagnostic = {
        "estimated_score": 72,
        "barriers": [{"category": "comprension", "severity": "barrera_detectada", "original_text": "SECRETO"}],
        "metadata": {"provider": "groq", "model": "m", "latency_ms": 1200, "total_tokens": 900},
    }
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO analyses VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("a1", "pdf", "tarea.pdf", "texto SECRETO", json.dumps(diagnostic), now.strftime("%Y-%m-%dT%H:%M:%SZ"), expires),
        )
        for i, status in enumerate(["aceptada", "descartada", "pendiente"]):
            conn.execute(
                "INSERT INTO adaptation_proposals VALUES (?, ?, ?, ?, ?, ?, ?)",
                (f"p{i}", "a1", "b1", status, json.dumps({"adaptation_type": "lenguaje_claro", "proposed_text": "SECRETO"}), "x", expires),
            )

    p = admin_metrics_service.build_metrics("24h", 0)["product"]
    assert p["analyses"] == 1 and p["avg_score"] == 72
    assert p["score_histogram"][7] == 1
    assert p["by_source"]["pdf"] == 1
    assert p["barriers"]["comprension"]["barrera_detectada"] == 1
    assert p["decisions"]["lenguaje_claro"] == {"aceptada": 1, "editada": 0, "descartada": 1, "pendiente": 1}
    assert p["acceptance_rate"] == 50
    assert "SECRETO" not in json.dumps(p) and "tarea.pdf" not in json.dumps(p)


def test_rango_invalido(db):
    with pytest.raises(ValueError):
        admin_metrics_service.build_metrics("1y")


# ---------------------------------------------------------------------------
# Registro desde LLMService
# ---------------------------------------------------------------------------

class _FlakyAdapter:
    """Falla una vez con 5xx y después responde."""

    def __init__(self):
        self.calls = 0

    async def generate(self, conn, **kwargs):
        self.calls += 1
        if self.calls == 1:
            raise ProviderServerError("503")
        return AdapterResult(text="CONECTADO", usage=Usage(prompt_tokens=7, output_tokens=3, total_tokens=10))


def test_llm_service_registra_cada_intento(db, monkeypatch):
    _connection(provider="gemini")
    service = LLMService()
    adapter = _FlakyAdapter()
    monkeypatch.setattr(service, "_adapter", lambda conn: adapter)
    monkeypatch.setattr("app.services.llm_service.asyncio.sleep", _no_sleep)

    asyncio.run(service.ping_connection())

    rows = ai_usage_repository.list_between(datetime.now(timezone.utc) - timedelta(minutes=5),
                                            datetime.now(timezone.utc) + timedelta(minutes=1))
    assert [r["outcome"] for r in rows] == ["server_error", "ok"]
    assert rows[1]["total_tokens"] == 10 and rows[1]["kind"] == "text"


async def _no_sleep(_seconds):
    return None


def test_si_falla_el_registro_la_llamada_sigue(db, monkeypatch):
    _connection(provider="gemini")
    service = LLMService()
    adapter = _FlakyAdapter()
    adapter.calls = 1
    monkeypatch.setattr(service, "_adapter", lambda conn: adapter)

    def broken(**kwargs):
        raise RuntimeError("disco lleno")

    monkeypatch.setattr(ai_usage_repository, "record", broken)
    result = asyncio.run(service.ping_connection())
    assert result["response"] == "CONECTADO"


# ---------------------------------------------------------------------------
# Sesión
# ---------------------------------------------------------------------------

def test_sin_sesion_la_pagina_redirige_al_login_y_la_api_responde_401(client):
    r = client.get(PREFIX + "/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"{PREFIX}/login?next=/admin/"
    assert client.get(PREFIX + "/api/metrics").status_code == 401
    assert client.post(PREFIX + "/keys/1/delete").status_code == 401
    assert client.get(PREFIX + "/diagramas/arquitectura/contenido").status_code == 401


def test_basic_auth_ya_no_sirve(client):
    assert client.get(PREFIX + "/api/metrics", auth=(USER, PASSWORD)).status_code == 401


def test_login_correcto_crea_cookie_segura_y_redirige_a_next(client):
    r = client.post(PREFIX + "/login", data={"username": USER, "password": PASSWORD, "next": f"{PREFIX}/diagramas/arquitectura"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"{PREFIX}/diagramas/arquitectura"
    cookie = r.headers["set-cookie"].lower()
    assert session.COOKIE_NAME in cookie and "httponly" in cookie and "samesite=strict" in cookie and "path=/;" in cookie
    assert client.get(PREFIX + "/api/metrics").status_code == 200


@pytest.mark.parametrize("target", ["https://malicioso.example", "//malicioso.example", "/api/v1/health", "/admin/../api", "/admin\\..\\x"])
def test_next_fuera_del_panel_vuelve_al_inicio(client, target):
    r = client.post(PREFIX + "/login", data={"username": USER, "password": PASSWORD, "next": target}, follow_redirects=False)
    assert r.headers["location"] == PREFIX


def test_login_incorrecto_y_bloqueo_por_intentos(client):
    for _ in range(session.MAX_FAILED_LOGINS):
        r = client.post(PREFIX + "/login", data={"username": USER, "password": "mala"})
        assert r.status_code == 401 and "Usuario o contraseña incorrectos" in r.text
    r = client.post(PREFIX + "/login", data={"username": USER, "password": PASSWORD})
    assert r.status_code == 429 and "Demasiados intentos" in r.text


def test_logout_borra_la_sesion(logged):
    r = logged.post(PREFIX + "/logout", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"{PREFIX}/login?bye=1"
    assert logged.get(PREFIX + "/api/metrics").status_code == 401


def test_token_alterado_vencido_o_de_otra_clave_no_vale(client, monkeypatch):
    token = session.create_token(session.local_subject())
    assert session.read_token(token) == f"local:{USER}"
    assert session.read_token(token[:-1] + ("0" if token[-1] != "0" else "1")) is None
    assert session.read_token(session.create_token(session.local_subject(), now=0)) is None
    monkeypatch.setattr(settings, "ADMIN_PASSWORD", SecretStr("otra-clave"))
    assert session.read_token(token) is None


def test_post_de_login_desde_otro_origen_se_rechaza(client):
    r = client.post(PREFIX + "/login", data={"username": USER, "password": PASSWORD},
                    headers={"Origin": "https://malicioso.example"})
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# Rutas
# ---------------------------------------------------------------------------

def test_panel_carga_obelisco_redoc_y_no_expone_keys(logged):
    _connection()
    r = logged.get(PREFIX + "/")
    assert r.status_code == 200
    assert "@gcba/obelisco-v2@1.19.0" in r.text
    assert 'href="/redoc"' in r.text and "Cerrar sesión" in r.text
    assert "gsk_prueba_123456" not in r.text


def test_pagina_de_diagrama_y_su_contenido(logged):
    r = logged.get(PREFIX + "/diagramas/arquitectura")
    assert r.status_code == 200 and f"{PREFIX}/diagramas/arquitectura/contenido" in r.text
    assert logged.get(PREFIX + "/diagramas/arquitectura/contenido").status_code == 200
    assert logged.get(PREFIX + "/diagramas/inexistente").status_code == 404


def test_endpoint_de_metricas(logged):
    r = logged.get(PREFIX + "/api/metrics", params={"range": "7d", "tz": 180})
    assert r.status_code == 200
    assert r.json()["range"] == "7d"
    assert logged.get(PREFIX + "/api/metrics", params={"range": "1y"}).status_code == 400


def test_estaticos_publicos_solo_de_la_lista(client):
    assert client.get(PREFIX + "/static/admin.css").status_code == 200
    assert client.get(PREFIX + "/static/..%2Frouter.py").status_code == 404
    assert client.get(PREFIX + "/static/otro.js").status_code == 404


def test_crear_editar_habilitar_y_borrar_conexion(logged):
    form = {"label": "Nueva", "key_value": "sk-1234567890", "provider": "Groq ", "model": "m", "priority": "5"}
    r = logged.post(PREFIX + "/keys", data=form, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"{PREFIX}#conexiones"
    key = repo.list_all_ordered_by_priority()[0]
    assert key["provider"] == "groq"

    r = logged.post(f"{PREFIX}/keys/{key['id']}/enabled", data={"enabled": "false"})
    assert r.json() == {"id": key["id"], "enabled": False}
    assert repo.list_all_ordered_by_priority()[0]["enabled"] == 0

    form.update({"label": "Renombrada", "enabled": "true", "key_value": ""})
    logged.post(f"{PREFIX}/keys/{key['id']}/update", data=form, follow_redirects=False)
    assert repo.list_all_ordered_by_priority()[0]["label"] == "Renombrada"

    logged.post(f"{PREFIX}/keys/{key['id']}/delete", follow_redirects=False)
    assert repo.list_all_ordered_by_priority() == []


def test_post_desde_otro_origen_se_rechaza(logged):
    r = logged.post(PREFIX + "/keys/1/enabled", data={"enabled": "true"}, headers={"Origin": "https://malicioso.example"})
    assert r.status_code == 403


@pytest.mark.parametrize("old, new", [
    ("/adminv2", "/admin/"),
    ("/adminv2/", "/admin/"),
    ("/adminv2/login?next=/adminv2/", "/admin/login?next=/adminv2/"),
    ("/adminv2/diagramas/arquitectura", "/admin/diagramas/arquitectura"),
])
def test_adminv2_redirige_a_admin(client, old, new):
    r = client.get(old, follow_redirects=False)
    assert r.status_code == 308 and r.headers["location"] == new
