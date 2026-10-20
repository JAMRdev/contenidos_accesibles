import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from app.core.config import settings

# Cada fila es una "conexión de IA": proveedor + modelo + API key + umbrales.
# La tabla conserva el nombre api_keys por compatibilidad con bases existentes.
SCHEMA = """
CREATE TABLE IF NOT EXISTS api_keys (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    label                   TEXT NOT NULL,
    encrypted_key           TEXT NOT NULL,
    enabled                 INTEGER NOT NULL DEFAULT 1,
    is_current              INTEGER NOT NULL DEFAULT 0,
    -- Proveedor ('gemini' usa el SDK nativo; el resto, API compatible con OpenAI)
    provider                TEXT NOT NULL DEFAULT 'gemini',
    base_url                TEXT,
    model                   TEXT NOT NULL DEFAULT '',
    -- Menor número = se usa primero. Ante un límite, se pasa a la siguiente.
    priority                INTEGER NOT NULL DEFAULT 100,
    -- JSON con parámetros propios del proveedor (ej. thinking_budget, reasoning_effort)
    extra_params            TEXT NOT NULL DEFAULT '{}',
    -- 1 = conexión principal sincronizada desde .env (AI_API_KEY) en cada arranque
    managed_by_env          INTEGER NOT NULL DEFAULT 0,
    -- Umbrales configurables por el admin, calcados de las 3 dimensiones
    -- que Google muestra en AI Studio (RPM / TPM / RPD). TPD es un extra
    -- opcional que Google no expone pero puede servir como techo propio.
    rpm_threshold           INTEGER,
    tpm_threshold           INTEGER,
    rpd_threshold           INTEGER,
    tpd_threshold           INTEGER,
    -- Contadores de ventana diaria (resetean por fecha UTC)
    request_count           INTEGER NOT NULL DEFAULT 0,
    prompt_token_count      INTEGER NOT NULL DEFAULT 0,
    candidates_token_count  INTEGER NOT NULL DEFAULT 0,
    total_token_count       INTEGER NOT NULL DEFAULT 0,
    last_reset_date         TEXT NOT NULL DEFAULT (date('now')),
    -- Contadores de ventana de minuto (resetean por minuto UTC)
    minute_request_count    INTEGER NOT NULL DEFAULT 0,
    minute_token_count      INTEGER NOT NULL DEFAULT 0,
    last_reset_minute       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M', 'now')),
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_api_keys_current
    ON api_keys(is_current) WHERE is_current = 1;

-- Diagnósticos de accesibilidad con expiración (TTL, Semana 4 - Issue #26)
CREATE TABLE IF NOT EXISTS analyses (
    id                      TEXT PRIMARY KEY,
    source_type             TEXT NOT NULL,
    filename                TEXT,
    extracted_text          TEXT NOT NULL,
    diagnostic_json         TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    expires_at              TEXT NOT NULL,
    -- Dueño (Issue #61): id de usuario de Clerk, test-web_<tipo>_<usuario> o 'anon'
    user_id                 TEXT NOT NULL DEFAULT 'anon'
);
CREATE INDEX IF NOT EXISTS idx_analyses_expires_at
    ON analyses(expires_at);

-- Propuestas de adaptación con IA (Issue #27). Vencen junto con su diagnóstico.
CREATE TABLE IF NOT EXISTS adaptation_proposals (
    id                      TEXT PRIMARY KEY,
    analysis_id             TEXT NOT NULL,
    barrier_id              TEXT NOT NULL,
    status                  TEXT NOT NULL DEFAULT 'pendiente',
    proposal_json           TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    expires_at              TEXT NOT NULL,
    user_id                 TEXT NOT NULL DEFAULT 'anon'
);
CREATE INDEX IF NOT EXISTS idx_adaptation_proposals_analysis
    ON adaptation_proposals(analysis_id);
CREATE INDEX IF NOT EXISTS idx_adaptation_proposals_expires_at
    ON adaptation_proposals(expires_at);

-- Registro de decisiones docentes sobre las propuestas (Issue #29), para el informe.
CREATE TABLE IF NOT EXISTS adaptation_decisions (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    proposal_id             TEXT NOT NULL,
    analysis_id             TEXT NOT NULL,
    barrier_id              TEXT NOT NULL,
    adaptation_type         TEXT NOT NULL,
    action                  TEXT NOT NULL,
    from_status             TEXT NOT NULL,
    to_status               TEXT NOT NULL,
    edited_text             TEXT,
    created_at              TEXT NOT NULL,
    expires_at              TEXT NOT NULL,
    user_id                 TEXT NOT NULL DEFAULT 'anon'
);
CREATE INDEX IF NOT EXISTS idx_adaptation_decisions_analysis
    ON adaptation_decisions(analysis_id);

-- Historial de llamadas a la IA para las métricas del panel (/admin).
-- Una fila por intento: nunca guarda el material ni la respuesta.
CREATE TABLE IF NOT EXISTS ai_usage_events (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at              TEXT NOT NULL,
    connection_id           INTEGER,
    connection_label        TEXT NOT NULL,
    provider                TEXT NOT NULL,
    model                   TEXT NOT NULL,
    -- 'text' o 'vision'
    kind                    TEXT NOT NULL,
    -- 'ok', 'rate_limited' (429), 'server_error' (5xx) o 'error' (key, modelo o parámetros)
    outcome                 TEXT NOT NULL,
    prompt_tokens           INTEGER NOT NULL DEFAULT 0,
    output_tokens           INTEGER NOT NULL DEFAULT 0,
    total_tokens            INTEGER NOT NULL DEFAULT 0,
    latency_ms              INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_usage_events_created_at
    ON ai_usage_events(created_at);

-- Usuarios de GitHub con acceso al panel (/admin). 'admin' puede modificar todo y
-- administrar usuarios; 'viewer' solo ve. La cuenta del .env es admin y no vive acá.
CREATE TABLE IF NOT EXISTS admin_members (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    github_username         TEXT NOT NULL UNIQUE COLLATE NOCASE,
    role                    TEXT NOT NULL CHECK (role IN ('admin', 'viewer')),
    enabled                 INTEGER NOT NULL DEFAULT 1,
    display_name            TEXT,
    avatar_url              TEXT,
    clerk_user_id           TEXT,
    last_login_at           TEXT,
    added_by                TEXT,
    created_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at              TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

-- Archivos de los docentes en R2 (Issue #62). Solo metadatos: el archivo vive en el bucket
-- (o en la carpeta local en desarrollo). 'pending' hasta que el navegador sube y se confirma.
CREATE TABLE IF NOT EXISTS files (
    id                      TEXT PRIMARY KEY,
    user_id                 TEXT NOT NULL,
    object_key              TEXT NOT NULL UNIQUE,
    -- 'upload' (subido por el docente) o 'export' (generado por /export)
    kind                    TEXT NOT NULL DEFAULT 'upload' CHECK (kind IN ('upload', 'export')),
    filename                TEXT NOT NULL,
    content_type            TEXT NOT NULL,
    size_bytes              INTEGER NOT NULL CHECK (size_bytes > 0),
    status                  TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'uploaded')),
    created_at              TEXT NOT NULL,
    uploaded_at             TEXT,
    expires_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_user_created
    ON files(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_files_expires_at
    ON files(expires_at);

-- Enlaces para compartir archivos (Issue #65). Se guarda el hash SHA-256 del token, nunca el
-- token: quien lea la base no puede usar los enlaces. /s/{token} redirige a una URL firmada.
CREATE TABLE IF NOT EXISTS file_shares (
    id                      TEXT PRIMARY KEY,
    file_id                 TEXT NOT NULL,
    created_by              TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    expires_at              TEXT NOT NULL,
    revoked_at              TEXT,
    access_count            INTEGER NOT NULL DEFAULT 0,
    last_access_at          TEXT
);
CREATE INDEX IF NOT EXISTS idx_file_shares_file
    ON file_shares(file_id);
CREATE INDEX IF NOT EXISTS idx_file_shares_expires_at
    ON file_shares(expires_at);

-- Eventos de archivos y enlaces para las métricas del panel (Issue #98). Una fila por evento,
-- sin nombres, contenido ni ids de archivo. Se borran al superar AI_USAGE_RETENTION_DAYS.
CREATE TABLE IF NOT EXISTS file_events (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at              TEXT NOT NULL,
    -- upload, upload_rejected, export, download, share_created, share_opened, share_revoked
    event                   TEXT NOT NULL,
    -- extensión del archivo: pdf, docx, pptx, md o txt
    format                  TEXT,
    size_bytes              INTEGER,
    -- exportaciones: '<content|report>:<file|link>'; enlaces: 'upload' o 'export'
    detail                  TEXT
);
CREATE INDEX IF NOT EXISTS idx_file_events_created_at
    ON file_events(created_at);

-- Configuración editable desde /admin (Issue #64), p. ej. la retención de archivos.
-- Sin fila, vale el valor por defecto de la variable de entorno.
CREATE TABLE IF NOT EXISTS app_settings (
    key                     TEXT PRIMARY KEY,
    value                   TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    updated_by              TEXT
);
"""


_d1_client = None
_d1_lock = threading.Lock()

# Tablas que tiene que tener D1 (migraciones de d1-gateway/migrations) para arrancar
REQUIRED_TABLES = {"api_keys", "analyses", "adaptation_proposals", "adaptation_decisions", "ai_usage_events", "admin_members", "files", "file_shares", "file_events", "app_settings"}


def _shared_d1_client():
    """Un solo cliente HTTP para todo el proceso: reutiliza conexiones (sin un TLS nuevo por consulta)."""
    global _d1_client
    with _d1_lock:
        if _d1_client is None:
            from app.db.d1_client import client_from_settings

            _d1_client = client_from_settings()
        return _d1_client


def using_d1() -> bool:
    return settings.DATABASE_BACKEND == "d1"


@contextmanager
def get_connection() -> Iterator[sqlite3.Connection]:
    """
    Unidad de trabajo sobre la base: SQLite local (por defecto) o Cloudflare D1 vía el Worker
    pasarela (DATABASE_BACKEND=d1). En D1 las escrituras del bloque se mandan en un batch
    atómico al salir (ver app/db/d1_connection.py).
    """
    if using_d1():
        from app.db.d1_connection import D1Connection

        conn = D1Connection(_shared_d1_client())
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
        return

    Path(settings.DATABASE_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(settings.DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# Columnas agregadas al pasar de "keys de Gemini" a "conexiones de IA".
# Las bases creadas antes no las tienen: se agregan con ALTER TABLE al iniciar.
_MULTI_PROVIDER_COLUMNS = {
    "provider": "TEXT NOT NULL DEFAULT 'gemini'",
    "base_url": "TEXT",
    "model": "TEXT NOT NULL DEFAULT ''",
    "priority": "INTEGER NOT NULL DEFAULT 100",
    "extra_params": "TEXT NOT NULL DEFAULT '{}'",
}


# Columna agregada con la conexión principal gestionada por .env (AI_API_KEY).
_ENV_CONNECTION_COLUMNS = {
    "managed_by_env": "INTEGER NOT NULL DEFAULT 0",
}

_ADDED_COLUMNS = {**_MULTI_PROVIDER_COLUMNS, **_ENV_CONNECTION_COLUMNS}

# Dueño de diagnósticos, propuestas y decisiones (Issue #61). Lo previo queda a nombre de 'anon'.
# En D1 lo agrega la migración 0005_user_id.sql.
_OWNED_TABLES = ("analyses", "adaptation_proposals", "adaptation_decisions")
_USER_ID_COLUMN = "user_id TEXT NOT NULL DEFAULT 'anon'"


def _migrate_columns(conn: sqlite3.Connection) -> None:
    for table in _OWNED_TABLES:
        if "user_id" not in {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {_USER_ID_COLUMN}")

    existing = {row["name"] for row in conn.execute("PRAGMA table_info(api_keys)")}
    missing = [c for c in _ADDED_COLUMNS if c not in existing]
    for column in missing:
        conn.execute(f"ALTER TABLE api_keys ADD COLUMN {column} {_ADDED_COLUMNS[column]}")
    if "model" in missing:
        # Todas las keys previas eran de Gemini con el modelo global de .env.
        conn.execute("UPDATE api_keys SET model = ?", (settings.GEMINI_MODEL,))
    if "extra_params" in missing:
        # Conserva el comportamiento anterior: thinking desactivado en el análisis.
        conn.execute("""UPDATE api_keys SET extra_params = '{"thinking_budget": 0}'""")


def init_db() -> None:
    if using_d1():
        _check_d1_schema()
        return
    with get_connection() as conn:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        _migrate_columns(conn)


def _check_d1_schema() -> None:
    """En D1 el esquema lo crean las migraciones de wrangler: acá solo se verifica que estén aplicadas."""
    with get_connection() as conn:
        tables = {row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        missing = REQUIRED_TABLES - tables
        if missing:
            raise RuntimeError(
                f"A la base D1 le faltan las tablas {', '.join(sorted(missing))}. "
                "Aplicá las migraciones: cd d1-gateway && npm run migrate:remote"
            )
        # Columnas agregadas después (0005_user_id.sql)
        without_owner = [
            t for t in _OWNED_TABLES
            if "user_id" not in {row["name"] for row in conn.execute(f"PRAGMA table_info({t})")}
        ]
    if without_owner:
        raise RuntimeError(
            f"A las tablas {', '.join(without_owner)} de D1 les falta la columna user_id. "
            "Aplicá las migraciones: cd d1-gateway && npm run migrate:remote"
        )
