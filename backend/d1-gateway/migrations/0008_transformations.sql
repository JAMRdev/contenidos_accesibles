-- Transformaciones de material (Issue #116): material sin imágenes, mapa mental, infografía o audio.
-- Igual que en SCHEMA (app/db/database.py). Guarda el resultado estructurado y la clave en R2 del
-- archivo generado, nunca el texto del material. Vencen con su diagnóstico o archivo de origen.
CREATE TABLE IF NOT EXISTS transformations (
    id                      TEXT PRIMARY KEY,
    user_id                 TEXT NOT NULL,
    format                  TEXT NOT NULL,
    status                  TEXT NOT NULL,
    review_status           TEXT NOT NULL DEFAULT 'pendiente',
    object_key              TEXT,
    payload_json            TEXT NOT NULL,
    created_at              TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    expires_at              TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_transformations_user_created
    ON transformations(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_transformations_expires_at
    ON transformations(expires_at);
