import { env } from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { catalogo } from "../src/worker/catalogo";
import { api, instalarClerk, usuarioClerk } from "./clerk-simulado";
import { GithubFalso } from "./github-simulado";

const body = (b: unknown) => ({ body: JSON.stringify(b) });

const CASO_FE = {
    area: "frontend",
    seccion: "Login de docentes",
    titulo: "Entrar con Google",
    ubicacion: "/login",
    pasos: ["Abrir /login", "Elegir Google"],
    esperado: "Entra y ve el inicio.",
    severidad: "alta",
    etiquetas: ["login"],
};

type Caso = { id: string; area: string; manual: boolean; activo: boolean; seccion: string; titulo: string; endpoint?: string; pasos: string[] };

beforeEach(async () => {
    instalarClerk();
    usuarioClerk("user_admin", { github: "admin-fijo" });
    usuarioClerk("user_qa", { github: "tester-qa" });
    usuarioClerk("user_viewer", { github: "solo-mira" });
    await api("user_admin", "/api/miembros", { method: "POST", ...body({ github: "tester-qa", rol: "qa" }) });
    await api("user_admin", "/api/miembros", { method: "POST", ...body({ github: "solo-mira", rol: "viewer" }) });
});

afterEach(() => {
    env.GITHUB_TOKEN = "";
});

async function crear(datos: Record<string, unknown> = CASO_FE) {
    const res = await api("user_qa", "/api/casos", { method: "POST", ...body(datos) });
    expect(res.status).toBe(201);
    return res.json<Caso>();
}

describe("casos manuales", () => {
    it("se crean con id por área que no se reutiliza", async () => {
        const a = await crear();
        const b = await crear({ ...CASO_FE, titulo: "Entrar con Microsoft" });
        const be = await crear({ ...CASO_FE, area: "backend", seccion: "Extras", titulo: "Algo del backend" });
        expect([a.id, b.id, be.id]).toEqual(["QA-FE-001", "QA-FE-002", "QA-BE-001"]);
        expect(a).toMatchObject({ area: "frontend", manual: true, activo: true, seccion: "Login de docentes", endpoint: "/login", pasos: ["Abrir /login", "Elegir Google"] });

        await api("user_qa", `/api/casos/${b.id}/archivar`, { method: "POST" });
        expect((await crear({ ...CASO_FE, titulo: "Otro" })).id).toBe("QA-FE-003");
    });

    it("se listan por área y los archivados no aparecen salvo que se pidan", async () => {
        const a = await crear();
        await crear({ ...CASO_FE, seccion: "Subir material", titulo: "Arrastrar un PDF" });
        await api("user_qa", `/api/casos/${a.id}/archivar`, { method: "POST" });

        const fe = await (await api("user_viewer", "/api/casos?area=frontend")).json<{ casos: Caso[] }>();
        expect(fe.casos.map((c) => c.titulo)).toEqual(["Arrastrar un PDF"]);
        const conArchivados = await (await api("user_viewer", "/api/casos?area=frontend&archivados=true")).json<{ casos: Caso[] }>();
        expect(conArchivados.casos).toHaveLength(2);
        const be = await (await api("user_viewer", "/api/casos?area=backend")).json<{ total: number }>();
        expect(be.total).toBe(catalogo.casos.length);

        const { secciones } = await (await api("user_viewer", "/api/casos/secciones?area=frontend")).json<{ secciones: { seccion: string; casos: number }[] }>();
        expect(secciones).toEqual([{ seccion: "Subir material", titulo: "Subir material", casos: 1 }]);
    });

    it("se editan; los del repo no", async () => {
        const a = await crear();
        const res = await api("user_qa", `/api/casos/${a.id}`, { method: "PATCH", ...body({ titulo: "Entrar con Google (OAuth)", ubicacion: "" }) });
        expect(await res.json()).toMatchObject({ titulo: "Entrar con Google (OAuth)", seccion: "Login de docentes" });
        expect((await api("user_qa", `/api/casos/${a.id}`)).status).toBe(200);
        const repo = await api("user_qa", "/api/casos/QA-4.3-01", { method: "PATCH", ...body({ titulo: "x" }) });
        expect(repo.status).toBe(409);
        expect((await api("user_qa", "/api/casos/QA-4.3-01/archivar", { method: "POST" })).status).toBe(409);
    });

    it("valida los datos y el rol", async () => {
        expect((await api("user_qa", "/api/casos", { method: "POST", ...body({ ...CASO_FE, pasos: [] }) })).status).toBe(422);
        expect((await api("user_qa", "/api/casos", { method: "POST", ...body({ ...CASO_FE, area: "mobile" }) })).status).toBe(422);
        expect((await api("user_qa", "/api/casos", { method: "POST", ...body({ ...CASO_FE, mock: true, ia_real: true }) })).status).toBe(422);
        expect((await api("user_viewer", "/api/casos", { method: "POST", ...body(CASO_FE) })).status).toBe(403);
    });
});

describe("sesiones y bugs del frontend", () => {
    it("una sesión de frontend lista solo sus casos y no acepta casos de otra área", async () => {
        const a = await crear();
        const s = await (await api("user_qa", "/api/sesiones", { method: "POST", ...body({ titulo: "Front", area: "frontend", entorno: "staging", url: "https://staging.vercel.app" }) })).json<{ id: number; area: string; avance: { total: number } }>();
        expect(s).toMatchObject({ area: "frontend", avance: { total: 1 } });

        const detalle = await (await api("user_qa", `/api/sesiones/${s.id}`)).json<{ casos: { caso: { id: string } }[] }>();
        expect(detalle.casos.map((c) => c.caso.id)).toEqual([a.id]);
        expect((await api("user_qa", `/api/sesiones/${s.id}/resultados/QA-4.3-01`, { method: "PUT", ...body({ estado: "paso" }) })).status).toBe(409);
        expect((await api("user_qa", `/api/sesiones/${s.id}/resultados/${a.id}`, { method: "PUT", ...body({ estado: "fallo", notas: "No redirige" }) })).status).toBe(200);

        // Archivado después de tener resultado: sigue apareciendo en esa sesión
        await api("user_qa", `/api/casos/${a.id}/archivar`, { method: "POST" });
        const despues = await (await api("user_qa", `/api/sesiones/${s.id}`)).json<{ casos: { caso: { id: string } }[] }>();
        expect(despues.casos.map((c) => c.caso.id)).toEqual([a.id]);
    });

    it("el bug de un caso de frontend se publica con la etiqueta frontend", async () => {
        env.GITHUB_TOKEN = "ghp_prueba";
        const gh = new GithubFalso();
        const a = await crear();
        const s = await (await api("user_qa", "/api/sesiones", { method: "POST", ...body({ titulo: "Front", area: "frontend", entorno: "staging", url: "https://staging.vercel.app" }) })).json<{ id: number }>();
        await api("user_qa", `/api/sesiones/${s.id}/resultados/${a.id}`, { method: "PUT", ...body({ estado: "fallo", notas: "No redirige" }) });
        const bug = await (await api("user_qa", "/api/bugs", { method: "POST", ...body({ caso_id: a.id, sesion_id: s.id, titulo: "Google no redirige" }) })).json<{ id: number; area: string; reporte: { pasos: string } }>();
        expect(bug).toMatchObject({ area: "frontend", reporte: { pasos: "Abrir /login\nElegir Google" } });

        await api("user_qa", `/api/bugs/${bug.id}/publicar`, { method: "POST" });
        expect(gh.issues[0].title).toBe("[QA - Issue] QA-FE-001 · Google no redirige");
        expect(gh.issues[0].labels).toEqual(["[QA - Issue]", "bug", "testing", "frontend"]);

        const filtrados = await (await api("user_qa", "/api/bugs?area=frontend")).json<{ bugs: unknown[] }>();
        expect(filtrados.bugs).toHaveLength(1);
        expect((await (await api("user_qa", "/api/bugs?area=backend")).json<{ bugs: unknown[] }>()).bugs).toHaveLength(0);
    });
});
