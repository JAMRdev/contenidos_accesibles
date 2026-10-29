-- Uso del texto a voz (Issue #121): las llamadas de TTS van a ai_usage_events con kind = 'tts',
-- y además guardan los caracteres enviados y los segundos de audio generados.
-- Igual que en SCHEMA (app/db/database.py). Las filas previas quedan en 0.
ALTER TABLE ai_usage_events ADD COLUMN characters INTEGER NOT NULL DEFAULT 0;
ALTER TABLE ai_usage_events ADD COLUMN audio_seconds REAL NOT NULL DEFAULT 0;
