import { beforeEach, describe, expect, it } from "vitest";
import { catalogo } from "../src/worker/catalogo";
import { api, instalarClerk, usuarioClerk } from "./clerk-simulado";

const body = (b: unknown) => ({ body: JSON.stringify(b) });
const NO_DESTRUCTIVOS = catalogo.casos.filter((c) => !c.destructivo).length;
const DESTRUCTIVO = catalogo.casos.find((c) => c.destructivo)!.id;

beforeEach(async () => {
    instalarClerk();
    usuarioClerk("user_admin", { github: "admin-fijo" });
    usuarioClerk("user_qa", { github: "tester-qa" });
    usuarioClerk("user_viewer", { github: "solo-mira" });
    await api("user_admin", "/api/miembros", { method: "POST", ...body({ github: "tester-qa", rol: "qa" }) });
    await api("user_admin", "/api/miembros", { method: "POST", ...body({ github: "solo-mira", rol: "viewer" }) });
});

type Sesion = { id: number; estado: string; avance: Record<string, number> };

async function crearSesion(datos: Record<string, unknown> = {}) {
    const res = await api("user_qa", "/api/sesiones", {
        method: "POST",
        ...body({ titulo: "Ronda 22/10", entorno: "produccion", url: "https://api.example.com/api/v1", version: "0.1.0", ...datos }),
    });
    expect(res.status).toBe(201);
    return res.json<Sesion>();
}

const resultado = (sesion: number, caso: string, estado: string, notas?: string) =>
    api("user_qa", `/api/sesiones/${sesion}/resultados/${caso}`, { method: "PUT", ...body({ estado, notas }) });

describe("ronda completa por la API", () => {
    it("crear sesión → registrar resultados → cerrar", async () => {
        const s = await crearSesion();
        expect(s.avance).toEqual({ total: NO_DESTRUCTIVOS, pendiente: NO_DESTRUCTIVOS, paso: 0, fallo: 0, bloqueado: 0, omitido: 0 });

        expect((await resultado(s.id, "QA-4.1-02", "paso")).status).toBe(200);
        expect((await resultado(s.id, "QA-4.3-01", "fallo", "Devolvió 500")).status).toBe(200);
        // Cambiar un resultado ya registrado lo actualiza (no duplica)
        expect(await (await resultado(s.id, "QA-4.3-01", "bloqueado", "Sin entorno")).json()).toMatchObject({ estado: "bloqueado", notas: "Sin entorno" });

        const detalle = await (await api("user_viewer", `/api/sesiones/${s.id}`)).json<{ avance: Record<string, number>; casos: unknown[] }>();
        expect(detalle.avance).toMatchObject({ pendiente: NO_DESTRUCTIVOS - 2, paso: 1, bloqueado: 1, fallo: 0 });
        expect(detalle.casos).toHaveLength(catalogo.casos.length);

        expect((await api("user_qa", `/api/sesiones/${s.id}/cerrar`, { method: "POST" })).status).toBe(200);
        expect((await resultado(s.id, "QA-4.1-01", "paso")).status).toBe(409);
        expect((await api("user_qa", `/api/sesiones/${s.id}/reabrir`, { method: "POST" })).status).toBe(403);
        expect((await api("user_admin", `/api/sesiones/${s.id}/reabrir`, { method: "POST" })).status).toBe(200);
        expect((await resultado(s.id, "QA-4.1-01", "paso")).status).toBe(200);
    });

    it("filtra los casos de la sesión por resultado, sección y marcas", async () => {
        const s = await crearSesion();
        await resultado(s.id, "QA-4.3-01", "fallo");
        await resultado(s.id, "QA-4.3-02", "paso");
        const ver = async (q: string) =>
            (await (await api("user_qa", `/api/sesiones/${s.id}?${q}`)).json<{ casos: { caso: { id: string } }[] }>()).casos.map((c) => c.caso.id);

        expect(await ver("resultado=fallo")).toEqual(["QA-4.3-01"]);
        expect(await ver("seccion=4.3&resultado=pendiente")).toEqual(["QA-4.3-03", "QA-4.3-04"]);
        expect((await ver("ia_real=true")).every((id) => catalogo.casos.find((c) => c.id === id)!.ia_real)).toBe(true);
        expect(await ver("q=health")).toContain("QA-4.1-02");
    });

    it("valida el caso, el entorno y los permisos", async () => {
        const s = await crearSesion();
        expect((await resultado(s.id, "QA-99.9-01", "paso")).status).toBe(404);
        expect((await resultado(s.id, DESTRUCTIVO, "paso")).status).toBe(409);
        expect((await resultado(s.id, "QA-4.1-01", "aprobado")).status).toBe(422);

        const local = await crearSesion({ entorno: "local", url: "http://127.0.0.1:8000/api/v1" });
        expect(local.avance.total).toBe(catalogo.casos.length);
        expect((await resultado(local.id, DESTRUCTIVO, "paso")).status).toBe(200);

        const viewer = await api("user_viewer", "/api/sesiones", { method: "POST", ...body({ titulo: "x", entorno: "local", url: "http://x" }) });
        expect(viewer.status).toBe(403);
        const mala = await api("user_qa", "/api/sesiones", { method: "POST", ...body({ titulo: "x", entorno: "marte", url: "ftp://x" }) });
        expect(mala.status).toBe(422);
    });

    it("volver un resultado a pendiente", async () => {
        const s = await crearSesion();
        await resultado(s.id, "QA-4.1-02", "paso");
        expect((await api("user_qa", `/api/sesiones/${s.id}/resultados/QA-4.1-02`, { method: "DELETE" })).status).toBe(200);
        expect((await api("user_qa", `/api/sesiones/${s.id}/resultados/QA-4.1-02`, { method: "DELETE" })).status).toBe(404);
    });
});

describe("listado de sesiones", () => {
    it("filtra por estado y entorno, y pagina", async () => {
        const a = await crearSesion({ titulo: "Producción A" });
        const b = await crearSesion({ titulo: "Local B", entorno: "local", url: "http://127.0.0.1:8000" });
        await crearSesion({ titulo: "Producción C" });
        await api("user_qa", `/api/sesiones/${a.id}/cerrar`, { method: "POST" });
        await resultado(b.id, "QA-4.1-02", "paso");

        const lista = async (q: string) => (await api("user_viewer", `/api/sesiones?${q}`)).json<{ sesiones: { titulo: string; avance: Record<string, number> }[]; siguiente: number | null }>();
        expect((await lista("estado=cerrada")).sesiones.map((s) => s.titulo)).toEqual(["Producción A"]);
        const locales = await lista("entorno=local");
        expect(locales.sesiones.map((s) => s.titulo)).toEqual(["Local B"]);
        expect(locales.sesiones[0].avance.paso).toBe(1);

        const p1 = await lista("limite=2");
        expect(p1.sesiones.map((s) => s.titulo)).toEqual(["Producción C", "Local B"]);
        const p2 = await lista(`limite=2&antes=${p1.siguiente}`);
        expect(p2.sesiones.map((s) => s.titulo)).toEqual(["Producción A"]);
        expect(p2.siguiente).toBeNull();
    });
});

describe("historial de un caso y comentarios de resultados", () => {
    it("muestra el caso en todas las sesiones", async () => {
        const s1 = await crearSesion({ titulo: "Ronda 1", version: "0.1.0" });
        const s2 = await crearSesion({ titulo: "Ronda 2", version: "0.2.0" });
        await resultado(s1.id, "QA-4.3-01", "fallo");
        await resultado(s2.id, "QA-4.3-01", "paso");
        const res = await (await api("user_viewer", "/api/casos/QA-4.3-01")).json<{ caso: { id: string }; historial: { estado: string; sesion: { titulo: string } }[] }>();
        expect(res.caso.id).toBe("QA-4.3-01");
        expect(res.historial.map((h) => [h.sesion.titulo, h.estado]).sort()).toEqual([["Ronda 1", "fallo"], ["Ronda 2", "paso"]]);
        expect((await api("user_viewer", "/api/casos/QA-0-00")).status).toBe(404);
    });

    it("comentar un resultado", async () => {
        const s = await crearSesion();
        const r = await (await resultado(s.id, "QA-4.3-01", "fallo")).json<{ id: number }>();
        const res = await api("user_qa", `/api/resultados/${r.id}/comentarios`, { method: "POST", ...body({ texto: "Pasa solo con mock", en_github: true }) });
        expect(res.status).toBe(201);
        const { comentarios } = await (await api("user_viewer", `/api/resultados/${r.id}/comentarios`)).json<{ comentarios: unknown[] }>();
        expect(comentarios).toMatchObject([{ autor: "tester-qa", texto: "Pasa solo con mock", en_github: true, origen: "plataforma" }]);
        expect((await api("user_viewer", `/api/resultados/${r.id}/comentarios`, { method: "POST", ...body({ texto: "x" }) })).status).toBe(403);
    });
});

describe("catálogo", () => {
    it("lista y filtra los casos", async () => {
        const todos = await (await api("user_viewer", "/api/casos")).json<{ total: number; secciones: unknown[] }>();
        expect(todos.total).toBe(catalogo.casos.length);
        const destructivos = await (await api("user_viewer", "/api/casos?destructivo=true")).json<{ casos: { destructivo: boolean }[] }>();
        expect(destructivos.casos.length).toBeGreaterThan(0);
        expect(destructivos.casos.every((c) => c.destructivo)).toBe(true);
    });
});
