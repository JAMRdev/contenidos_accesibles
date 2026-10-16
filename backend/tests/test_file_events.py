"""
Eventos de archivos y enlaces, y su bloque en las métricas del panel (Issue #98).
Corre contra el almacenamiento local (sin red).
"""
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient

from app.core import rate_limit
from app.core.config import settings
from app.db import file_event_repository as file_events
from app.db.database import get_connection, init_db
from app.main import app
from app.services import admin_metrics_service, storage_service
from app.services.storage_service import LocalStorage

PDF = b"%PDF-1.7\n% material\n" + b"0" * 900
SECRET_NAME = "Lista de Juan Pérez 3B.pdf"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    init_db()
    rate_limit.reset()
    storage_service.reset_storage(LocalStorage(str(tmp_path / "storage"), b"secreto-de-prueba"))
    yield TestClient(app, follow_redirects=False)
    storage_service.reset_storage()
    rate_limit.reset()


def _path(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}" if parts.query else parts.path


def _upload(client, body: bytes = PDF, declared: int | None = None, name: str = SECRET_NAME) -> tuple[str, int]:
    data = client.post("/api/v1/files/upload-url",
                       json={"filename": name, "content_type": "application/pdf", "size_bytes": declared or len(body)}).json()
    client.put(_path(data["upload"]["url"]), content=body, headers=data["upload"]["headers"])
    return data["file"]["file_id"], client.post(f"/api/v1/files/{data['file']['file_id']}/complete").status_code


def _events() -> list[dict]:
    with get_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT event, format, size_bytes, detail FROM file_events ORDER BY id")]


def test_cada_accion_registra_su_evento_sin_nombres(client):
    file_id, code = _upload(client)
    assert code == 200
    assert _upload(client, declared=len(PDF) + 5)[1] == 422  # tamaño distinto: rechazada
    client.get(f"/api/v1/files/{file_id}/download-url")
    share = client.post(f"/api/v1/files/{file_id}/shares").json()
    client.get(_path(share["url"]))
    client.delete(f"/api/v1/files/{file_id}/shares/{share['share_id']}")
    analysis_id = client.post("/api/v1/analysis/sample?mock=true", json={"text": "Texto de prueba para exportar. " * 10}).json()["analysis_id"]
    client.get("/api/v1/export/content", params={"analysis_id": analysis_id, "format": "md"})
    client.get("/api/v1/export/report", params={"analysis_id": analysis_id, "format": "txt", "delivery": "link"})

    events = _events()
    assert [e["event"] for e in events] == [
        "upload", "upload_rejected", "download", "share_created", "share_opened", "share_revoked", "export", "export",
    ]
    assert events[0] == {"event": "upload", "format": "pdf", "size_bytes": len(PDF), "detail": None}
    assert [e["detail"] for e in events[2:6]] == ["upload"] * 4
    assert [e["detail"] for e in events[6:]] == ["content:file", "report:link"]
    assert [e["format"] for e in events[6:]] == ["md", "txt"]
    assert "Juan" not in str(events)


def test_registrar_un_evento_nunca_rompe_la_operacion(client, monkeypatch):
    def broken():
        raise RuntimeError("base caída")

    monkeypatch.setattr("app.db.file_event_repository.get_connection", broken)
    assert _upload(client)[1] == 200
    with pytest.raises(ValueError):
        file_events.record("otro")


def test_retencion(client):
    old = datetime.now(timezone.utc) - timedelta(days=settings.AI_USAGE_RETENTION_DAYS + 1)
    file_events.record("upload", format="pdf", size_bytes=10, created_at=old)
    file_events.record("upload", format="pdf", size_bytes=10)
    assert file_events.delete_expired() == 1
    assert len(_events()) == 1


def test_bloque_de_metricas_del_periodo(client):
    a, _ = _upload(client)
    b, _ = _upload(client, name="clase.pdf")
    _upload(client, declared=len(PDF) + 5)
    keep = client.post(f"/api/v1/files/{a}/shares").json()
    revoked = client.post(f"/api/v1/files/{b}/shares").json()
    client.get(_path(keep["url"]))
    client.get(_path(keep["url"]))
    client.delete(f"/api/v1/files/{b}/shares/{revoked['share_id']}")
    # Un evento del período anterior, para la comparación
    file_events.record("upload", format="docx", size_bytes=100, created_at=datetime.now(timezone.utc) - timedelta(hours=30))

    f = admin_metrics_service.build_metrics("24h")["files"]
    assert f["kpis"]["uploads"]["value"] == 2 and f["kpis"]["uploads"]["previous"] == 1
    assert f["kpis"]["share_opens"]["value"] == 2 and f["kpis"]["shares_created"]["value"] == 2
    assert f["kpis"]["uploaded_bytes"]["value"] == 2 * len(PDF)
    assert sum(f["activity"]["upload"]) == 2 and sum(f["activity"]["share_opened"]) == 2
    assert f["rejected_uploads"] == 1 and f["revoked_shares"] == 1
    assert f["by_format"]["pdf"] == {"upload": 2, "export": 0}
    assert f["shares"]["by_kind"]["upload"] == {"activo": 1, "revocado": 1, "vencido": 0}
    assert (f["shares"]["created"], f["shares"]["opened"], f["shares"]["opens"]) == (2, 1, 2)
    recent = f["shares"]["recent"]
    assert {r["state"] for r in recent} == {"activo", "revocado"} and all(r["format"] == "pdf" for r in recent)
    assert "Juan" not in str(f) and "clase" not in str(f)


def test_enlace_vencido_en_las_metricas(client):
    file_id, _ = _upload(client)
    share = client.post(f"/api/v1/files/{file_id}/shares").json()
    with get_connection() as conn:
        conn.execute("UPDATE file_shares SET expires_at = '2020-01-01T00:00:00Z' WHERE id = ?", (share["share_id"],))
    f = admin_metrics_service.build_metrics("24h")["files"]
    assert f["shares"]["by_kind"]["upload"]["vencido"] == 1


def test_sin_actividad(client):
    f = admin_metrics_service.build_metrics("7d")["files"]
    assert f["kpis"]["uploads"]["value"] == 0 and f["shares"]["recent"] == []
    assert len(f["activity"]["upload"]) == 7
