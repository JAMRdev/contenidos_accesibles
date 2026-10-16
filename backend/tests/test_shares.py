"""
Enlaces revocables para compartir archivos (Issue #65): /files/{id}/shares y /s/{token}.
Corre contra el almacenamiento local (sin red), que firma las URLs igual que R2.
"""
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app.core import rate_limit
from app.core.config import settings
from app.core.owner import current_owner
from app.db.database import get_connection, init_db
from app.main import app
from app.services import storage_service
from app.services.storage_service import LocalStorage

PDF = b"%PDF-1.7\n% material para compartir\n" + b"0" * 500


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    init_db()
    rate_limit.reset()
    storage_service.reset_storage(LocalStorage(str(tmp_path / "storage"), b"secreto-de-prueba"))
    yield TestClient(app, follow_redirects=False)
    app.dependency_overrides.clear()
    storage_service.reset_storage()
    rate_limit.reset()


def _path(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}" if parts.query else parts.path


def _uploaded(client, complete: bool = True) -> str:
    data = client.post("/api/v1/files/upload-url",
                       json={"filename": "Guía ñandú.pdf", "content_type": "application/pdf", "size_bytes": len(PDF)}).json()
    assert client.put(_path(data["upload"]["url"]), content=PDF, headers=data["upload"]["headers"]).status_code == 200
    if complete:
        assert client.post(f"/api/v1/files/{data['file']['file_id']}/complete").status_code == 200
    return data["file"]["file_id"]


def _share(client, file_id: str, **body) -> dict:
    r = client.post(f"/api/v1/files/{file_id}/shares", json=body or None)
    assert r.status_code == 201, r.text
    return r.json()


def test_crear_abrir_y_descargar_directo_del_almacenamiento(client):
    file_id = _uploaded(client)
    share = _share(client, file_id)
    assert share["url"].startswith("http://testserver/s/") and share["access_count"] == 0

    opened = client.get(_path(share["url"]))
    assert opened.status_code == 302
    assert opened.headers["cache-control"] == "no-store" and opened.headers["referrer-policy"] == "no-referrer"
    target = opened.headers["location"]
    assert "/api/v1/files/local/users/anon/uploads/" in target  # URL firmada, no la API
    got = client.get(_path(target))
    assert got.status_code == 200 and got.content == PDF
    assert "filename*=UTF-8''Gu%C3%ADa%20%C3%B1and%C3%BA.pdf" in got.headers["content-disposition"]

    listed = client.get(f"/api/v1/files/{file_id}/shares").json()
    assert len(listed) == 1 and listed[0]["access_count"] == 1 and listed[0]["last_access_at"]
    assert "url" not in listed[0]


def test_el_token_no_se_guarda_en_la_base(client):
    share = _share(client, _uploaded(client))
    token = share["url"].rsplit("/", 1)[1]
    with get_connection() as conn:
        rows = [dict(r) for r in conn.execute("SELECT * FROM file_shares")]
    assert len(rows) == 1 and token not in str(rows) and rows[0]["id"] == share["share_id"]
    assert client.get(f"/s/{share['share_id']}").status_code == 404  # el id no sirve para abrirlo


def test_revocar_corta_el_enlace_al_instante(client):
    file_id = _uploaded(client)
    share = _share(client, file_id)
    assert client.get(_path(share["url"])).status_code == 302
    assert client.delete(f"/api/v1/files/{file_id}/shares/{share['share_id']}").status_code == 204
    assert client.get(_path(share["url"])).status_code == 404
    assert client.delete(f"/api/v1/files/{file_id}/shares/{share['share_id']}").status_code == 404
    assert client.get(f"/api/v1/files/{file_id}/shares").json() == []


def test_varios_enlaces_se_revocan_por_separado(client):
    file_id = _uploaded(client)
    first, second = _share(client, file_id), _share(client, file_id)
    client.delete(f"/api/v1/files/{file_id}/shares/{first['share_id']}")
    assert client.get(_path(first["url"])).status_code == 404
    assert client.get(_path(second["url"])).status_code == 302


def test_vencimiento_del_enlace_y_del_archivo(client):
    file_id = _uploaded(client)
    link, other = _share(client, file_id), _share(client, file_id)
    with get_connection() as conn:
        conn.execute("UPDATE file_shares SET expires_at = '2020-01-01T00:00:00Z' WHERE id = ?", (link["share_id"],))
    assert client.get(_path(link["url"])).status_code == 404
    assert client.get(_path(other["url"])).status_code == 302
    with get_connection() as conn:
        conn.execute("UPDATE files SET expires_at = '2020-01-01T00:00:00Z' WHERE id = ?", (file_id,))
    assert client.get(_path(other["url"])).status_code == 404


def test_vigencia_pedida_nunca_supera_la_del_archivo(client):
    file_id = _uploaded(client)
    file_expires = client.get(f"/api/v1/files/{file_id}").json()["expires_at"]
    assert _share(client, file_id)["expires_at"] == file_expires
    assert _share(client, file_id, expires_in_hours=10_000)["expires_at"] == file_expires
    assert _share(client, file_id, expires_in_hours=1)["expires_at"] < file_expires
    assert client.post(f"/api/v1/files/{file_id}/shares", json={"expires_in_hours": 0}).status_code == 422


def test_solo_el_dueno_y_solo_archivos_subidos(client):
    pending = _uploaded(client, complete=False)
    assert client.post(f"/api/v1/files/{pending}/shares").status_code == 409
    file_id = _uploaded(client)
    share = _share(client, file_id)
    app.dependency_overrides[current_owner] = lambda: "otro-usuario"
    assert client.post(f"/api/v1/files/{file_id}/shares").status_code == 404
    assert client.get(f"/api/v1/files/{file_id}/shares").status_code == 404
    assert client.delete(f"/api/v1/files/{file_id}/shares/{share['share_id']}").status_code == 404
    assert client.get(_path(share["url"])).status_code == 302  # el enlace en sí es público


@pytest.mark.parametrize("token", ["corto", "x" * 43, "x" * 200])
def test_tokens_invalidos_son_404(client, token):
    assert client.get(f"/s/{token}").status_code == 404


def test_rate_limit_por_ip(client, monkeypatch):
    monkeypatch.setattr(settings, "SHARE_RATE_LIMIT_PER_IP_MINUTE", 3)
    share = _share(client, _uploaded(client))
    codes = [client.get(_path(share["url"])).status_code for _ in range(4)]
    assert codes == [302, 302, 302, 429]
    assert client.get(_path(share["url"])).headers.get("retry-after")
