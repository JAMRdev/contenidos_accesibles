import { env } from "cloudflare:test";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { buscarCaso } from "../src/worker/catalogo";
import { armarIssue, faltantes, marcaCaso, type DatosReporte } from "../src/worker/reporte";
import { api, githubSimulado, instalarClerk, llamadasAGithub, usuarioClerk } from "./clerk-simulado";

const body = (b: unknown) => ({ body: JSON.stringify(b) });

const REPORTE: DatosReporte = {
    id: 7,
    titulo: "Devuelve 500 con un texto válido",
    severidad: "alta",
    entorno: "produccion",
    url: "https://api.example.com/api/v1",
    version: "0.1.0",
    pasos: "Enviar el texto\nMirar la respuesta",
    esperado: "200 con word_count 7.",
    obtenido: '500\n{"detail": "Internal Server Error"}',
    notas: "",
    sesion_id: 3,
    creado_por: "tester-qa",
};

describe("plantilla del issue", () => {
    it("arma título, marca oculta, secciones, evidencias y etiquetas", () => {
        const caso = buscarCaso("QA-4.3-01")!;
        const issue = armarIssue(REPORTE, caso, [{ id: 12, etiqueta: "Response", nombre: "response.json", tipo: "texto" }], "https://qa.test");
        expect(issue.titulo).toBe("[QA - Issue] QA-4.3-01 · Devuelve 500 con un texto válido");
        expect(issue.etiquetas).toEqual(["[QA - Issue]", "bug", "testing", "backend"]);
        expect(issue.cuerpo.startsWith(marcaCaso("QA-4.3-01"))).toBe(true);
        expect(issue.cuerpo).toContain("**Entorno:** Producción · `https://api.example.com/api/v1` · backend v0.1.0");
        expect(issue.cuerpo).toContain("1. Enviar el texto\n2. Mirar la respuesta");
        expect(issue.cuerpo).toContain('```\n500\n{"detail": "Internal Server Error"}\n```');
        expect(issue.cuerpo).toContain("- [Response](https://qa.test/evidencias/12) (texto, response.json)");
        expect(issue.cuerpo).toContain("[plataforma de QA](https://qa.test/bugs/7) (sesión #3)");
        expect(issue.cuerpo).not.toContain("### Notas");
    });

    it("informa qué falta para publicar", () => {
        expect(faltantes(REPORTE)).toEqual([]);
        expect(faltantes({ ...REPORTE, pasos: " ", obtenido: "" })).toEqual(["pasos para reproducir", "resultado obtenido"]);
    });
});

describe("reporte de un bug", () => {
    beforeEach(async () => {
        instalarClerk();
        usuarioClerk("user_admin", { github: "admin-fijo" });
        usuarioClerk("user_qa", { github: "tester-qa" });
        await api("user_admin", "/api/miembros", { method: "POST", ...body({ github: "tester-qa", rol: "qa" }) });
    });

    async function bugDesdeSesion() {
        const s = await (await api("user_qa", "/api/sesiones", { method: "POST", ...body({ titulo: "R", entorno: "staging", url: "https://staging.example/api/v1", version: "0.2.0" }) })).json<{ id: number }>();
        await api("user_qa", `/api/sesiones/${s.id}/resultados/QA-4.3-01`, { method: "PUT", ...body({ estado: "fallo", notas: "Respondió 500" }) });
        const bug = await (await api("user_qa", "/api/bugs", { method: "POST", ...body({ caso_id: "QA-4.3-01", sesion_id: s.id, titulo: "Falla process-text" }) })).json<{ id: number; reporte: Record<string, string> }>();
        return { sesion: s.id, bug };
    }

    it("se precarga del catálogo, la sesión y las notas del resultado", async () => {
        const { bug } = await bugDesdeSesion();
        const caso = buscarCaso("QA-4.3-01")!;
        expect(bug.reporte).toEqual({ pasos: caso.pasos.join("\n"), esperado: caso.esperado, obtenido: "Respondió 500", url: "https://staging.example/api/v1", version: "0.2.0" });
    });

    it("se edita y la vista previa muestra el issue y lo que falta", async () => {
        const { bug } = await bugDesdeSesion();
        await api("user_qa", `/api/bugs/${bug.id}`, { method: "PATCH", ...body({ obtenido: "", version: "" }) });
        const previa = await (await api("user_qa", `/api/bugs/${bug.id}/vista-previa`)).json<{ titulo: string; cuerpo: string; faltan: string[]; repo: string }>();
        expect(previa.titulo).toBe("[QA - Issue] QA-4.3-01 · Falla process-text");
        expect(previa.faltan).toEqual(["resultado obtenido"]);
        expect(previa.repo).toBe("e18-innovalab/contenidos_accesibles");
        expect(previa.cuerpo).toContain("**Entorno:** Staging · `https://staging.example/api/v1`");
        expect(previa.cuerpo).not.toContain("backend v");

        await api("user_qa", `/api/bugs/${bug.id}`, { method: "PATCH", ...body({ obtenido: "500 Internal Server Error" }) });
        expect((await (await api("user_qa", `/api/bugs/${bug.id}/vista-previa`)).json<{ faltan: string[] }>()).faltan).toEqual([]);
    });

    it("la vista previa enlaza las evidencias del bug y del resultado", async () => {
        const { sesion, bug } = await bugDesdeSesion();
        const r = await env.DB.prepare("SELECT id FROM qa_results WHERE session_id = ?").bind(sesion).first<{ id: number }>();
        await api("user_qa", "/api/evidencias/texto", { method: "POST", ...body({ resultado_id: r!.id, etiqueta: "curl", texto: "curl $API/documents/process-text" }) });
        await api("user_qa", "/api/evidencias/texto", { method: "POST", ...body({ bug_id: bug.id, etiqueta: "Response", texto: '{"detail":"x"}' }) });
        const previa = await (await api("user_qa", `/api/bugs/${bug.id}/vista-previa`)).json<{ cuerpo: string }>();
        expect(previa.cuerpo).toMatch(/- \[curl\]\(https:\/\/qa\.test\/evidencias\/\d+\)/);
        expect(previa.cuerpo).toMatch(/- \[Response\]\(https:\/\/qa\.test\/evidencias\/\d+\)/);
    });
});

describe("duplicados", () => {
    beforeEach(async () => {
        instalarClerk();
        usuarioClerk("user_admin", { github: "admin-fijo" });
    });
    afterEach(() => {
        env.GITHUB_TOKEN = undefined;
    });

    const crear = async (caso: string) =>
        (await api("user_admin", "/api/bugs", { method: "POST", ...body({ caso_id: caso, titulo: "x" }) })).json<{ id: number }>();

    it("sin token, muestra los de la plataforma y avisa que no consulta GitHub", async () => {
        const viejo = await crear("QA-4.3-01");
        await crear("QA-4.5-07");
        const descartado = await crear("QA-4.3-01");
        await api("user_admin", `/api/bugs/${descartado.id}`, { method: "PATCH", ...body({ estado: "descartado" }) });
        const nuevo = await crear("QA-4.3-01");

        const d = await (await api("user_admin", `/api/bugs/${nuevo.id}/duplicados`)).json<{ plataforma: { id: number }[]; github: { disponible: boolean; issues: unknown[] } }>();
        expect(d.plataforma.map((b) => b.id)).toEqual([viejo.id]);
        expect(d.github).toEqual({ disponible: false, issues: [], error: null });
        expect(llamadasAGithub).toEqual([]);
    });

    it("con token, busca issues abiertos del caso en el repo", async () => {
        env.GITHUB_TOKEN = "ghp_prueba";
        githubSimulado(() => Response.json({ items: [{ number: 140, title: "[QA - Issue] QA-4.3-01 · Algo", html_url: "https://github.com/x/y/issues/140", state: "open" }] }));
        const bug = await crear("QA-4.3-01");
        const d = await (await api("user_admin", `/api/bugs/${bug.id}/duplicados`)).json<{ github: { disponible: boolean; issues: { numero: number }[] } }>();
        expect(d.github.disponible).toBe(true);
        expect(d.github.issues.map((i) => i.numero)).toEqual([140]);
        expect(decodeURIComponent(llamadasAGithub[0].ruta)).toContain('repo:e18-innovalab/contenidos_accesibles is:issue is:open "QA-4.3-01"');
    });

    it("si GitHub falla, lo informa sin romper la respuesta", async () => {
        env.GITHUB_TOKEN = "ghp_prueba";
        githubSimulado(() => Response.json({ message: "Bad credentials" }, { status: 401 }));
        const bug = await crear("QA-4.3-01");
        const res = await api("user_admin", `/api/bugs/${bug.id}/duplicados`);
        expect(res.status).toBe(200);
        expect((await res.json<{ github: { error: string } }>()).github.error).toBe("GitHub respondió 401: Bad credentials.");
    });
});
