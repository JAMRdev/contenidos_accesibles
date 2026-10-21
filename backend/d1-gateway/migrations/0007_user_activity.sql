-- Historial de uso por usuario y usuarios de la API para las estadísticas de /admin (Issue #104).
-- Igual que en SCHEMA (app/db/database.py). Ambas tablas son permanentes: ninguna limpieza las borra.
CREATE TABLE IF NOT EXISTS user_activity (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at              TEXT NOT NULL,
    user_id                 TEXT NOT NULL,
    action                  TEXT NOT NULL,
    format                  TEXT,
    detail                  TEXT,
    adaptation_type         TEXT,
    mock                    INTEGER NOT NULL DEFAULT 0,
    size_bytes              INTEGER,
    score                   INTEGER,
    barriers                INTEGER,
    provider                TEXT,
    model                   TEXT,
    latency_ms              INTEGER,
    tokens                  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_user_activity_created_at
    ON user_activity(created_at);
CREATE INDEX IF NOT EXISTS idx_user_activity_user_created
    ON user_activity(user_id, created_at);

CREATE TABLE IF NOT EXISTS api_users (
    user_id                 TEXT PRIMARY KEY,
    source                  TEXT NOT NULL,
    role                    TEXT NOT NULL,
    name                    TEXT,
    email                   TEXT,
    avatar_url              TEXT,
    first_seen_at           TEXT NOT NULL,
    last_seen_at            TEXT NOT NULL,
    profile_fetched_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_api_users_last_seen
    ON api_users(last_seen_at);

-- Lo que todavía no venció pasa al historial, para no arrancar las estadísticas de cero.
-- Solo metadatos, igual que las filas nuevas.
INSERT INTO user_activity (created_at, user_id, action, format, mock, score, barriers, provider, model, latency_ms)
SELECT created_at, user_id, 'analysis', source_type,
       COALESCE(json_extract(diagnostic_json, '$.metadata.provider') = 'mock', 0),
       json_extract(diagnostic_json, '$.estimated_score'),
       json_array_length(diagnostic_json, '$.barriers'),
       json_extract(diagnostic_json, '$.metadata.provider'),
       json_extract(diagnostic_json, '$.metadata.model'),
       json_extract(diagnostic_json, '$.metadata.latency_ms')
FROM analyses WHERE json_valid(diagnostic_json);

INSERT INTO user_activity (created_at, user_id, action, adaptation_type, mock, provider, model, latency_ms)
SELECT created_at, user_id, 'proposal',
       json_extract(proposal_json, '$.adaptation_type'),
       COALESCE(json_extract(proposal_json, '$.metadata.provider') = 'mock', 0),
       json_extract(proposal_json, '$.metadata.provider'),
       json_extract(proposal_json, '$.metadata.model'),
       json_extract(proposal_json, '$.metadata.latency_ms')
FROM adaptation_proposals WHERE json_valid(proposal_json);

INSERT INTO user_activity (created_at, user_id, action, detail, adaptation_type)
SELECT created_at, user_id, 'decision', CASE WHEN action = 'deshacer' THEN 'deshacer' ELSE to_status END, adaptation_type
FROM adaptation_decisions;

-- Archivos vigentes: la extensión es lo que sigue al último punto (nunca el nombre)
INSERT INTO user_activity (created_at, user_id, action, format, size_bytes)
SELECT COALESCE(uploaded_at, created_at), user_id, kind,
       CASE WHEN instr(filename, '.') > 0
            THEN lower(replace(filename, rtrim(filename, replace(filename, '.', '')), '')) END,
       size_bytes
FROM files WHERE status = 'uploaded';

INSERT INTO api_users (user_id, source, role, first_seen_at, last_seen_at)
SELECT user_id,
       CASE WHEN user_id = 'anon' THEN 'anon' WHEN user_id LIKE 'test-web\_%' ESCAPE '\' THEN 'test_web' ELSE 'frontend' END,
       'docente', MIN(created_at), MAX(created_at)
FROM user_activity GROUP BY user_id;
