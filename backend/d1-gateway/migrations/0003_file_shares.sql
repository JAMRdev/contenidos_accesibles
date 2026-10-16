-- Tabla file_shares: enlaces revocables para compartir archivos de R2 (Issue #65).
-- Igual que en SCHEMA (app/db/database.py).
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
