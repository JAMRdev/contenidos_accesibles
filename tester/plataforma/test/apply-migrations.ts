// Aplica las migraciones al D1 simulado y vacía D1 y R2 antes de cada test (el almacenamiento
// se comparte entre los tests de un mismo archivo).
import { applyD1Migrations, env } from "cloudflare:test";
import { beforeEach } from "vitest";

await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);

// Hijas antes que padres (claves foráneas)
const TABLAS = [
    "qa_evidence", "qa_comments", "qa_bug_history", "qa_bugs", "qa_results", "qa_sessions",
    "qa_meta", "qa_members", "qa_users", "qa_audit",
];

beforeEach(async () => {
    await env.DB.batch(TABLAS.map((t) => env.DB.prepare(`DELETE FROM ${t}`)));
    const { objects } = await env.EVIDENCIAS.list();
    if (objects.length) await env.EVIDENCIAS.delete(objects.map((o) => o.key));
});
