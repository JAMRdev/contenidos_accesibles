"""
Configuración común de pytest (Issue #44).

- Variables obligatorias con valores de prueba, para que `pytest` corra sin `.env` (en CI no hay).
  Las variables de entorno tienen prioridad sobre el `.env`, así que localmente tampoco se usan
  las credenciales reales.
- Base SQLite temporal por corrida: los tests nunca escriben en `app_data.db`, donde están las
  API keys locales. Los tests que necesitan su propia base la siguen cambiando con monkeypatch.
"""
import os

import pytest
from cryptography.fernet import Fernet

os.environ.setdefault("ADMIN_PASSWORD", "clave-de-prueba")
os.environ.setdefault("DB_ENCRYPTION_KEY", Fernet.generate_key().decode())
# Sin conexión de IA del .env: ningún test puede llamar a un proveedor real por accidente.
os.environ["AI_API_KEY"] = ""


# TEST_DATABASE_BACKEND=d1 corre toda la suite contra el adaptador de D1 (app/db/d1_connection.py)
# con un D1 de prueba sobre SQLite (tests/fake_d1.py). Por defecto, SQLite directo como en local.
D1_MODE = os.environ.get("TEST_DATABASE_BACKEND") == "d1"


@pytest.fixture(autouse=True, scope="session")
def isolated_database(tmp_path_factory):
    from app.core.config import settings
    from app.db import database
    from app.db.database import init_db

    from app.services import storage_service

    original = (settings.DATABASE_PATH, settings.DATABASE_BACKEND, database._d1_client, settings.LOCAL_STORAGE_PATH)
    settings.DATABASE_PATH = str(tmp_path_factory.mktemp("db") / "test.db")
    # Archivos (Issue #62): carpeta temporal, nunca local_storage/ del repo
    settings.LOCAL_STORAGE_PATH = str(tmp_path_factory.mktemp("storage"))
    storage_service.reset_storage()
    if D1_MODE:
        from tests.fake_d1 import FakeD1Client

        settings.DATABASE_BACKEND = "d1"
        database._d1_client = FakeD1Client()
    init_db()
    yield
    settings.DATABASE_PATH, settings.DATABASE_BACKEND, database._d1_client, settings.LOCAL_STORAGE_PATH = original
    storage_service.reset_storage()
