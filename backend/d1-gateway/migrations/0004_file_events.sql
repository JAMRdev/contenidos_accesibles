-- Tabla file_events: eventos de archivos y enlaces para las métricas de /admin (Issue #98).
-- Igual que en SCHEMA (app/db/database.py).
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
