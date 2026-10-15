import json
from functools import lru_cache
from typing import Annotated, Literal, Union
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    # --- Entorno y Servidor ---
    # Literal restringe los valores a exactamente estos tres.
    # Si alguien escribe ENVIRONMENT=produccion en .env, la app no arranca.
    ENVIRONMENT: Literal["development", "staging", "production"] = "development"

    # --- Metadatos de la API ---
    PROJECT_NAME: str = "InnovaLab - Asistente de Accesibilidad"
    API_V1_STR: str = "/api/v1"
    VERSION: str = "0.1.0"

    # DEBUG False por defecto es la postura más segura.
    # En development se puede activar desde .env (DEBUG=true).
    # Controla el nivel de detalle en errores (no la visibilidad de Swagger/ReDoc).
    DEBUG: bool = False

    # Puerto del servidor (ge=1, le=65535 valida rango TCP válido con Pydantic)
    PORT: int = Field(default=8000, ge=1, le=65535)

    # --- Conexión de IA principal (gestionada por .env) ---
    # Si AI_API_KEY está definida, en cada arranque se crea o actualiza una conexión
    # con estos datos, habilitada y con la prioridad más alta. Editarla o borrarla
    # desde /admin no persiste: se restaura al reiniciar. Las demás conexiones
    # (respaldo) se siguen gestionando desde /admin.
    AI_API_KEY: SecretStr | None = None
    # Id de una plantilla de app/services/llm/providers.py (define base_url y límites).
    AI_PROVIDER: str = "groq"
    AI_MODEL: str = "openai/gpt-oss-120b"
    AI_CONNECTION_LABEL: str = "IL-1 gpt-oss-120b - PROD"

    # Modelo asignado a las keys de Gemini de bases anteriores a multi-proveedor
    # (solo lo usa la migración de app/db/database.py).
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # --- Panel de Administración (/admin, protegido con HTTP Basic Auth) ---
    ADMIN_USERNAME: str = "admin"
    # Sin default: si falta en .env, la app NO arranca (Fail-Fast).
    ADMIN_PASSWORD: SecretStr

    # --- Login de /adminv2 con Clerk (aplicación de Clerk propia del panel, separada de la de docentes) ---
    # "local": solo usuario/contraseña del .env · "clerk": solo Clerk · "ambos": Clerk + el login local como respaldo
    ADMIN_AUTH: Literal["local", "clerk", "ambos"] = "ambos"
    ADMIN_CLERK_PUBLISHABLE_KEY: str | None = None
    ADMIN_CLERK_SECRET_KEY: SecretStr | None = None
    # Clave pública PEM de Clerk (JWT public key): valida los tokens sin llamadas de red
    ADMIN_CLERK_JWT_KEY: str | None = None
    # Usuarios de GitHub con acceso al panel, separados por comas. Se cargan al arrancar si no existen;
    # después se administran desde la zona de usuarios del panel.
    ADMIN_GITHUB_ADMINS: str = ""
    ADMIN_GITHUB_VIEWERS: str = ""

    # --- Persistencia local (SQLite) de conexiones de IA y contadores de uso ---
    DATABASE_PATH: str = "app_data.db"

    # Tiempo de retención de diagnósticos antes de expirar (TTL en horas, Semana 4)
    ANALYSIS_TTL_HOURS: int = 24

    # Días que se conserva el historial de llamadas a la IA (métricas del panel)
    AI_USAGE_RETENTION_DAYS: int = 30

    # Clave simétrica (Fernet) para cifrar las API keys guardadas en la DB.
    # Generarla con: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    DB_ENCRYPTION_KEY: SecretStr

    # --- Documentación (/docs, /redoc, openapi.json) ---
    # En development queda abierta. En staging/production exige la sesión del panel
    # (Clerk o ADMIN_USERNAME/ADMIN_PASSWORD; admin y solo lectura pueden verla).

    # --- Seguridad / CORS ---
    # Acepta string separado por comas o lista JSON desde el .env:
    #   BACKEND_CORS_ORIGINS=https://mi-app.vercel.app,http://localhost:3000
    #   BACKEND_CORS_ORIGINS=["https://mi-app.vercel.app","http://localhost:3000"]
    # NoDecode evita que pydantic-settings lo decodifique como JSON antes del
    # validador: sin él, el formato con comas rompe el arranque (Issue #57).
    BACKEND_CORS_ORIGINS: Annotated[list[str], NoDecode] = [
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]

    @field_validator("ADMIN_CLERK_JWT_KEY", mode="before")
    @classmethod
    def normalize_pem(cls, v: str | None) -> str | None:
        """
        La PEM puede llegar en una línea con "\\n" literales (así queda al pegarla en el panel de
        Railway, que no interpreta escapes como el .env): se convierten en saltos de línea reales.
        """
        if not isinstance(v, str):
            return v
        v = v.strip().strip('"').strip("'").replace("\\r\\n", "\n").replace("\\n", "\n").strip()
        return v or None

    @field_validator("BACKEND_CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, list[str]]) -> list[str]:
        """Parsea orígenes CORS desde string CSV, lista JSON o lista directa."""
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("["):
                try:
                    return json.loads(v)
                except json.JSONDecodeError as e:
                    raise ValueError(f"BACKEND_CORS_ORIGINS no es una lista JSON válida: {e}") from e
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        if isinstance(v, list):
            return v
        raise ValueError(f"Formato no válido para BACKEND_CORS_ORIGINS: {v}")

    # --- Rate limit de los endpoints que consumen IA real (app/core/rate_limit.py) ---
    # Ventanas deslizantes en memoria; las llamadas con ?mock=true no cuentan.
    # 0 desactiva ese límite. El global protege la cuota aunque se falsee la IP.
    AI_RATE_LIMIT_PER_IP_MINUTE: int = Field(default=5, ge=0)
    AI_RATE_LIMIT_PER_IP_DAY: int = Field(default=50, ge=0)
    AI_RATE_LIMIT_GLOBAL_MINUTE: int = Field(default=20, ge=0)
    AI_RATE_LIMIT_GLOBAL_DAY: int = Field(default=500, ge=0)

    # --- Web de pruebas (web/, Static Site en Render) ---
    # URL pública de la web de pruebas. Si está definida, /admin muestra el enlace
    # y su origen se suma a CORS sin tener que repetirlo en BACKEND_CORS_ORIGINS.
    TEST_WEB_URL: str | None = None

    @property
    def test_web_origin(self) -> str | None:
        """Origen (esquema + host) de la web de pruebas, o None si TEST_WEB_URL no está definida."""
        if not self.TEST_WEB_URL:
            return None
        parts = urlsplit(self.TEST_WEB_URL.strip())
        return f"{parts.scheme}://{parts.netloc}" if parts.scheme and parts.netloc else None

    @property
    def cors_origins(self) -> list[str]:
        """BACKEND_CORS_ORIGINS más el origen (esquema + host) de TEST_WEB_URL."""
        origins = list(self.BACKEND_CORS_ORIGINS)
        if self.TEST_WEB_URL:
            parts = urlsplit(self.TEST_WEB_URL.strip())
            origin = f"{parts.scheme}://{parts.netloc}"
            if parts.scheme and parts.netloc and origin not in origins:
                origins.append(origin)
        return origins

    @property
    def admin_clerk_enabled(self) -> bool:
        """Clerk solo se ofrece si el modo lo permite y están las tres claves."""
        return self.ADMIN_AUTH != "local" and bool(
            self.ADMIN_CLERK_PUBLISHABLE_KEY and self.ADMIN_CLERK_SECRET_KEY and self.ADMIN_CLERK_JWT_KEY
        )

    @property
    def admin_local_login_enabled(self) -> bool:
        """El login con usuario/contraseña del .env queda como respaldo si Clerk no está configurado."""
        return self.ADMIN_AUTH != "clerk" or not self.admin_clerk_enabled

    @property
    def admin_github_seed(self) -> dict[str, str]:
        """{usuario_de_github: rol} a cargar al arrancar. Si alguien figura en las dos listas, queda como admin."""
        def parse(raw: str) -> list[str]:
            return [u.strip().lstrip("@") for u in raw.split(",") if u.strip()]

        seed = {u: "viewer" for u in parse(self.ADMIN_GITHUB_VIEWERS)}
        seed.update({u: "admin" for u in parse(self.ADMIN_GITHUB_ADMINS)})
        return seed

    @property
    def docs_protected(self) -> bool:
        """True si /docs, /redoc y openapi.json exigen credenciales."""
        return self.ENVIRONMENT != "development"


    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",  # Variables extra en .env se ignoran sin lanzar error
    )


@lru_cache
def get_settings() -> Settings:
    """
    Retorna una instancia única y cacheada de Settings (patrón Singleton).

    El decorador @lru_cache garantiza que el archivo .env se lea del disco
    una sola vez durante todo el ciclo de vida de la aplicación.
    Al usar Depends(get_settings) en los endpoints, FastAPI permite
    sobreescribir la configuración fácilmente en tests automatizados.
    """
    return Settings()


# Instancia de acceso directo (para uso fuera del sistema de inyección de dependencias)
settings = get_settings()
