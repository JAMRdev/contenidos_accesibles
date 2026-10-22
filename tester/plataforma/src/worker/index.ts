// Worker de la plataforma de QA (Issue #123).
//
// - GET /health      → estado de D1 y R2 (sin login, para monitoreo)
// - GET /api/config  → publishable key de Clerk para la web (sin login)
// - /api/*           → API de la plataforma; exige sesión de Clerk y rol (auth.ts)
// - el resto         → la web de QA; lo sirve Cloudflare desde dist/web sin pasar por acá
//
// El Cron Trigger (cada 10 minutos) va a sincronizar el estado de los bugs con GitHub (#134);
// por ahora solo registra cuándo corrió.
import { Hono } from "hono";
import { listarAuditoria } from "./auditoria";
import { requiereRol, requiereSesion, type AppEnv } from "./auth";
import { health } from "./health";
import { LAST_CRON_KEY, setMeta } from "./meta";
import { alcanza } from "./roles";
import { miembros } from "./rutas/miembros";

const app = new Hono<AppEnv>();

app.get("/health", health);

app.get("/api/config", (c) => c.json({ clerk_publishable_key: c.env.CLERK_PUBLISHABLE_KEY }));

// De acá en adelante, toda la API exige sesión
app.use("/api/*", requiereSesion);

app.get("/api/me", (c) => {
    const u = c.get("usuario");
    return c.json({
        github: u.github,
        nombre: u.nombre,
        avatar_url: u.avatarUrl,
        rol: u.rol,
        permisos: { escribir: alcanza(u.rol, "qa"), administrar: alcanza(u.rol, "admin") },
    });
});

app.route("/api/miembros", miembros);

app.get("/api/auditoria", requiereRol("admin"), async (c) => {
    const limite = Math.min(Math.max(Number(c.req.query("limite")) || 50, 1), 200);
    const antes = Number(c.req.query("antes")) || undefined;
    return c.json({ entradas: await listarAuditoria(c.env, limite, antes) });
});

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
} satisfies ExportedHandler<AppEnv["Bindings"]>;
