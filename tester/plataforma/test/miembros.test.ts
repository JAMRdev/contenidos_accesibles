import { beforeEach, describe, expect, it } from "vitest";
import { api, instalarClerk, usuarioClerk } from "./clerk-simulado";

const json = (body: unknown) => ({ body: JSON.stringify(body) });

beforeEach(() => {
    instalarClerk();
    usuarioClerk("user_admin", { github: "admin-fijo" });
    usuarioClerk("user_qa", { github: "tester-qa" });
    usuarioClerk("user_viewer", { github: "solo-mira" });
});

async function alta(github: string, rol: string) {
    return api("user_admin", "/api/miembros", { method: "POST", ...json({ github, rol }) });
}

describe("miembros", () => {
    it("un admin agrega miembros y ellos entran con su rol", async () => {
        expect((await alta("@tester-qa", "qa")).status).toBe(201);
        const res = await alta("solo-mira", "viewer");
        expect(await res.json()).toMatchObject({
            fijos: [{ github: "admin-fijo", rol: "admin" }],
            miembros: [
                { github: "tester-qa", rol: "qa", habilitado: true, agregado_por: "admin-fijo" },
                { github: "solo-mira", rol: "viewer", habilitado: true },
            ],
        });

        const qa = await (await api("user_qa", "/api/me")).json();
        expect(qa).toMatchObject({ rol: "qa", permisos: { escribir: true, administrar: false } });
        const viewer = await (await api("user_viewer", "/api/me")).json();
        expect(viewer).toMatchObject({ rol: "viewer", permisos: { escribir: false, administrar: false } });
    });

    it("qa y viewer no administran miembros ni ven la auditoría", async () => {
        await alta("tester-qa", "qa");
        await alta("solo-mira", "viewer");
        for (const sub of ["user_qa", "user_viewer"]) {
            expect((await api(sub, "/api/miembros")).status).toBe(403);
            expect((await api(sub, "/api/auditoria")).status).toBe(403);
            const res = await api(sub, "/api/miembros", { method: "POST", ...json({ github: "otro", rol: "qa" }) });
            expect(res.status).toBe(403);
        }
    });

    it("valida el usuario, el rol y los duplicados", async () => {
        expect((await alta("no válido!", "qa")).status).toBe(422);
        expect((await alta("alguien", "superadmin")).status).toBe(422);
        expect((await alta("alguien", "qa")).status).toBe(201);
        expect((await alta("ALGUIEN", "viewer")).status).toBe(409);
        expect((await alta("admin-fijo", "qa")).status).toBe(409);
    });

    it("cambiar el rol o suspender aplica en el próximo request", async () => {
        await alta("tester-qa", "qa");
        const cambio = await api("user_admin", "/api/miembros/tester-qa", { method: "PATCH", ...json({ rol: "viewer" }) });
        expect(cambio.status).toBe(200);
        expect(await (await api("user_qa", "/api/me")).json()).toMatchObject({ rol: "viewer" });

        await api("user_admin", "/api/miembros/tester-qa", { method: "PATCH", ...json({ habilitado: false }) });
        expect((await api("user_qa", "/api/me")).status).toBe(403);
    });

    it("nadie cambia su propio acceso ni el de un admin fijo", async () => {
        await alta("otro-admin", "admin");
        usuarioClerk("user_otro_admin", { github: "otro-admin" });
        const propio = await api("user_otro_admin", "/api/miembros/otro-admin", { method: "PATCH", ...json({ rol: "qa" }) });
        expect(propio.status).toBe(409);
        const fijo = await api("user_otro_admin", "/api/miembros/admin-fijo", { method: "PATCH", ...json({ rol: "qa" }) });
        expect(fijo.status).toBe(409);
        expect((await api("user_otro_admin", "/api/miembros/otro-admin", { method: "DELETE" })).status).toBe(409);
    });

    it("quitar el acceso y la auditoría de cada cambio", async () => {
        await alta("tester-qa", "qa");
        await api("user_admin", "/api/miembros/tester-qa", { method: "PATCH", ...json({ rol: "viewer" }) });
        expect((await api("user_admin", "/api/miembros/tester-qa", { method: "DELETE" })).status).toBe(200);
        expect((await api("user_admin", "/api/miembros/tester-qa", { method: "DELETE" })).status).toBe(404);
        expect((await api("user_qa", "/api/me")).status).toBe(403);

        const { entradas } = await (await api("user_admin", "/api/auditoria")).json<{ entradas: { actor: string; accion: string; detalle: unknown }[] }>();
        const cambios = entradas.filter((e) => e.accion.startsWith("miembro."));
        expect(cambios.map((e) => [e.accion, e.actor, e.detalle])).toEqual([
            ["miembro.quitar", "admin-fijo", { github: "tester-qa" }],
            ["miembro.cambiar", "admin-fijo", { github: "tester-qa", rol: "viewer" }],
            ["miembro.agregar", "admin-fijo", { github: "tester-qa", rol: "qa" }],
        ]);
    });
});
