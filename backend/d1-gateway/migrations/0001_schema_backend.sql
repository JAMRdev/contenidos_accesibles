-- Esquema completo del backend para Cloudflare D1 (Issue #59).
-- Mismo esquema que app/db/database.py (SCHEMA), que sigue creando la base SQLite local;
-- tests/test_d1_data_layer.py verifica que ambos coincidan. Cambios futuros: migración
-- nueva en este directorio + actualizar SCHEMA.
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
    expires_at              TEXT NOT NULL
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
    expires_at              TEXT NOT NULL
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
    expires_at              TEXT NOT NULL
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
