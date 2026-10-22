import { env } from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { api, instalarClerk, usuarioClerk } from "./clerk-simulado";
import { GithubFalso } from "./github-simulado";

const body = (b: unknown) => ({ body: JSON.stringify(b) });
const REPO_ORG = env.GITHUB_REPO;

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
    env.GITHUB_TOKEN = undefined;
    env.GITHUB_REPO = REPO_ORG;
});

async function sesion(titulo = "Ronda") {
    return (await (await api("user_qa", "/api/sesiones", { method: "POST", ...body({ titulo, entorno: "staging", url: "https://staging.example/api/v1", version: "0.3.0" }) })).json<{ id: number }>()).id;
}

async function resultado(s: number, caso: string, estado: string, notas = "Respondió 500") {
    return api("user_qa", `/api/sesiones/${s}/resultados/${caso}`, { method: "PUT", ...body({ estado, notas }) });
}

async function bugCompleto(s: number, caso: string) {
    await resultado(s, caso, "fallo");
    return (await (await api("user_qa", "/api/bugs", { method: "POST", ...body({ caso_id: caso, sesion_id: s, titulo: `Falla ${caso}` }) })).json<{ id: number }>()).id;
}

const publicarBug = (id: number) => api("user_qa", `/api/bugs/${id}/publicar`, { method: "POST" });

type ResultadoBug = { accion: string; issue: { numero: number; url: string }; comentarios: number; avisos: string[]; bug: { estado: string; issue: { numero: number } | null } };

describe("publicar un bug", () => {
    it("crea el issue con título, etiquetas y marcas, sin asignar, y lo agrega al tablero", async () => {
        const id = await bugCompleto(await sesion(), "QA-4.3-01");
        const res = await publicarBug(id);
        expect(res.status).toBe(200);
        const r = await res.json<ResultadoBug>();
        expect(r).toMatchObject({ accion: "creado", issue: { numero: 100 }, avisos: [], bug: { estado: "abierto", issue: { numero: 100 } } });

        const [issue] = gh.issues;
        expect(issue.title).toBe("[QA - Issue] QA-4.3-01 · Falla QA-4.3-01");
        expect(issue.labels).toEqual(["[QA - Issue]", "bug", "testing", "backend"]);
        expect(issue.body).toContain("<!-- qa-case: QA-4.3-01 -->");
        expect(issue.body).toContain(`<!-- qa-bug: ${id} -->`);
        expect(gh.tablero.agregados).toEqual(["I_100"]);
        expect(gh.tablero.campos).toEqual([{ item: "ITEM_I_100", opcion: "O_FALLO" }]);

        const detalle = await (await api("user_qa", `/api/bugs/${id}`)).json<{ historial: { a: string; nota: string | null }[] }>();
        expect(detalle.historial.at(-1)).toMatchObject({ a: "abierto", nota: "Publicado en GitHub como #100" });
    });

    it("publicar de nuevo no duplica: actualiza el issue con las evidencias nuevas", async () => {
        const id = await bugCompleto(await sesion(), "QA-4.3-01");
        await publicarBug(id);
        expect(gh.issues[0].body).toContain("_Este bug no tiene evidencias adjuntas en la plataforma de QA._");
        await api("user_qa", "/api/evidencias/texto", { method: "POST", ...body({ bug_id: id, etiqueta: "Response", texto: '{"detail":"x"}' }) });
        const r = await (await publicarBug(id)).json<ResultadoBug>();
        expect(r.accion).toBe("actualizado");
        expect(gh.issues).toHaveLength(1);
        expect(gh.issues[0].body).toContain("**1 evidencia adjunta**");
    });

    it("con un repo personal no toca el tablero de la organización", async () => {
        env.GITHUB_REPO = "JAMRdev/qa-sandbox";
        gh = new GithubFalso("JAMRdev/qa-sandbox");
        await publicarBug(await bugCompleto(await sesion(), "QA-4.3-01"));
        expect(gh.issues).toHaveLength(1);
        expect(gh.tablero.agregados).toEqual([]);
    });

    it("un segundo bug del mismo caso comenta en el issue abierto y queda como duplicado", async () => {
        const s = await sesion();
        const primero = await bugCompleto(s, "QA-4.3-01");
        await publicarBug(primero);
        const segundo = await bugCompleto(await sesion("Otra ronda"), "QA-4.3-01");
        const r = await (await publicarBug(segundo)).json<ResultadoBug>();
        expect(r).toMatchObject({ accion: "duplicado", issue: { numero: 100 }, bug: { estado: "descartado", issue: null } });
        expect(gh.issues).toHaveLength(1);
        expect(gh.issues[0].comments[0].body).toContain(`**Se volvió a reproducir este bug** en la plataforma de QA (bug #${segundo})`);
    });

    it("un issue abierto con la marca del caso (sin bug en la plataforma) se vincula y se comenta", async () => {
        gh.issues.push({ number: 77, title: "Bug viejo", body: "<!-- qa-case: QA-4.3-01 -->\nAlgo", labels: ["[QA - Issue]"], state: "open", comments: [] });
        const id = await bugCompleto(await sesion(), "QA-4.3-01");
        const r = await (await publicarBug(id)).json<ResultadoBug>();
        expect(r).toMatchObject({ accion: "vinculado", issue: { numero: 77 }, bug: { estado: "abierto", issue: { numero: 77 } } });
        expect(gh.issues).toHaveLength(1);
        expect(gh.issues[0].comments).toHaveLength(1);
        // Un issue ajeno (vinculado) no se reescribe al publicar de nuevo
        expect((await (await publicarBug(id)).json<ResultadoBug>()).accion).toBe("ya_publicado");
        expect(gh.issues[0].body).toBe("<!-- qa-case: QA-4.3-01 -->\nAlgo");
    });

    it("envía los comentarios marcados para GitHub una sola vez", async () => {
        const id = await bugCompleto(await sesion(), "QA-4.3-01");
        await api("user_qa", `/api/bugs/${id}/comentarios`, { method: "POST", ...body({ texto: "Pasa también con PDF", en_github: true }) });
        await api("user_qa", `/api/bugs/${id}/comentarios`, { method: "POST", ...body({ texto: "Nota interna" }) });
        expect((await (await publicarBug(id)).json<ResultadoBug>()).comentarios).toBe(1);
        expect(gh.issues[0].comments.map((c) => c.body)).toEqual([expect.stringContaining("> Pasa también con PDF")]);

        await api("user_qa", `/api/bugs/${id}/comentarios`, { method: "POST", ...body({ texto: "Otra para GitHub", en_github: true }) });
        expect((await (await publicarBug(id)).json<ResultadoBug>()).comentarios).toBe(1);
        expect(gh.issues[0].comments).toHaveLength(2);
        const detalle = await (await api("user_qa", `/api/bugs/${id}`)).json<{ comentarios: { texto: string; publicado_en_github: boolean }[] }>();
        expect(detalle.comentarios.map((c) => [c.texto, c.publicado_en_github])).toEqual([["Pasa también con PDF", true], ["Nota interna", false], ["Otra para GitHub", true]]);
    });

    it("valida GitHub configurado, reporte completo, estado y rol", async () => {
        const s = await sesion();
        const id = await bugCompleto(s, "QA-4.3-01");
        env.GITHUB_TOKEN = undefined;
        expect((await publicarBug(id)).status).toBe(409);
        env.GITHUB_TOKEN = "ghp_prueba";

        await api("user_qa", `/api/bugs/${id}`, { method: "PATCH", ...body({ obtenido: "" }) });
        const incompleto = await publicarBug(id);
        expect(incompleto.status).toBe(422);
        expect(await incompleto.json()).toEqual({ error: "Para publicar falta completar: resultado obtenido." });

        usuarioClerk("user_viewer", { github: "solo-mira" });
        await api("user_admin", "/api/miembros", { method: "POST", ...body({ github: "solo-mira", rol: "viewer" }) });
        expect((await api("user_viewer", `/api/bugs/${id}/publicar`, { method: "POST" })).status).toBe(403);
        expect(gh.issues).toHaveLength(0);
    });

    it("si GitHub falla al crear, responde 502 y el reintento publica", async () => {
        const id = await bugCompleto(await sesion(), "QA-4.3-01");
        gh.fallarAlCrear = 502;
        const fallo = await publicarBug(id);
        expect(fallo.status).toBe(502);
        expect((await fallo.json<{ error: string }>()).error).toContain("podés reintentar");
        const pub = await env.DB.prepare("SELECT status FROM qa_publications WHERE key = ?").bind(`bug:${id}:issue`).first<{ status: string }>();
        expect(pub?.status).toBe("error");

        expect((await (await publicarBug(id)).json<ResultadoBug>()).accion).toBe("creado");
        expect(gh.issues).toHaveLength(1);
    });

    it("si un intento anterior creó el issue sin guardarlo, el reintento lo recupera por la marca", async () => {
        const id = await bugCompleto(await sesion(), "QA-4.3-01");
        gh.issues.push({ number: 55, title: "x", body: `<!-- qa-case: QA-9.9-99 --><!-- qa-bug: ${id} -->`, labels: [], state: "open", comments: [] });
        await env.DB.prepare("INSERT INTO qa_publications (key, kind, bug_id, status, actor, updated_at) VALUES (?, 'issue', ?, 'error', 'x', '2026-01-01T00:00:00Z')")
            .bind(`bug:${id}:issue`, id)
            .run();
        const r = await (await publicarBug(id)).json<ResultadoBug>();
        expect(r.issue.numero).toBe(55);
        expect(gh.issues).toHaveLength(1);
    });
});

describe("publicar una sesión", () => {
    it("el plan dice qué se va a publicar", async () => {
        const s = await sesion();
        await bugCompleto(s, "QA-4.3-01");
        const incompleto = await bugCompleto(s, "QA-4.3-02");
        await api("user_qa", `/api/bugs/${incompleto}`, { method: "PATCH", ...body({ pasos: "" }) });
        const plan = await (await api("user_qa", `/api/sesiones/${s}/publicacion`)).json<{ bugs: { listo: boolean; faltan: string[] }[]; ejecucion: { accion: string } }>();
        expect(plan.bugs.map((b) => b.listo)).toEqual([true, false]);
        expect(plan.bugs[1].faltan).toEqual(["pasos para reproducir"]);
        expect(plan.ejecucion.accion).toBe("crear");
        expect(gh.issues).toHaveLength(0);
    });

    it("publica los bugs listos, omite los incompletos y crea el issue de ejecución; publicar de nuevo lo actualiza", async () => {
        const s = await sesion("Ronda 1");
        const listo = await bugCompleto(s, "QA-4.3-01");
        const incompleto = await bugCompleto(s, "QA-4.3-02");
        await api("user_qa", `/api/bugs/${incompleto}`, { method: "PATCH", ...body({ pasos: "" }) });
        await resultado(s, "QA-4.1-02", "paso");

        const r = await (await api("user_qa", `/api/sesiones/${s}/publicar`, { method: "POST" })).json<{
            ejecucion: { numero: number };
            bugs: { bug_id: number; accion: string }[];
            omitidos: { id: number }[];
            errores: string[];
        }>();
        expect(r.errores).toEqual([]);
        expect(r.bugs).toEqual([expect.objectContaining({ bug_id: listo, accion: "creado" })]);
        expect(r.omitidos.map((o) => o.id)).toEqual([incompleto]);
        const ejecucion = gh.issues.find((i) => i.number === r.ejecucion.numero)!;
        expect(ejecucion.title).toMatch(/^\[QA - Issue\] Ejecución \d{4}-\d{2}-\d{2} · Staging · Ronda 1$/);
        expect(ejecucion.labels).toEqual(["[QA - Issue]", "testing"]);
        expect(ejecucion.body).toContain(`<!-- qa-session: ${s} -->`);
        expect(ejecucion.body).toContain("| `QA-4.1-02` |");
        expect(ejecucion.body).toContain("| ✅ Pasó |");
        expect(ejecucion.body).toMatch(/\| `QA-4\.3-01` \| .* \| ❌ Falló \| #100 \|/);

        // Completar el incompleto y publicar de nuevo: no crea otra ejecución, la actualiza
        await api("user_qa", `/api/bugs/${incompleto}`, { method: "PATCH", ...body({ pasos: "Enviar el texto" }) });
        const r2 = await (await api("user_qa", `/api/sesiones/${s}/publicar`, { method: "POST" })).json<{ ejecucion: { numero: number }; bugs: { accion: string }[] }>();
        expect(r2.ejecucion.numero).toBe(r.ejecucion.numero);
        expect(r2.bugs.map((b) => b.accion)).toEqual(["creado", "actualizado"]);
        expect(gh.issues.filter((i) => i.title.includes("Ejecución"))).toHaveLength(1);
        expect(gh.issues).toHaveLength(3);
    });

    it("si un caso con bug abierto pasa en otra sesión, propone cerrarlo una sola vez", async () => {
        const s1 = await sesion("Ronda 1");
        await bugCompleto(s1, "QA-4.3-01");
        await api("user_qa", `/api/sesiones/${s1}/publicar`, { method: "POST" });

        const s2 = await sesion("Ronda 2");
        await resultado(s2, "QA-4.3-01", "paso", "");
        const r = await (await api("user_qa", `/api/sesiones/${s2}/publicar`, { method: "POST" })).json<{ propuestas_cierre: number[] }>();
        expect(r.propuestas_cierre).toEqual([100]);
        const bug = gh.issues.find((i) => i.number === 100)!;
        expect(bug.state).toBe("open");
        expect(bug.comments.at(-1)?.body).toContain("Propongo cerrar este issue");

        await api("user_qa", `/api/sesiones/${s2}/publicar`, { method: "POST" });
        expect(bug.comments.filter((c) => c.body.includes("Propongo cerrar"))).toHaveLength(1);
    });
});
