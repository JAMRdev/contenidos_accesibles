// Aplica las migraciones al D1 simulado antes de cada archivo de tests.
import { applyD1Migrations, env } from "cloudflare:test";

await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);
