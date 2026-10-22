import { env } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import { motivoTransicion } from "../src/worker/rutas/bugs";
import { api, instalarClerk, usuarioClerk } from "./clerk-simulado";

const body = (b: unknown) => ({ body: JSON.stringify(b) });

type Bug = { id: number; estado: string; severidad: string; entorno: string | null; caso: { id: string; seccion: string } };

beforeEach(async () => {
    instalarClerk();
    usuarioClerk("user_admin", { github: "admin-fijo" });
    usuarioClerk("user_qa", { github: "tester-qa" });
    usuarioClerk("user_viewer", { github: "solo-mira" });
    await api("user_admin", "/api/miembros", { method: "POST", ...body({ github: "tester-qa", rol: "qa" }) });
    await api("user_admin", "/api/miembros", { method: "POST", ...body({ github: "solo-mira", rol: "viewer" }) });
});

async function crearBug(datos: Record<string, unknown>) {
    const res = await api("user_qa", "/api/bugs", { method: "POST", ...body({ titulo: "Algo falla", ...datos }) });
    expect(res.status).toBe(201);
    return res.json<Bug>();
}

/** Simula un bug ya publicado en GitHub (lo hace #130). */
async function publicar(id: number, numero: number) {
    await env.DB.prepare("UPDATE qa_bugs SET status = 'abierto', issue_number = ?, issue_url = ? WHERE id = ?")
        .bind(numero, `https://github.com/e18-innovalab/contenidos_accesibles/issues/${numero}`, id)
        .run();
}

describe("crear y consultar bugs", () => {
    it("un bug nuevo es borrador, toma la severidad del caso y el entorno de la sesión", async () => {
        const s = await (await api("user_qa", "/api/sesiones", { method: "POST", ...body({ titulo: "R", entorno: "staging", url: "https://staging.example" }) })).json<{ id: number }>();
        const bug = await crearBug({ caso_id: "QA-4.5-07", sesion_id: s.id });
        expect(bug).toMatchObject({ estado: "borrador", severidad: "alta", entorno: "staging", caso: { id: "QA-4.5-07", seccion: "4.5" } });

        const detalle = await (await api("user_viewer", `/api/bugs/${bug.id}`)).json<{ historial: { a: string; origen: string }[] }>();
        expect(detalle.historial).toMatchObject([{ a: "borrador", origen: "qa" }]);
    });

    it("valida el caso y los permisos", async () => {
        expect((await api("user_qa", "/api/bugs", { method: "POST", ...body({ caso_id: "QA-0-00", titulo: "x" }) })).status).toBe(404);
        expect((await api("user_qa", "/api/bugs", { method: "POST", ...body({ caso_id: "QA-4.1-01", titulo: "" }) })).status).toBe(422);
        expect((await api("user_viewer", "/api/bugs", { method: "POST", ...body({ caso_id: "QA-4.1-01", titulo: "x" }) })).status).toBe(403);
    });

    it("filtra por estado, severidad, sección, caso y texto, y pagina", async () => {
        const a = await crearBug({ caso_id: "QA-4.3-01", titulo: "Métricas mal contadas" });
        await crearBug({ caso_id: "QA-4.5-07", titulo: "Datos personales sin 422" });
        await crearBug({ caso_id: "QA-4.16-02", titulo: "Acepta .exe", severidad: "baja" });
        await api("user_qa", `/api/bugs/${a.id}`, { method: "PATCH", ...body({ estado: "descartado" }) });

        const ver = async (q: string) => (await (await api("user_viewer", `/api/bugs?${q}`)).json<{ bugs: { caso: { id: string } }[]; siguiente: number | null }>());
        const ids = async (q: string) => (await ver(q)).bugs.map((b) => b.caso.id);
        expect(await ids("estado=descartado")).toEqual(["QA-4.3-01"]);
        expect(await ids("estado=borrador&severidad=alta")).toEqual(["QA-4.5-07"]);
        expect(await ids("severidad=baja")).toEqual(["QA-4.16-02"]);
        expect(await ids("seccion=4.16")).toEqual(["QA-4.16-02"]);
        expect(await ids("caso=QA-4.5-07")).toEqual(["QA-4.5-07"]);
        expect(await ids("q=exe")).toEqual(["QA-4.16-02"]);
        expect(await ids("autor=tester-qa")).toHaveLength(3);

        const p1 = await ver("limite=2");
        expect(p1.bugs).toHaveLength(2);
        expect((await ver(`limite=2&antes=${p1.siguiente}`)).bugs.map((b) => b.caso.id)).toEqual(["QA-4.3-01"]);
    });
});

describe("resumen", () => {
    it("cuenta los bugs por estado (todos los estados, aunque estén en cero)", async () => {
        const a = await crearBug({ caso_id: "QA-4.3-01" });
        await crearBug({ caso_id: "QA-4.3-02" });
        await api("user_qa", `/api/bugs/${a.id}`, { method: "PATCH", ...body({ estado: "descartado" }) });
        const { por_estado } = await (await api("user_viewer", "/api/bugs/resumen")).json<{ por_estado: Record<string, number> }>();
        expect(por_estado).toEqual({ borrador: 1, abierto: 0, en_curso: 0, resuelto: 0, verificado: 0, reabierto: 0, descartado: 1 });
    });
});

describe("estado de un bug", () => {
    it("sin issue solo pasa entre borrador y descartado", async () => {
        const bug = await crearBug({ caso_id: "QA-4.3-01" });
        const cambiar = (estado: string) => api("user_qa", `/api/bugs/${bug.id}`, { method: "PATCH", ...body({ estado }) });
        expect((await cambiar("abierto")).status).toBe(409);
        expect((await cambiar("resuelto")).status).toBe(409);
        expect((await cambiar("descartado")).status).toBe(200);
        expect((await cambiar("borrador")).status).toBe(200);
    });

    it("publicado, QA mueve el estado y queda en el historial y la auditoría", async () => {
        const bug = await crearBug({ caso_id: "QA-4.3-01" });
        await publicar(bug.id, 321);
        const cambiar = (estado: string) => api("user_qa", `/api/bugs/${bug.id}`, { method: "PATCH", ...body({ estado }) });
        expect((await cambiar("borrador")).status).toBe(409);
        expect(await (await cambiar("verificado")).json()).toMatchObject({ estado: "verificado", issue: { numero: 321 } });
        expect((await cambiar("reabierto")).status).toBe(200);

        const detalle = await (await api("user_viewer", `/api/bugs/${bug.id}`)).json<{ historial: { de: string | null; a: string; actor: string }[] }>();
        // publicar() de este test no deja historial (el de #130 sí): el primer cambio de QA sale de "abierto"
        expect(detalle.historial.map((h) => [h.de, h.a])).toEqual([[null, "borrador"], ["abierto", "verificado"], ["verificado", "reabierto"]]);
        const { entradas } = await (await api("user_admin", "/api/auditoria")).json<{ entradas: { accion: string }[] }>();
        expect(entradas.filter((e) => e.accion === "bug.estado")).toHaveLength(2);
    });

    it("editar título, descripción y severidad sin cambiar el estado", async () => {
        const bug = await crearBug({ caso_id: "QA-4.3-01" });
        const res = await api("user_qa", `/api/bugs/${bug.id}`, { method: "PATCH", ...body({ titulo: "Nuevo título", descripcion: "Pasos…", severidad: "baja" }) });
        expect(await res.json()).toMatchObject({ titulo: "Nuevo título", descripcion: "Pasos…", severidad: "baja", estado: "borrador" });
        expect((await api("user_qa", `/api/bugs/${bug.id}`, { method: "PATCH", ...body({}) })).status).toBe(422);
    });

    it("reglas de transición", () => {
        expect(motivoTransicion({ status: "borrador", issue_number: null }, "borrador")).toMatch(/ya está/);
        expect(motivoTransicion({ status: "abierto", issue_number: 1 }, "en_curso")).toBeNull();
        expect(motivoTransicion({ status: "resuelto", issue_number: 1 }, "borrador")).toMatch(/no vuelve a borrador/);
    });
});

describe("comentarios de un bug", () => {
    it("QA comenta y todos los ven", async () => {
        const bug = await crearBug({ caso_id: "QA-4.3-01" });
        await api("user_qa", `/api/bugs/${bug.id}/comentarios`, { method: "POST", ...body({ texto: "Interno" }) });
        await api("user_qa", `/api/bugs/${bug.id}/comentarios`, { method: "POST", ...body({ texto: "Para GitHub", en_github: true }) });
        const detalle = await (await api("user_viewer", `/api/bugs/${bug.id}`)).json<{ comentarios: { texto: string; en_github: boolean }[] }>();
        expect(detalle.comentarios.map((c) => [c.texto, c.en_github])).toEqual([["Interno", false], ["Para GitHub", true]]);
        expect((await api("user_qa", `/api/bugs/${bug.id}/comentarios`, { method: "POST", ...body({ texto: " " }) })).status).toBe(422);
    });
});
