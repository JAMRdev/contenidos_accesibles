-- Dueño de diagnósticos, propuestas y decisiones (Issue #61): id de usuario de Clerk,
-- test-web_<tipo>_<usuario> desde la web de pruebas o 'anon' mientras la API es anónima.
-- Igual que en SCHEMA (app/db/database.py). Lo previo queda a nombre de 'anon'.
ALTER TABLE analyses ADD COLUMN user_id TEXT NOT NULL DEFAULT 'anon';
ALTER TABLE adaptation_proposals ADD COLUMN user_id TEXT NOT NULL DEFAULT 'anon';
ALTER TABLE adaptation_decisions ADD COLUMN user_id TEXT NOT NULL DEFAULT 'anon';
