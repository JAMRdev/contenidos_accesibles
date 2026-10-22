// Aplica las migraciones al D1 simulado y lo vacía antes de cada test (el almacenamiento
// se comparte entre los tests de un mismo archivo).
import { applyD1Migrations, env } from "cloudflare:test";
import { beforeEach } from "vitest";

await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);

const TABLAS = ["qa_meta", "qa_members", "qa_users", "qa_audit"];

beforeEach(async () => {
    await env.DB.batch(TABLAS.map((t) => env.DB.prepare(`DELETE FROM ${t}`)));
});
