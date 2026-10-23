-- Publicación en GitHub (Issue #130).

-- Issue de ejecución de cada sesión (uno por sesión; publicar de nuevo lo actualiza)
ALTER TABLE qa_sessions ADD COLUMN execution_issue_number INTEGER;
ALTER TABLE qa_sessions ADD COLUMN execution_issue_url TEXT;

-- Qué se publicó en GitHub. `key` identifica cada publicación (por ejemplo bug:7:issue o
-- sesion:3:ejecucion): publicar dos veces no duplica y un intento a medias se retoma.
CREATE TABLE IF NOT EXISTS qa_publications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    key TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('issue', 'duplicado', 'comentario', 'ejecucion', 'propuesta_cierre')),
    bug_id INTEGER,
    session_id INTEGER,
    issue_number INTEGER,
    comment_id INTEGER,
    status TEXT NOT NULL DEFAULT 'pendiente' CHECK (status IN ('pendiente', 'ok', 'error')),
    error TEXT,
    actor TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_qa_publications_bug ON qa_publications (bug_id);
CREATE INDEX IF NOT EXISTS idx_qa_publications_session ON qa_publications (session_id);
