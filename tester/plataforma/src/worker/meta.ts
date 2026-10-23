// Tabla qa_meta: pares clave → valor de la plataforma.
import type { Env } from "./env";

export const LAST_CRON_KEY = "ultima_cron";

export async function getMeta(env: Env, key: string): Promise<string | null> {
    return env.DB.prepare("SELECT value FROM qa_meta WHERE key = ?").bind(key).first<string>("value");
}

export async function setMeta(env: Env, key: string, value: string): Promise<void> {
    await env.DB.prepare(
        `INSERT INTO qa_meta (key, value) VALUES (?, ?)
         ON CONFLICT (key) DO UPDATE SET value = excluded.value,
             updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')`,
    )
        .bind(key, value)
        .run();
}
