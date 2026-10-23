import { createExecutionContext, createScheduledController, env, waitOnExecutionContext } from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import worker from "../src/worker/index";
import { estadoSegunGithub } from "../src/worker/sincronizacion";
import { api, instalarClerk, usuarioClerk } from "./clerk-simulado";
import { GithubFalso } from "./github-simulado";

const body = (b: unknown) => ({ body: JSON.stringify(b) });

let gh: GithubFalso;

beforeEach(async () => {
    instalarClerk();
    usuarioClerk("user_admin", { github: "admin-fijo" });
    usuarioClerk("user_qa", { github: "tester-qa" });
    usuarioClerk("user_viewer", { github: "solo-mira" });
    await api("user_admin", "/api/miembros", { method: "POST", ...body({ github: "tester-qa", rol: "qa" }) });
    await api("user_admin", "/api/miembros", { method: "POST", ...body({ github: "solo-mira", rol: "viewer" }) });
    env.GITHUB_TOKEN = "ghp_prueba";
    gh = new GithubFalso();
});

afterEach(() => {
    env.GITHUB_TOKEN = "";
});

/** Un bug publicado como issue #100. */
async function bugPublicado() {
    const s = await (await api("user_qa", "/api/sesiones", { method: "POST", ...body({ titulo: "R", entorno: "staging", url: "https://staging.example" }) })).json<{ id: number }>();
    await api("user_qa", `/api/sesiones/${s.id}/resultados/QA-4.3-01`, { method: "PUT", ...body({ estado: "fallo", notas: "500" }) });
    const bug = await (await api("user_qa", "/api/bugs", { method: "POST", ...body({ caso_id: "QA-4.3-01", sesion_id: s.id, titulo: "Falla" }) })).json<{ id: number }>();
    await api("user_qa", `/api/bugs/${bug.id}/publicar`, { method: "POST" });
    return bug.id;
}

type Detalle = { estado: string; issue: { estado: string; motivo: string | null; tablero: string | null }; historial: { a: string; origen: string; nota: string | null }[]; comentarios: { autor: string; texto: string; origen: string }[] };
const detalle = async (id: number) => (await api("user_viewer", `/api/bugs/${id}`)).json<Detalle>();
const sincronizarBug = (id: number) => api("user_viewer", `/api/bugs/${id}/sincronizar`, { method: "POST" });

describe("estado según GitHub", () => {
    it("reglas", () => {
        const abierto = { estado: "open" as const, motivo: null, tablero: null };
        expect(estadoSegunGithub("abierto", "open", { estado: "closed", motivo: "completed", tablero: null })?.estado).toBe("resuelto");
        expect(estadoSegunGithub("verificado", "open", { estado: "closed", motivo: "completed", tablero: null })).toBeNull();
        expect(estadoSegunGithub("abierto", "open", { estado: "closed", motivo: "not_planned", tablero: null })?.estado).toBe("descartado");
        expect(estadoSegunGithub("resuelto", "closed", abierto)?.estado).toBe("reabierto");
        expect(estadoSegunGithub("resuelto", "open", { ...abierto, motivo: "reopened" })?.estado).toBe("reabierto");
        expect(estadoSegunGithub("abierto", "open", { ...abierto, tablero: "In progress" })?.estado).toBe("en_curso");
        expect(estadoSegunGithub("verificado", "open", { ...abierto, tablero: "In progress" })).toBeNull();
        expect(estadoSegunGithub("abierto", "open", abierto)).toBeNull();
    });
});

describe("sincronizar un bug", () => {
    it("trae los comentarios nuevos de GitHub una sola vez y no reimporta los de la plataforma", async () => {
        const id = await bugPublicado();
        await api("user_qa", `/api/bugs/${id}/comentarios`, { method: "POST", ...body({ texto: "Desde la plataforma", en_github: true }) });
        await api("user_qa", `/api/bugs/${id}/publicar`, { method: "POST" });
        gh.issues[0].comments.push({ id: 7001, body: "Lo miro mañana", autor: "malessan4" });

        expect(await (await sincronizarBug(id)).json()).toMatchObject({ bugs: 1, comentarios: 1, errores: [] });
        await sincronizarBug(id);
        const d = await detalle(id);
        expect(d.comentarios.map((c) => [c.autor, c.texto, c.origen])).toEqual([
            ["tester-qa", "Desde la plataforma", "plataforma"],
            ["malessan4", "Lo miro mañana", "github"],
        ]);
    });

    it("cerrar el issue como completado lo pasa a resuelto y queda en el historial", async () => {
        const id = await bugPublicado();
        Object.assign(gh.issues[0], { state: "closed", stateReason: "COMPLETED" });
        const r = await (await sincronizarBug(id)).json<{ cambios: { de: string; a: string }[] }>();
        expect(r.cambios).toEqual([expect.objectContaining({ bug: id, de: "abierto", a: "resuelto" })]);
        const d = await detalle(id);
        expect(d).toMatchObject({ estado: "resuelto", issue: { estado: "closed", motivo: "completed" } });
        expect(d.historial.at(-1)).toMatchObject({ a: "resuelto", origen: "github", nota: "Cerrado en GitHub como completado" });

        // Reabrirlo lo pasa a reabierto
        Object.assign(gh.issues[0], { state: "open", stateReason: "REOPENED" });
        await sincronizarBug(id);
        expect((await detalle(id)).estado).toBe("reabierto");
    });

    it("el Status del tablero y el cierre como no planificado", async () => {
        const id = await bugPublicado();
        gh.issues[0].tablero = "In progress";
        await sincronizarBug(id);
        expect(await detalle(id)).toMatchObject({ estado: "en_curso", issue: { tablero: "In progress" } });
        Object.assign(gh.issues[0], { state: "closed", stateReason: "NOT_PLANNED" });
        await sincronizarBug(id);
        expect((await detalle(id)).estado).toBe("descartado");
    });

    it("un bug sin publicar no se sincroniza", async () => {
        const bug = await (await api("user_qa", "/api/bugs", { method: "POST", ...body({ caso_id: "QA-4.3-01", titulo: "x" }) })).json<{ id: number }>();
        expect((await sincronizarBug(bug.id)).status).toBe(409);
    });
});

describe("sincronizar todo", () => {
    it("el botón sincroniza todos los bugs publicados (solo QA)", async () => {
        const id = await bugPublicado();
        Object.assign(gh.issues[0], { state: "closed", stateReason: "COMPLETED" });
        expect((await api("user_viewer", "/api/sincronizar", { method: "POST" })).status).toBe(403);
        const r = await (await api("user_qa", "/api/sincronizar", { method: "POST" })).json<{ bugs: number; cambios: unknown[] }>();
        expect(r).toMatchObject({ bugs: 1 });
        expect(r.cambios).toHaveLength(1);
        expect((await detalle(id)).estado).toBe("resuelto");
        const health = await (await api(null, "/health")).json<{ ultima_sincronizacion: string | null }>();
        expect(health.ultima_sincronizacion).not.toBeNull();
    });

    it("el Cron sincroniza con origen 'sincronizacion'", async () => {
        const id = await bugPublicado();
        Object.assign(gh.issues[0], { state: "closed", stateReason: "COMPLETED" });
        const ctx = createExecutionContext();
        await worker.scheduled(createScheduledController({ scheduledTime: Date.now(), cron: "*/10 * * * *" }), env, ctx);
        await waitOnExecutionContext(ctx);
        expect((await detalle(id)).historial.at(-1)).toMatchObject({ a: "resuelto", origen: "sincronizacion" });
    });

    it("sin token no rompe: lo informa", async () => {
        await bugPublicado();
        env.GITHUB_TOKEN = "";
        const r = await (await api("user_qa", "/api/sincronizar", { method: "POST" })).json<{ errores: string[] }>();
        expect(r.errores).toEqual(["GitHub no está configurado (falta GITHUB_TOKEN)."]);
    });
});
