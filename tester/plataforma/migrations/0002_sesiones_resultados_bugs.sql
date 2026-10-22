-- Sesiones de QA, resultados, bugs y comentarios (Issue #127).

-- Una sesión es una ronda de pruebas contra un entorno.
CREATE TABLE IF NOT EXISTS qa_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    environment TEXT NOT NULL CHECK (environment IN ('produccion', 'staging', 'local')),
    base_url TEXT NOT NULL,
    backend_version TEXT,
    notes TEXT,
    status TEXT NOT NULL DEFAULT 'abierta' CHECK (status IN ('abierta', 'cerrada')),
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    closed_by TEXT,
    closed_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_qa_sessions_created_at ON qa_sessions (created_at);

-- Resultado de un caso del catálogo en una sesión (uno por caso; sin fila = pendiente).
CREATE TABLE IF NOT EXISTS qa_results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES qa_sessions (id) ON DELETE CASCADE,
    case_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('paso', 'fallo', 'bloqueado', 'omitido')),
    notes TEXT,
    updated_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    UNIQUE (session_id, case_id)
);
CREATE INDEX IF NOT EXISTS idx_qa_results_case ON qa_results (case_id);

-- Bug encontrado en un caso. Su estado es independiente del resultado: lo cambia QA y,
-- una vez publicado, también GitHub (sincronización, #134).
CREATE TABLE IF NOT EXISTS qa_bugs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    case_id TEXT NOT NULL,
    section TEXT NOT NULL,
    session_id INTEGER REFERENCES qa_sessions (id) ON DELETE SET NULL,
    environment TEXT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    severity TEXT NOT NULL CHECK (severity IN ('alta', 'media', 'baja')),
    status TEXT NOT NULL DEFAULT 'borrador'
        CHECK (status IN ('borrador', 'abierto', 'en_curso', 'resuelto', 'verificado', 'reabierto', 'descartado')),
    issue_number INTEGER,
    issue_url TEXT,
    github_state TEXT,
    github_state_reason TEXT,
    project_status TEXT,
    synced_at TEXT,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_qa_bugs_case ON qa_bugs (case_id);
CREATE INDEX IF NOT EXISTS idx_qa_bugs_status ON qa_bugs (status);
CREATE UNIQUE INDEX IF NOT EXISTS idx_qa_bugs_issue ON qa_bugs (issue_number) WHERE issue_number IS NOT NULL;

-- Cambios de estado de un bug y quién o qué los hizo.
CREATE TABLE IF NOT EXISTS qa_bug_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    bug_id INTEGER NOT NULL REFERENCES qa_bugs (id) ON DELETE CASCADE,
    from_status TEXT,
    to_status TEXT NOT NULL,
    source TEXT NOT NULL CHECK (source IN ('qa', 'github', 'sincronizacion')),
    actor TEXT,
    note TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_qa_bug_history_bug ON qa_bug_history (bug_id);

-- Comentarios sobre un resultado o un bug. Los de GitHub llegan con la sincronización (#134);
-- los internos marcados para GitHub se publican con #130.
CREATE TABLE IF NOT EXISTS qa_comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL CHECK (target_type IN ('resultado', 'bug')),
    target_id INTEGER NOT NULL,
    author TEXT NOT NULL,
    body TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'plataforma' CHECK (origin IN ('plataforma', 'github')),
    to_github INTEGER NOT NULL DEFAULT 0,
    github_comment_id INTEGER,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_qa_comments_target ON qa_comments (target_type, target_id);
