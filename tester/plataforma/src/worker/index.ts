// Worker de la plataforma de QA (Issue #123).
//
// - GET /health → estado de D1 y R2 (sin login, para monitoreo)
// - /api/*      → API de la plataforma (llega con #126 y #127)
// - el resto    → la web de QA; lo sirve Cloudflare desde dist/web sin pasar por acá
//
// El Cron Trigger (cada 10 minutos) va a sincronizar el estado de los bugs con GitHub (#134);
// por ahora solo registra cuándo corrió.
import { Hono } from "hono";
import type { Env } from "./env";
import { health } from "./health";
import { LAST_CRON_KEY, setMeta } from "./meta";

const app = new Hono<{ Bindings: Env }>();

app.get("/health", health);

// Fuera de /api, lo que llega acá es de la web (Cloudflare suele servirlo antes, desde los assets)
app.notFound((c) =>
    c.req.path.startsWith("/api/") ? c.json({ error: "No existe ese endpoint." }, 404) : c.env.ASSETS.fetch(c.req.raw),
);

app.onError((err, c) => {
    console.error(err);
    return c.json({ error: "Error interno de la plataforma de QA." }, 500);
});

export default {
    fetch: app.fetch,
    async scheduled(controller, env, ctx) {
        ctx.waitUntil(setMeta(env, LAST_CRON_KEY, new Date(controller.scheduledTime).toISOString()));
    },
} satisfies ExportedHandler<Env>;
