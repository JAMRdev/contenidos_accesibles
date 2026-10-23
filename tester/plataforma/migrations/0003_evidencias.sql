-- Evidencias de resultados y bugs (Issue #128). El archivo vive en el R2 de QA (r2_key);
-- acá, sus metadatos. Sin borrado automático: un bug tiene que poder ver su evidencia meses después.
CREATE TABLE IF NOT EXISTS qa_evidence (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    result_id INTEGER REFERENCES qa_results (id) ON DELETE SET NULL,
    bug_id INTEGER REFERENCES qa_bugs (id) ON DELETE SET NULL,
    kind TEXT NOT NULL CHECK (kind IN ('imagen', 'video', 'texto')),
    label TEXT,
    filename TEXT NOT NULL,
    content_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    r2_key TEXT NOT NULL UNIQUE,
    -- Cuántos datos sensibles se ocultaron antes de guardar (solo texto)
    redactions INTEGER NOT NULL DEFAULT 0,
    uploaded_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    CHECK (result_id IS NOT NULL OR bug_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_qa_evidence_result ON qa_evidence (result_id);
CREATE INDEX IF NOT EXISTS idx_qa_evidence_bug ON qa_evidence (bug_id);
