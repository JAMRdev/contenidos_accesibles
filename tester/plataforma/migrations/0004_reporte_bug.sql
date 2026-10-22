-- Formulario de reporte de bug (Issue #129): los campos del reporte que se publica en GitHub.
-- description pasa a ser "notas adicionales".
ALTER TABLE qa_bugs ADD COLUMN steps TEXT NOT NULL DEFAULT '';
ALTER TABLE qa_bugs ADD COLUMN expected TEXT NOT NULL DEFAULT '';
ALTER TABLE qa_bugs ADD COLUMN actual TEXT NOT NULL DEFAULT '';
ALTER TABLE qa_bugs ADD COLUMN tested_url TEXT;
ALTER TABLE qa_bugs ADD COLUMN backend_version TEXT;
