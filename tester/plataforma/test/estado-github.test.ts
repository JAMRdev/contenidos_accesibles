import { env } from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { api, instalarClerk, usuarioClerk } from "./clerk-simulado";
import { GithubFalso } from "./github-simulado";

const body = (b: unknown) => ({ body: JSON.stringify(b) });

let gh: GithubFalso;

beforeEach(async () => {
    instalarClerk();
    usuarioClerk("user_admin", { github: "admin-fijo" });
    usuarioClerk("user_qa", { github: "tester-qa" });
    await api("user_admin", "/api/miembros", { method: "POST", ...body({ github: "tester-qa", rol: "qa" }) });
    env.GITHUB_TOKEN = "ghp_prueba";
    gh = new GithubFalso();
});

afterEach(() => {
    env.GITHUB_TOKEN = "";
});

async function bugPublicado() {
    const s = await (await api("user_qa", "/api/sesiones", { method: "POST", ...body({ titulo: "R", entorno: "staging", url: "https://staging.example" }) })).json<{ id: number }>();
    await api("user_qa", `/api/sesiones/${s.id}/resultados/QA-4.3-01`, { method: "PUT", ...body({ estado: "fallo", notas: "500" }) });
    const bug = await (await api("user_qa", "/api/bugs", { method: "POST", ...body({ caso_id: "QA-4.3-01", sesion_id: s.id, titulo: "Falla" }) })).json<{ id: number }>();
    await api("user_qa", `/api/bugs/${bug.id}/publicar`, { method: "POST" });
    return bug.id;
}

const cambiar = (id: number, estado: string) => api("user_qa", `/api/bugs/${id}`, { method: "PATCH", ...body({ estado }) });
const sincronizar = (id: number) => api("user_qa", `/api/bugs/${id}/sincronizar`, { method: "POST" });
type Detalle = { estado: string; historial: { a: string; nota: string | null }[]; comentarios: unknown[] };
const detalle = async (id: number) => (await api("user_qa", `/api/bugs/${id}`)).json<Detalle>();

describe("estado de QA hacia GitHub", () => {
    it("reabrir un bug resuelto reabre el issue, comenta y la sincronización no lo pisa", async () => {
        const id = await bugPublicado();
        Object.assign(gh.issues[0], { state: "closed", stateReason: "COMPLETED" });
        await sincronizar(id);
        expect((await detalle(id)).estado).toBe("resuelto");

        expect((await cambiar(id, "reabierto")).status).toBe(200);
        expect(gh.issues[0].state).toBe("open");
        expect(gh.issues[0].comments.at(-1)?.body).toContain("**QA lo volvió a probar y sigue fallando**");
        expect((await detalle(id)).historial.at(-1)).toMatchObject({ a: "reabierto", nota: "Reabierto también en GitHub (#100)" });

        // La sincronización siguiente ve el issue abierto (reabierto): el estado se mantiene
        gh.issues[0].stateReason = "REOPENED";
        await sincronizar(id);
        const d = await detalle(id);
        expect(d.estado).toBe("reabierto");
        // El comentario que publicó la plataforma no vuelve como comentario de GitHub
        expect(d.comentarios).toHaveLength(0);
    });

    it("verificar comenta (y propone cerrar si el issue sigue abierto), sin cerrarlo", async () => {
        const id = await bugPublicado();
        expect((await cambiar(id, "verificado")).status).toBe(200);
        expect(gh.issues[0].state).toBe("open");
        expect(gh.issues[0].comments.at(-1)?.body).toContain("Propongo cerrar este issue");

        Object.assign(gh.issues[0], { state: "closed", stateReason: "COMPLETED" });
        await sincronizar(id);
        expect((await detalle(id)).estado).toBe("verificado");
    });

    it("descartar comenta proponiendo cerrarlo como no planificado, sin cerrarlo", async () => {
        const id = await bugPublicado();
        expect((await cambiar(id, "descartado")).status).toBe(200);
        expect(gh.issues[0].state).toBe("open");
        expect(gh.issues[0].comments.at(-1)?.body).toContain("cerrarlo como no planificado");
        // Con el tablero en Done, la sincronización no pisa lo que decidió QA
        gh.issues[0].tablero = "Done";
        await sincronizar(id);
        expect((await detalle(id)).estado).toBe("descartado");
    });

    it("si GitHub falla, el estado no cambia en la plataforma", async () => {
        const id = await bugPublicado();
        const repo = env.GITHUB_REPO;
        env.GITHUB_REPO = "otro/repo-inexistente";
        const res = await cambiar(id, "verificado");
        env.GITHUB_REPO = repo;
        expect(res.status).toBe(502);
        expect((await detalle(id)).estado).toBe("abierto");
    });
});
