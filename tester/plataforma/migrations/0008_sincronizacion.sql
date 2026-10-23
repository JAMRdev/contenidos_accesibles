-- Sincronización desde GitHub (Issue #134): un comentario de GitHub se importa una sola vez.
CREATE UNIQUE INDEX IF NOT EXISTS idx_qa_comments_github ON qa_comments (github_comment_id) WHERE github_comment_id IS NOT NULL;
