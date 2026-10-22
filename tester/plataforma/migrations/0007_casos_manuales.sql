-- Casos manuales y áreas: la plataforma también se usa para el frontend, que no tiene casos en el
-- repo. Los casos del repo (tester/casos) son del área backend; los manuales se crean desde la web.

-- Casos creados desde la plataforma. El id (QA-FE-001, QA-BE-001…) no se reutiliza nunca: un caso
-- que ya no se usa se archiva (active = 0), así sus resultados y bugs siguen apuntando a algo.
CREATE TABLE IF NOT EXISTS qa_cases (
    id TEXT PRIMARY KEY,
    area TEXT NOT NULL CHECK (area IN ('backend', 'frontend')),
    -- Caso de uso o sección que agrupa casos (por ejemplo "Login de docentes")
    section TEXT NOT NULL,
    title TEXT NOT NULL,
    -- Endpoint o pantalla
    location TEXT,
    preconditions TEXT NOT NULL DEFAULT '[]',
    steps TEXT NOT NULL,
    test_data TEXT,
    expected TEXT NOT NULL,
    severity TEXT NOT NULL CHECK (severity IN ('alta', 'media', 'baja')),
    tags TEXT NOT NULL DEFAULT '[]',
    mock INTEGER NOT NULL DEFAULT 0,
    ia_real INTEGER NOT NULL DEFAULT 0,
    destructive INTEGER NOT NULL DEFAULT 0,
    active INTEGER NOT NULL DEFAULT 1,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_qa_cases_area ON qa_cases (area);

-- Cada sesión prueba un área; cada bug hereda el área de su caso (etiqueta del issue)
ALTER TABLE qa_sessions ADD COLUMN area TEXT NOT NULL DEFAULT 'backend';
ALTER TABLE qa_bugs ADD COLUMN area TEXT NOT NULL DEFAULT 'backend';
