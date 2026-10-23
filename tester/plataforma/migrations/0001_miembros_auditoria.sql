-- Login con Clerk y roles de la plataforma de QA (Issue #126).

-- Cuentas de GitHub con acceso y su rol: 'viewer' ve, 'qa' además registra resultados y publica,
-- 'admin' además administra miembros. Los admins de QA_ADMINS (wrangler.toml) no viven acá.
CREATE TABLE IF NOT EXISTS qa_members (
    github_username TEXT PRIMARY KEY COLLATE NOCASE,
    role TEXT NOT NULL CHECK (role IN ('viewer', 'qa', 'admin')),
    enabled INTEGER NOT NULL DEFAULT 1,
    added_by TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

-- Usuarios de Clerk ya identificados (cuenta de GitHub verificada), para no consultar la API
-- de Clerk en cada request. Se vuelven a consultar pasada una hora.
CREATE TABLE IF NOT EXISTS qa_users (
    clerk_user_id TEXT PRIMARY KEY,
    github_username TEXT NOT NULL,
    display_name TEXT,
    avatar_url TEXT,
    fetched_at TEXT NOT NULL,
    last_seen_at TEXT
);

-- Registro de auditoría: quién hizo qué y cuándo. detail es JSON.
CREATE TABLE IF NOT EXISTS qa_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    actor TEXT,
    action TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_qa_audit_created_at ON qa_audit (created_at);
