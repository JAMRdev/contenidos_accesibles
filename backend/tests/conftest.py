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


@pytest.fixture(autouse=True, scope="session")
def isolated_database(tmp_path_factory):
    from app.core.config import settings
    from app.db.database import init_db

    original = settings.DATABASE_PATH
    settings.DATABASE_PATH = str(tmp_path_factory.mktemp("db") / "test.db")
    init_db()
    yield
    settings.DATABASE_PATH = original
