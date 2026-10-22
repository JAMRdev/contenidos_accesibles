import { env, SELF } from "cloudflare:test";
import { beforeEach, describe, expect, it } from "vitest";
import { firmaValida } from "../src/worker/rutas/evidencias";
import { api, instalarClerk, ORIGEN, tokenDe, usuarioClerk } from "./clerk-simulado";

const body = (b: unknown) => ({ body: JSON.stringify(b) });
const PNG = new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0, 0, 0, 13, 73, 72, 68, 82, 1, 2, 3]);

type Evidencia = { id: number; tipo: string; nombre: string; bytes: number; datos_ocultos: number; url: string; etiqueta: string | null };

let resultadoId: number;

beforeEach(async () => {
    instalarClerk();
    usuarioClerk("user_admin", { github: "admin-fijo" });
    usuarioClerk("user_qa", { github: "tester-qa" });
    usuarioClerk("user_viewer", { github: "solo-mira" });
    await api("user_admin", "/api/miembros", { method: "POST", ...body({ github: "tester-qa", rol: "qa" }) });
    await api("user_admin", "/api/miembros", { method: "POST", ...body({ github: "solo-mira", rol: "viewer" }) });
    const s = await (await api("user_qa", "/api/sesiones", { method: "POST", ...body({ titulo: "R", entorno: "local", url: "http://127.0.0.1:8000" }) })).json<{ id: number }>();
    const r = await (await api("user_qa", `/api/sesiones/${s.id}/resultados/QA-4.3-01`, { method: "PUT", ...body({ estado: "fallo" }) })).json<{ id: number }>();
    resultadoId = r.id;
});

async function subir(sub: string, datos: BodyInit, contentType: string, query = `resultado=${resultadoId}`, nombre = "captura.png") {
    return SELF.fetch(`${ORIGEN}/api/evidencias?${query}`, {
        method: "POST",
        headers: { Authorization: `Bearer ${await tokenDe(sub)}`, "Content-Type": contentType, "X-Nombre": encodeURIComponent(nombre), "X-Etiqueta": "Pantalla" },
        body: datos,
    });
}

describe("subir archivos", () => {
    it("una captura PNG se guarda en R2 y se descarga igual", async () => {
        const res = await subir("user_qa", PNG, "image/png");
        expect(res.status).toBe(201);
        const ev = await res.json<Evidencia>();
        expect(ev).toMatchObject({ tipo: "imagen", nombre: "captura.png", bytes: PNG.byteLength, etiqueta: "Pantalla", url: `/evidencias/${ev.id}` });

        const archivo = await api("user_viewer", `/api/evidencias/${ev.id}/archivo`);
        expect(archivo.headers.get("Content-Type")).toBe("image/png");
        expect(archivo.headers.get("X-Content-Type-Options")).toBe("nosniff");
        expect(new Uint8Array(await archivo.arrayBuffer())).toEqual(PNG);

        const { evidencias } = await (await api("user_viewer", `/api/evidencias?resultado=${resultadoId}`)).json<{ evidencias: Evidencia[] }>();
        expect(evidencias.map((e) => e.id)).toEqual([ev.id]);
    });

    it("rechaza tipos no admitidos, archivos que no son lo que dicen y archivos muy grandes", async () => {
        expect((await subir("user_qa", "<script>", "text/html")).status).toBe(422);
        const falso = await subir("user_qa", new TextEncoder().encode("no soy un png"), "image/png");
        expect(falso.status).toBe(422);
        expect(await falso.json()).toEqual({ error: "El contenido del archivo no coincide con su tipo." });
        const grande = await SELF.fetch(`${ORIGEN}/api/evidencias?resultado=${resultadoId}`, {
            method: "POST",
            headers: { Authorization: `Bearer ${await tokenDe("user_qa")}`, "Content-Type": "image/png", "Content-Length": String(11 * 1024 * 1024) },
            body: PNG,
        });
        expect(grande.status).toBe(422);
        // No quedó nada en el bucket
        expect((await env.EVIDENCIAS.list()).objects).toHaveLength(0);
    });

    it("un archivo de texto subido se guarda con los datos sensibles ocultos", async () => {
        const log = "GET /api/v1/files\nAuthorization: Bearer abcdefghijklmnopqrstu\n200 OK";
        const ev = await (await subir("user_qa", log, "text/plain", `resultado=${resultadoId}`, "consola.log")).json<Evidencia>();
        expect(ev.datos_ocultos).toBe(1);
        const texto = await (await api("user_viewer", `/api/evidencias/${ev.id}/archivo`)).text();
        expect(texto).toBe("GET /api/v1/files\nAuthorization: [OCULTO]\n200 OK");
    });

    it("exige un destino que exista y el rol QA", async () => {
        expect((await subir("user_qa", PNG, "image/png", "")).status).toBe(422);
        expect((await subir("user_qa", PNG, "image/png", "resultado=999")).status).toBe(404);
        expect((await subir("user_qa", PNG, "image/png", `resultado=${resultadoId}&bug=1`)).status).toBe(422);
        expect((await subir("user_viewer", PNG, "image/png")).status).toBe(403);
    });
});

describe("texto pegado", () => {
    it("un curl pegado se guarda sin el token y un JSON se guarda como JSON", async () => {
        const curl = await api("user_qa", "/api/evidencias/texto", {
            method: "POST",
            ...body({ resultado_id: resultadoId, etiqueta: "curl", texto: "curl -H 'Authorization: Bearer eyJabcdefghijk.eyJabcdefghijk.firmafirmafirma' $API/health" }),
        });
        expect(curl.status).toBe(201);
        const ev = await curl.json<Evidencia>();
        expect(ev).toMatchObject({ tipo: "texto", nombre: "curl.txt", datos_ocultos: 1 });
        expect(await (await api("user_viewer", `/api/evidencias/${ev.id}/archivo`)).text()).not.toContain("eyJ");

        const bug = await (await api("user_qa", "/api/bugs", { method: "POST", ...body({ caso_id: "QA-4.3-01", titulo: "x" }) })).json<{ id: number }>();
        const json = await (await api("user_qa", "/api/evidencias/texto", { method: "POST", ...body({ bug_id: bug.id, etiqueta: "Response", texto: '{"detail":"x"}' }) })).json<Evidencia>();
        expect(json.nombre).toBe("response.json");
        const archivo = await api("user_viewer", `/api/evidencias/${json.id}/archivo`);
        expect(archivo.headers.get("Content-Type")).toBe("application/json; charset=utf-8");
    });
});

describe("administración", () => {
    it("solo un admin borra evidencias, y queda en la auditoría y en el uso del bucket", async () => {
        const ev = await (await subir("user_qa", PNG, "image/png")).json<Evidencia>();
        const uso = await (await api("user_admin", "/api/evidencias/uso")).json<{ total: { cantidad: number; bytes: number } }>();
        expect(uso.total).toEqual({ cantidad: 1, bytes: PNG.byteLength });
        expect((await api("user_qa", "/api/evidencias/uso")).status).toBe(403);

        expect((await api("user_qa", `/api/evidencias/${ev.id}`, { method: "DELETE" })).status).toBe(403);
        expect((await api("user_admin", `/api/evidencias/${ev.id}`, { method: "DELETE" })).status).toBe(200);
        expect((await env.EVIDENCIAS.list()).objects).toHaveLength(0);
        expect((await api("user_viewer", `/api/evidencias/${ev.id}`)).status).toBe(404);
        const { entradas } = await (await api("user_admin", "/api/auditoria")).json<{ entradas: { accion: string }[] }>();
        expect(entradas.some((e) => e.accion === "evidencia.borrar")).toBe(true);
    });

    it("un resultado con evidencias no vuelve a pendiente", async () => {
        await subir("user_qa", PNG, "image/png");
        const s = await env.DB.prepare("SELECT session_id FROM qa_results WHERE id = ?").bind(resultadoId).first<{ session_id: number }>();
        const res = await api("user_qa", `/api/sesiones/${s!.session_id}/resultados/QA-4.3-01`, { method: "DELETE" });
        expect(res.status).toBe(409);
    });
});

describe("firmas", () => {
    it("reconoce los formatos admitidos", () => {
        const txt = (s: string) => new TextEncoder().encode(s);
        expect(firmaValida("image/png", PNG)).toBe(true);
        expect(firmaValida("image/jpeg", new Uint8Array([0xff, 0xd8, 0xff, 0xe0]))).toBe(true);
        expect(firmaValida("image/gif", txt("GIF89a"))).toBe(true);
        expect(firmaValida("image/webp", txt("RIFF\0\0\0\0WEBPVP8 "))).toBe(true);
        expect(firmaValida("video/mp4", txt("\0\0\0\x20ftypisom"))).toBe(true);
        expect(firmaValida("video/webm", new Uint8Array([0x1a, 0x45, 0xdf, 0xa3]))).toBe(true);
        expect(firmaValida("image/png", txt("GIF89a"))).toBe(false);
    });
});
