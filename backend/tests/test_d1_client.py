"""
Cliente del Worker pasarela de D1 (Issue #58).

Los tests unitarios usan un transporte HTTP simulado (sin red). El de integración corre
contra el Worker local solo si D1_GATEWAY_TEST_URL está definida:

    cd backend/d1-gateway && npm run dev          # en otra terminal
    D1_GATEWAY_TEST_URL=http://127.0.0.1:8787 D1_GATEWAY_TEST_TOKEN=token-local-de-prueba pytest tests/test_d1_client.py
"""
import json
import os
import uuid

import httpx
import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.db.d1_client import D1AuthError, D1Client, D1Error, D1QueryError

URL, TOKEN = "https://gateway.example.workers.dev", "secreto-de-prueba"


def client(handler, **kwargs) -> D1Client:
    return D1Client(URL, TOKEN, transport=httpx.MockTransport(handler), backoff_s=0, **kwargs)


def ok(results=None, meta=None):
    return httpx.Response(200, json={"results": results or [], "meta": meta or {}})


# ---------------------------------------------------------------------------
# Unitarios
# ---------------------------------------------------------------------------

def test_query_envia_sql_parametros_y_token():
    seen = {}

    def handler(request: httpx.Request):
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return ok([{"n": 3}], {"changes": 0})

    r = client(handler).query("SELECT COUNT(*) AS n FROM t WHERE a = ? AND b = ?", ["x", 1])
    assert r.rows == [{"n": 3}]
    assert seen["url"] == f"{URL}/query"
    assert seen["auth"] == f"Bearer {TOKEN}"
    assert seen["body"] == {"sql": "SELECT COUNT(*) AS n FROM t WHERE a = ? AND b = ?", "params": ["x", 1]}


def test_batch_devuelve_un_resultado_por_sentencia():
    def handler(request):
        body = json.loads(request.content)
        assert [s["sql"] for s in body["statements"]] == ["INSERT INTO t VALUES (?)", "SELECT 1"]
        return httpx.Response(200, json={"results": [{"results": [], "meta": {"changes": 1, "last_row_id": 7}},
                                                     {"results": [{"1": 1}], "meta": {}}]})

    first, second = client(handler).batch([("INSERT INTO t VALUES (?)", [5]), ("SELECT 1", [])])
    assert (first.changes, first.last_row_id) == (1, 7)
    assert second.rows == [{"1": 1}]


def test_batch_vacio_no_llama_al_worker():
    def handler(request):
        raise AssertionError("no debería llamar")

    assert client(handler).batch([]) == []


@pytest.mark.parametrize("status, error", [(400, D1QueryError), (422, D1QueryError), (401, D1AuthError), (500, D1Error)])
def test_errores_no_transitorios_no_se_reintentan(status, error):
    calls = []

    def handler(request):
        calls.append(1)
        return httpx.Response(status, json={"error": "detalle del Worker"})

    with pytest.raises(error):
        client(handler, retries=3).query("SELECT 1")
    assert len(calls) == 1


def test_error_de_sql_muestra_el_mensaje_de_d1():
    def handler(request):
        return httpx.Response(422, json={"error": "D1_ERROR: no such table: x"})

    with pytest.raises(D1QueryError, match="no such table"):
        client(handler).query("SELECT * FROM x")


def test_reintenta_errores_transitorios_y_se_recupera():
    responses = iter([httpx.Response(503), httpx.Response(502), ok([{"ok": 1}])])

    r = client(lambda request: next(responses), retries=2).query("SELECT 1 AS ok")
    assert r.rows == [{"ok": 1}]


def test_sin_conexion_falla_despues_de_los_reintentos():
    calls = []

    def handler(request):
        calls.append(1)
        raise httpx.ConnectError("sin red", request=request)

    with pytest.raises(D1Error, match="No se pudo conectar"):
        client(handler, retries=2).query("SELECT 1")
    assert len(calls) == 3


def test_respuesta_200_sin_json_no_rompe():
    r = client(lambda request: httpx.Response(200, text="")).query("SELECT 1")
    assert r.rows == []


# ---------------------------------------------------------------------------
# Configuración
# ---------------------------------------------------------------------------

def _settings(**overrides) -> Settings:
    base = {"ADMIN_PASSWORD": "admin", "DB_ENCRYPTION_KEY": "k"}
    return Settings(_env_file=None, **(base | overrides))


def test_por_defecto_sigue_sqlite_local():
    assert _settings().DATABASE_BACKEND == "sqlite"


@pytest.mark.parametrize("missing", ["D1_GATEWAY_URL", "D1_GATEWAY_TOKEN"])
def test_d1_sin_url_o_token_no_arranca(missing):
    values = {"D1_GATEWAY_URL": URL, "D1_GATEWAY_TOKEN": TOKEN}
    values.pop(missing)
    with pytest.raises(ValidationError, match=missing):
        _settings(DATABASE_BACKEND="d1", **values)


def test_d1_con_url_y_token_arranca():
    s = _settings(DATABASE_BACKEND="d1", D1_GATEWAY_URL=URL, D1_GATEWAY_TOKEN=TOKEN)
    assert s.D1_GATEWAY_TOKEN.get_secret_value() == TOKEN


# ---------------------------------------------------------------------------
# Integración con el Worker local (opcional)
# ---------------------------------------------------------------------------

LOCAL_URL = os.environ.get("D1_GATEWAY_TEST_URL")


@pytest.mark.skipif(not LOCAL_URL, reason="D1_GATEWAY_TEST_URL no definida (Worker local apagado)")
def test_integracion_con_el_worker_local():
    token = os.environ.get("D1_GATEWAY_TEST_TOKEN", "token-local-de-prueba")
    marker = f"test-{uuid.uuid4()}"
    with D1Client(LOCAL_URL, token) as db:
        assert db.query("SELECT ?1 + ?2 AS suma", [2, 3]).rows == [{"suma": 5}]

        inserted = db.query("INSERT INTO gateway_healthcheck (source) VALUES (?)", [marker])
        assert inserted.changes == 1

        # batch atómico: si una sentencia falla, la otra no se aplica
        with pytest.raises(D1QueryError):
            db.batch([("INSERT INTO gateway_healthcheck (source) VALUES (?)", [marker]),
                      ("INSERT INTO tabla_inexistente VALUES (1)", [])])
        count = db.query("SELECT COUNT(*) AS n FROM gateway_healthcheck WHERE source = ?", [marker])
        assert count.rows == [{"n": 1}]

        with pytest.raises(D1QueryError, match="una sola sentencia"):
            db.query("SELECT 1; DROP TABLE gateway_healthcheck")

    with D1Client(LOCAL_URL, "token-incorrecto") as wrong, pytest.raises(D1AuthError):
        wrong.query("SELECT 1")
