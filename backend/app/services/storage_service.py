"""
Almacenamiento de archivos de los docentes (Issue #62).

Regla de diseño: la API no transporta archivos. El navegador sube y descarga con URLs
firmadas de pocos minutos; el backend solo firma, verifica (HEAD) y borra.

- R2Storage (STORAGE_BACKEND=r2, producción): bucket privado de Cloudflare R2 por su API
  compatible con S3 (boto3). Las URLs apuntan a R2 y el archivo nunca pasa por Railway.
- LocalStorage (STORAGE_BACKEND=local, desarrollo): carpeta local. Las URLs firmadas
  (HMAC) apuntan a /api/v1/files/local/..., que solo responde en este modo.

Las dos devuelven URLs con el mismo contrato, así el frontend no distingue el modo.
"""
import base64
import hashlib
import hmac
import secrets
import threading
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol
from urllib.parse import quote, urlencode

from app.core.config import settings

LOCAL_ROUTE = "/api/v1/files/local"


@dataclass(frozen=True)
class PresignedUpload:
    url: str
    method: str
    headers: dict[str, str]


@dataclass(frozen=True)
class StoredObject:
    size: int
    content_type: str | None


class StorageError(Exception):
    """El almacenamiento no respondió o rechazó la operación."""


class Storage(Protocol):
    name: str

    def presign_put(self, key: str, content_type: str, size: int, expires_s: int) -> PresignedUpload: ...
    def presign_get(self, key: str, filename: str, content_type: str, expires_s: int) -> str: ...
    def head(self, key: str) -> StoredObject | None: ...
    def read_prefix(self, key: str, length: int) -> bytes: ...
    def put(self, key: str, data: bytes, content_type: str) -> None: ...
    def delete(self, key: str) -> None: ...
    def ping(self) -> None: ...


def new_object_key(owner_id: str, folder: str, extension: str) -> str:
    """users/<dueño>/<carpeta>/<aleatorio><ext>: no adivinable y con prefijo por usuario."""
    return f"users/{owner_id}/{folder}/{secrets.token_urlsafe(24)}{extension}"


def content_disposition(filename: str) -> str:
    """attachment con el nombre original (RFC 6266: ASCII de respaldo + UTF-8)."""
    fallback = "".join(c if c.isascii() and c not in '"\\' and c.isprintable() else "_" for c in filename) or "archivo"
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename)}"


# ---------------------------------------------------------------------------
# Cloudflare R2
# ---------------------------------------------------------------------------

class R2Storage:
    name = "r2"

    def __init__(self, account_id: str, access_key_id: str, secret_access_key: str, bucket: str, client=None):
        self.bucket = bucket
        if client is None:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "s3",
                endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
                aws_access_key_id=access_key_id,
                aws_secret_access_key=secret_access_key,
                region_name="auto",
                config=Config(signature_version="s3v4", retries={"max_attempts": 2, "mode": "standard"},
                              connect_timeout=5, read_timeout=15),
            )
        self._s3 = client

    def presign_put(self, key: str, content_type: str, size: int, expires_s: int) -> PresignedUpload:
        # Content-Type va firmado: R2 rechaza la subida si el navegador manda otro.
        # El tamaño se controla al confirmar (HEAD), porque el navegador fija Content-Length.
        url = self._s3.generate_presigned_url(
            "put_object", Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type}, ExpiresIn=expires_s,
        )
        return PresignedUpload(url=url, method="PUT", headers={"Content-Type": content_type})

    def presign_get(self, key: str, filename: str, content_type: str, expires_s: int) -> str:
        return self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key, "ResponseContentDisposition": content_disposition(filename),
                    "ResponseContentType": content_type},
            ExpiresIn=expires_s,
        )

    def head(self, key: str) -> StoredObject | None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            meta = self._s3.head_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return None
            raise StorageError(f"R2 respondió un error al consultar el archivo ({e.response.get('Error', {}).get('Code')}).") from e
        except BotoCoreError as e:
            raise StorageError(f"No se pudo conectar con R2 ({e.__class__.__name__}).") from e
        return StoredObject(size=int(meta["ContentLength"]), content_type=meta.get("ContentType"))

    def read_prefix(self, key: str, length: int) -> bytes:
        return self._call("get_object", Key=key, Range=f"bytes=0-{length - 1}")["Body"].read()

    def put(self, key: str, data: bytes, content_type: str) -> None:
        self._call("put_object", Key=key, Body=data, ContentType=content_type)

    def delete(self, key: str) -> None:
        self._call("delete_object", Key=key)

    def ping(self) -> None:
        self._call("head_bucket")

    def _call(self, operation: str, **params):
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            return getattr(self._s3, operation)(Bucket=self.bucket, **params)
        except ClientError as e:
            raise StorageError(f"R2 respondió un error ({e.response.get('Error', {}).get('Code')}).") from e
        except BotoCoreError as e:
            raise StorageError(f"No se pudo conectar con R2 ({e.__class__.__name__}).") from e


# ---------------------------------------------------------------------------
# Disco local (desarrollo)
# ---------------------------------------------------------------------------

class LocalStorage:
    """Carpeta local con URLs firmadas por HMAC que sirve el propio backend (solo desarrollo)."""

    name = "local"

    def __init__(self, root: str, secret: bytes):
        self.root = Path(root).resolve()
        self._secret = secret

    def path_for(self, key: str) -> Path:
        """Ruta del objeto; rechaza claves que salgan de la carpeta (.., rutas absolutas)."""
        parts = PurePosixPath(key).parts
        if not parts or key.startswith("/") or "//" in key or "\\" in key or any(p in ("..", ".") for p in parts):
            raise StorageError("Clave de objeto inválida.")
        path = (self.root / Path(*parts)).resolve()
        if self.root not in path.parents:
            raise StorageError("Clave de objeto inválida.")
        return path

    def sign(self, method: str, key: str, expires: int, content_type: str, extra: str = "") -> str:
        message = "\n".join((method, key, str(expires), content_type, extra)).encode()
        digest = hmac.new(self._secret, message, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def verify(self, method: str, key: str, expires: int, content_type: str, signature: str, extra: str = "") -> bool:
        if expires < time.time():
            return False
        return hmac.compare_digest(self.sign(method, key, expires, content_type, extra), signature)

    def presign_put(self, key: str, content_type: str, size: int, expires_s: int) -> PresignedUpload:
        expires = int(time.time()) + expires_s
        query = urlencode({"exp": expires, "sig": self.sign("PUT", key, expires, content_type)})
        return PresignedUpload(url=f"{LOCAL_ROUTE}/{quote(key)}?{query}", method="PUT", headers={"Content-Type": content_type})

    def presign_get(self, key: str, filename: str, content_type: str, expires_s: int) -> str:
        expires = int(time.time()) + expires_s
        query = urlencode({"exp": expires, "name": filename, "type": content_type,
                           "sig": self.sign("GET", key, expires, content_type, filename)})
        return f"{LOCAL_ROUTE}/{quote(key)}?{query}"

    def head(self, key: str) -> StoredObject | None:
        path = self.path_for(key)
        if not path.is_file():
            return None
        meta = path.with_name(path.name + ".type")
        return StoredObject(size=path.stat().st_size, content_type=meta.read_text() if meta.exists() else None)

    def read_prefix(self, key: str, length: int) -> bytes:
        with self.path_for(key).open("rb") as f:
            return f.read(length)

    def put(self, key: str, data: bytes, content_type: str) -> None:
        path = self.path_for(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.with_name(path.name + ".type").write_text(content_type)

    def delete(self, key: str) -> None:
        path = self.path_for(key)
        for p in (path, path.with_name(path.name + ".type")):
            p.unlink(missing_ok=True)

    def ping(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        probe = self.root / ".ping"
        probe.write_text("ok")
        probe.unlink()


# ---------------------------------------------------------------------------
# Instancia del proceso
# ---------------------------------------------------------------------------

_storage: Storage | None = None
_lock = threading.Lock()


def _local_secret() -> bytes:
    # Derivada de DB_ENCRYPTION_KEY: no hace falta otra variable y no sirve para otra cosa
    return hashlib.sha256(b"local-storage-url:" + settings.DB_ENCRYPTION_KEY.get_secret_value().encode()).digest()


def get_storage() -> Storage:
    global _storage
    with _lock:
        if _storage is None:
            if settings.STORAGE_BACKEND == "r2":
                _storage = R2Storage(
                    settings.R2_ACCOUNT_ID, settings.R2_ACCESS_KEY_ID,
                    settings.R2_SECRET_ACCESS_KEY.get_secret_value(), settings.R2_BUCKET,
                )
            else:
                _storage = LocalStorage(settings.LOCAL_STORAGE_PATH, _local_secret())
        return _storage


def reset_storage(storage: Storage | None = None) -> None:
    """Tests: reemplaza (o vuelve a crear en el próximo uso) el almacenamiento del proceso."""
    global _storage
    with _lock:
        _storage = storage
