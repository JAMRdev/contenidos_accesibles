// GET /health: sin login, para monitoreo (sección Estado de /admin del backend).
// Devuelve { ok: true } solo si responden D1 y R2; si no, 503 con el detalle.
import type { Context } from "hono";
import type { Env } from "./env";
import { getMeta, LAST_CRON_KEY } from "./meta";

type Probe = { ok: true; value: string | null } | { ok: false; error: string };

async function probe(fn: () => Promise<string | null>): Promise<Probe> {
    try {
        return { ok: true, value: await fn() };
    } catch (e) {
        // Solo el tipo de error: el mensaje puede traer detalles internos de Cloudflare
        return { ok: false, error: e instanceof Error ? e.name : "Error" };
    }
}

export async function health(c: Context<{ Bindings: Env }>) {
    const [d1, r2] = await Promise.all([
        probe(() => getMeta(c.env, LAST_CRON_KEY)),
        // HEAD de una clave que no existe: confirma acceso al bucket sin leer evidencias
        probe(async () => ((await c.env.EVIDENCIAS.head("health")) ? "existe" : null)),
    ]);
    const ok = d1.ok && r2.ok;
    c.header("Cache-Control", "no-store");
    return c.json(
        {
            ok,
            servicio: "innovalab-qa",
            version: c.env.CF_VERSION?.id?.slice(0, 8) ?? "local",
            d1: d1.ok ? "ok" : d1.error,
            r2: r2.ok ? "ok" : r2.error,
            ultima_cron: d1.ok ? d1.value : null,
        },
        ok ? 200 : 503,
    );
}
