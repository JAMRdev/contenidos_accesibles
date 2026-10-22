-- Datos sueltos de la plataforma (Issue #124). Por ahora: cuándo corrió el Cron por última vez,
-- para ver en /health que el Trigger está andando.
CREATE TABLE IF NOT EXISTS qa_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
