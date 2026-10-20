-- Tabla app_settings: configuración editable desde /admin (Issue #64), p. ej. la retención de archivos.
-- Igual que en SCHEMA (app/db/database.py).
-- Sin fila, vale el valor por defecto de la variable de entorno.
CREATE TABLE IF NOT EXISTS app_settings (
    key                     TEXT PRIMARY KEY,
    value                   TEXT NOT NULL,
    updated_at              TEXT NOT NULL,
    updated_by              TEXT
);
