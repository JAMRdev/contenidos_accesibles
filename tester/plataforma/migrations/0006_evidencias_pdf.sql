-- Evidencias en PDF (Issue #128). SQLite no permite cambiar un CHECK: se reconstruye la tabla
-- conservando los datos.
CREATE TABLE qa_evidence_nueva (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id INTEGER REFERENCES qa_results (id) ON DELETE SET NULL,
    bug_id INTEGER REFERENCES qa_bugs (id) ON DELETE SET NULL,
    kind TEXT NOT NULL CHECK (kind IN ('imagen', 'video', 'texto', 'pdf')),
    label TEXT,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    r2_key TEXT NOT NULL UNIQUE,
    redactions INTEGER NOT NULL DEFAULT 0,
    uploaded_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (result_id IS NOT NULL OR bug_id IS NOT NULL)
);
INSERT INTO qa_evidence_nueva SELECT * FROM qa_evidence;
DROP TABLE qa_evidence;
ALTER TABLE qa_evidence_nueva RENAME TO qa_evidence;
CREATE INDEX IF NOT EXISTS idx_qa_evidence_result ON qa_evidence (result_id);
CREATE INDEX IF NOT EXISTS idx_qa_evidence_bug ON qa_evidence (bug_id);
