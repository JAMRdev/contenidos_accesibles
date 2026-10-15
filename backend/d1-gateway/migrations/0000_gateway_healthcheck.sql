-- Migración inicial (Issue #58): tabla mínima para comprobar que el backend lee y
-- escribe en D1 a través del Worker. El esquema real del backend llega en #59.
CREATE TABLE IF NOT EXISTS gateway_healthcheck (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    source      TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
