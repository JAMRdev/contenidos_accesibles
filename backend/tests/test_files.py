"""
Archivos con subida directa al almacenamiento (Issue #62).

El flujo completo corre contra el almacenamiento local (el que se usa en desarrollo), que
firma las URLs igual que R2. R2Storage se prueba aparte con el cliente S3 de boto3 sin red
(firmas locales y respuestas simuladas con Stubber).
"""
import time
from urllib.parse import parse_qs, urlsplit

import pytest
from botocore.stub import Stubber
from fastapi.testclient import TestClient

from app.core.config import Settings, settings
from app.core.owner import current_owner
from app.db import analysis_repository  # noqa: F401 - registra el esquema completo
from app.db.database import get_connection, init_db
from app.main import app
from app.services import storage_service
from app.services.storage_service import LocalStorage, R2Storage, StorageError, content_disposition

PDF = b"%PDF-1.7\n% prueba\n" + b"0" * 2000
DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "DATABASE_PATH", str(tmp_path / "test.db"))
    init_db()
    monkeypatch.setattr(settings, "STORAGE_BACKEND", "local")
    local = LocalStorage(str(tmp_path / "storage"), b"secreto-de-prueba")
    storage_service.reset_storage(local)
    yield local
    storage_service.reset_storage()


@pytest.fixture
def client(storage):
    yield TestClient(app)
    app.dependency_overrides.clear()


def _ask(client, filename="material.pdf", content_type="application/pdf", size=len(PDF)):
    return client.post("/api/v1/files/upload-url", json={"filename": filename, "content_type": content_type, "size_bytes": size})


def _path(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.path}?{parts.query}"


def _upload(client, body=PDF, **kwargs) -> dict:
    r = _ask(client, size=kwargs.pop("size", len(body)), **kwargs)
    assert r.status_code == 201, r.text
    data = r.json()
    put = client.put(_path(data["upload"]["url"]), content=body, headers=data["upload"]["headers"])
    assert put.status_code == 200, put.text
    return data


# ---------------------------------------------------------------------------
# Flujo completo
# ---------------------------------------------------------------------------

def test_subir_confirmar_y_descargar(client, storage):
    data = _upload(client)
    file_id = data["file"]["file_id"]
    assert data["file"]["status"] == "pending" and data["upload"]["method"] == "PUT"
    assert data["upload"]["url"].startswith("http://testserver/api/v1/files/local/users/anon/uploads/")
    assert data["upload"]["headers"] == {"Content-Type": "application/pdf"}

    done = client.post(f"/api/v1/files/{file_id}/complete")
    assert done.status_code == 200 and done.json()["status"] == "uploaded" and done.json()["uploaded_at"]
    assert client.post(f"/api/v1/files/{file_id}/complete").json()["status"] == "uploaded"  # idempotente

    info = client.get(f"/api/v1/files/{file_id}").json()
    assert info["filename"] == "material.pdf" and info["size_bytes"] == len(PDF) and info["kind"] == "upload"

    link = client.get(f"/api/v1/files/{file_id}/download-url").json()["download"]
    got = client.get(_path(link["url"]))
    assert got.status_code == 200 and got.content == PDF
    assert got.headers["content-type"] == "application/pdf"
    assert 'filename="material.pdf"' in got.headers["content-disposition"]
    assert got.headers["cache-control"] == "no-store"


def test_la_clave_tiene_prefijo_por_usuario_y_no_es_adivinable(client, storage):
    first, second = _upload(client), _upload(client)
    keys = [r["object_key"] for r in _rows()]
    assert all(k.startswith("users/anon/uploads/") and k.endswith(".pdf") for k in keys)
    assert len(set(keys)) == 2 and all(len(k.rsplit("/", 1)[1]) >= 32 for k in keys)
    assert first["file"]["file_id"] != second["file"]["file_id"]


def _rows() -> list[dict]:
    with get_connection() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM files ORDER BY created_at")]


# ---------------------------------------------------------------------------
# Validación del pedido
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("filename,content_type,size,code", [
    ("virus.exe", "application/pdf", 100, 400),
    ("sin_extension", "application/pdf", 100, 400),
    ("material.pdf", "text/html", 100, 400),
    ("material.pdf", "application/pdf", 10 * 1024 * 1024 + 1, 413),
    ("material.pdf", "application/pdf", 0, 422),
])
def test_rechaza_formato_tipo_o_tamano_al_pedir_la_url(client, filename, content_type, size, code):
    assert _ask(client, filename, content_type, size).status_code == code
    assert _rows() == []


def test_acepta_word_y_powerpoint(client):
    assert _ask(client, "clase.docx", DOCX_TYPE, 500).status_code == 201
    assert _ask(client, "clase.pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", 500).status_code == 201


# ---------------------------------------------------------------------------
# Confirmación
# ---------------------------------------------------------------------------

def test_confirmar_antes_de_subir_es_409(client):
    file_id = _ask(client).json()["file"]["file_id"]
    assert client.post(f"/api/v1/files/{file_id}/complete").status_code == 409


def test_tamano_distinto_se_descarta(client):
    data = _upload(client, size=len(PDF) + 10)
    file_id = data["file"]["file_id"]
    r = client.post(f"/api/v1/files/{file_id}/complete")
    assert r.status_code == 422 and "no coincide" in r.json()["detail"]
    assert client.get(f"/api/v1/files/{file_id}").status_code == 404
    assert _rows() == []


def test_firma_binaria_que_no_es_pdf_se_descarta(client, storage):
    data = _upload(client, body=b"<html>no soy un pdf</html>")
    r = client.post(f"/api/v1/files/{data['file']['file_id']}/complete")
    assert r.status_code == 422 and "%PDF-" in r.json()["detail"]
    assert storage.head(_path(data["upload"]["url"]).split("/local/")[1].split("?")[0]) is None


def test_no_se_puede_descargar_sin_confirmar(client):
    data = _upload(client)
    assert client.get(f"/api/v1/files/{data['file']['file_id']}/download-url").status_code == 404


# ---------------------------------------------------------------------------
# Dueño y vencimiento
# ---------------------------------------------------------------------------

def test_archivo_de_otro_usuario_es_404(client):
    file_id = _upload(client)["file"]["file_id"]
    app.dependency_overrides[current_owner] = lambda: "otro-usuario"
    for r in (client.get(f"/api/v1/files/{file_id}"), client.post(f"/api/v1/files/{file_id}/complete"),
              client.get(f"/api/v1/files/{file_id}/download-url")):
        assert r.status_code == 404


def test_archivo_vencido_es_404(client):
    file_id = _upload(client)["file"]["file_id"]
    with get_connection() as conn:
        conn.execute("UPDATE files SET expires_at = '2020-01-01T00:00:00Z' WHERE id = ?", (file_id,))
    assert client.get(f"/api/v1/files/{file_id}").status_code == 404


# ---------------------------------------------------------------------------
# URLs firmadas del almacenamiento local
# ---------------------------------------------------------------------------

def test_put_con_otro_content_type_se_rechaza(client):
    data = _ask(client).json()
    r = client.put(_path(data["upload"]["url"]), content=PDF, headers={"Content-Type": "text/plain"})
    assert r.status_code == 403


def test_url_vencida_o_alterada_se_rechaza(client, storage):
    data = _ask(client).json()
    url = _path(data["upload"]["url"])
    headers = data["upload"]["headers"]
    tampered = url.replace("/uploads/", "/uploads/x")
    assert client.put(tampered, content=PDF, headers=headers).status_code == 403
    key = urlsplit(url).path.split("/local/")[1]
    old = int(time.time()) - 10
    expired = f"/api/v1/files/local/{key}?exp={old}&sig={storage.sign('PUT', key, old, 'application/pdf')}"
    assert client.put(expired, content=PDF, headers=headers).status_code == 403


def test_clave_que_sale_de_la_carpeta_se_rechaza(storage):
    for key in ("../fuera.pdf", "/etc/passwd", "users/../../x", "users//x"):
        with pytest.raises(StorageError):
            storage.path_for(key)


def test_con_r2_las_rutas_locales_no_existen(client, monkeypatch):
    storage_service.reset_storage(R2Storage("cuenta", "id", "secreto", "bucket"))
    assert client.put("/api/v1/files/local/users/anon/uploads/x.pdf?exp=1&sig=x", content=PDF).status_code == 404
    assert client.get("/api/v1/files/local/users/anon/uploads/x.pdf?exp=1&sig=x&name=a&type=b").status_code == 404


# ---------------------------------------------------------------------------
# Exportaciones con enlace
# ---------------------------------------------------------------------------

def test_exportar_con_enlace_guarda_el_archivo_y_devuelve_url_firmada(client):
    analysis = client.post("/api/v1/analysis/sample?mock=true", json={"text": "Texto de prueba para exportar. " * 20})
    analysis_id = analysis.json()["analysis_id"]
    direct = client.get("/api/v1/export/content", params={"analysis_id": analysis_id, "format": "md"})
    linked = client.get("/api/v1/export/content", params={"analysis_id": analysis_id, "format": "md", "delivery": "link"})
    assert linked.status_code == 200 and linked.headers["content-type"] == "application/json"
    body = linked.json()
    assert body["file"]["kind"] == "export" and body["file"]["status"] == "uploaded"
    assert body["download"]["method"] == "GET"
    assert "/users/anon/exports/" in body["download"]["url"]
    got = client.get(_path(body["download"]["url"]))
    assert got.content == direct.content
    report = client.get("/api/v1/export/report", params={"analysis_id": analysis_id, "format": "txt", "delivery": "link"})
    assert report.status_code == 200 and report.json()["file"]["filename"].endswith(".txt")


def test_exportar_sin_delivery_sigue_devolviendo_el_archivo(client):
    analysis_id = client.post("/api/v1/analysis/sample?mock=true", json={"text": "Texto de prueba. " * 20}).json()["analysis_id"]
    r = client.get("/api/v1/export/content", params={"analysis_id": analysis_id, "format": "txt"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert _rows() == []


# ---------------------------------------------------------------------------
# R2 (boto3 sin red)
# ---------------------------------------------------------------------------

@pytest.fixture
def r2():
    return R2Storage("cuenta123", "AKIDPRUEBA", "secreto", "archivos")


def test_r2_firma_put_con_content_type(r2):
    upload = r2.presign_put("users/anon/uploads/abc.pdf", "application/pdf", 100, 300)
    parts = urlsplit(upload.url)
    query = parse_qs(parts.query)
    assert parts.netloc == "cuenta123.r2.cloudflarestorage.com" and parts.path == "/archivos/users/anon/uploads/abc.pdf"
    assert query["X-Amz-Expires"] == ["300"] and "content-type" in query["X-Amz-SignedHeaders"][0]
    assert upload.method == "PUT" and upload.headers == {"Content-Type": "application/pdf"}


def test_r2_firma_get_con_nombre_original(r2):
    url = r2.presign_get("users/anon/exports/x.pdf", "Informe ñandú.pdf", "application/pdf", 120)
    query = parse_qs(urlsplit(url).query)
    assert query["response-content-disposition"] == [content_disposition("Informe ñandú.pdf")]
    assert query["response-content-type"] == ["application/pdf"]


def test_r2_head_inexistente_es_none_y_errores_son_storage_error(r2):
    with Stubber(r2._s3) as stub:
        stub.add_client_error("head_object", service_error_code="404", http_status_code=404)
        stub.add_response("head_object", {"ContentLength": 42, "ContentType": "application/pdf"})
        stub.add_client_error("head_object", service_error_code="AccessDenied", http_status_code=403)
        stub.add_client_error("head_bucket", service_error_code="NoSuchBucket", http_status_code=404)
        assert r2.head("a") is None
        assert r2.head("b").size == 42
        with pytest.raises(StorageError):
            r2.head("c")
        with pytest.raises(StorageError):
            r2.ping()


def test_content_disposition_con_caracteres_no_ascii():
    assert content_disposition('a"b ñ.pdf') == "attachment; filename=\"a_b _.pdf\"; filename*=UTF-8''a%22b%20%C3%B1.pdf"


def test_r2_exige_credenciales_al_arrancar():
    with pytest.raises(ValueError, match="R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET"):
        Settings(STORAGE_BACKEND="r2", R2_ACCOUNT_ID="", R2_ACCESS_KEY_ID="", R2_SECRET_ACCESS_KEY="", R2_BUCKET="")
