-- Tabla files: metadatos de los archivos de los docentes en R2 (Issue #62).
-- Igual que en SCHEMA (app/db/database.py).
-- Archivos de los docentes en R2 (Issue #62). Solo metadatos: el archivo vive en el bucket
-- (o en la carpeta local en desarrollo). 'pending' hasta que el navegador sube y se confirma.
CREATE TABLE IF NOT EXISTS files (
    id                      TEXT PRIMARY KEY,
    user_id                 TEXT NOT NULL,
    object_key              TEXT NOT NULL UNIQUE,
    -- 'upload' (subido por el docente) o 'export' (generado por /export)
    kind                    TEXT NOT NULL DEFAULT 'upload' CHECK (kind IN ('upload', 'export')),
    filename                TEXT NOT NULL,
    content_type            TEXT NOT NULL,
    size_bytes              INTEGER NOT NULL CHECK (size_bytes > 0),
    status                  TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'uploaded')),
    created_at              TEXT NOT NULL,
    uploaded_at             TEXT,
    expires_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_files_user_created
    ON files(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_files_expires_at
    ON files(expires_at);
