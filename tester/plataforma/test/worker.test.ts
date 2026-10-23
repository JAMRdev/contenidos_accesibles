import { createExecutionContext, createScheduledController, env, SELF, waitOnExecutionContext } from "cloudflare:test";
import { describe, expect, it } from "vitest";
import { catalogo } from "../src/worker/catalogo";
import worker from "../src/worker/index";
import { api, instalarClerk, usuarioClerk } from "./clerk-simulado";

describe("GET /health", () => {
    it("responde ok con D1 y R2 accesibles y sin Cron todavía", async () => {
        const res = await SELF.fetch("https://qa.test/health");
        expect(res.status).toBe(200);
        expect(res.headers.get("Cache-Control")).toBe("no-store");
        expect(await res.json()).toMatchObject({
            ok: true,
            servicio: "innovalab-qa",
            d1: "ok",
            r2: "ok",
            ultima_cron: null,
        });
    });

    it("informa cuántos casos tiene el catálogo empaquetado", async () => {
        const body = await (await SELF.fetch("https://qa.test/health")).json<{ casos: number }>();
        expect(body.casos).toBe(catalogo.casos.length);
        expect(body.casos).toBeGreaterThan(100);
    });

    it("muestra la última corrida del Cron", async () => {
        const scheduledTime = Date.UTC(2026, 9, 22, 12, 30);
        const ctx = createExecutionContext();
        await worker.scheduled(createScheduledController({ scheduledTime, cron: "*/10 * * * *" }), env, ctx);
        await waitOnExecutionContext(ctx);

        const body = await (await SELF.fetch("https://qa.test/health")).json<{ ultima_cron: string }>();
        expect(body.ultima_cron).toBe("2026-10-22T12:30:00.000Z");
    });
});

describe("rutas de la API", () => {
    it("una ruta desconocida de /api pide sesión y, con sesión, devuelve 404 en JSON", async () => {
        instalarClerk();
        usuarioClerk("user_admin", { github: "admin-fijo" });
        expect((await api(null, "/api/nada")).status).toBe(401);
        const res = await api("user_admin", "/api/nada");
        expect(res.status).toBe(404);
        expect(await res.json()).toEqual({ error: "No existe ese endpoint." });
    });
});

describe("web", () => {
    it("sirve la web de QA en / y en rutas de la web", async () => {
        for (const path of ["/", "/sesiones/123"]) {
            const res = await SELF.fetch(`https://qa.test${path}`);
            expect(res.status).toBe(200);
            expect(await res.text()).toContain('<div id="root">');
        }
    });
});
