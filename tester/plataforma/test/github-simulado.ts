// GitHub simulado en memoria para los tests de publicación: issues, comentarios, búsqueda y el
// GraphQL del tablero. Se instala con githubSimulado() de clerk-simulado.ts.
import { githubSimulado } from "./clerk-simulado";

export type IssueSimulado = {
    number: number;
    title: string;
    body: string;
    labels: string[];
    state: "open" | "closed";
    comments: { id: number; body: string; autor?: string }[];
    // Para la sincronización (#134)
    stateReason?: "COMPLETED" | "NOT_PLANNED" | "DUPLICATE" | "REOPENED" | null;
    tablero?: string | null;
};

export class GithubFalso {
    issues: IssueSimulado[] = [];
    tablero: { agregados: string[]; campos: { item: string; opcion: string }[] } = { agregados: [], campos: [] };
    // Si se define, la próxima creación de issue falla con este código
    fallarAlCrear: number | null = null;
    private siguiente = 100;
    private siguienteComentario = 9000;

    constructor(readonly repo = "e18-innovalab/contenidos_accesibles") {
        githubSimulado((url, init) => this.responder(url, init));
    }

    private issueJson(i: IssueSimulado) {
        return { number: i.number, title: i.title, body: i.body, state: i.state, html_url: `https://github.com/${this.repo}/issues/${i.number}`, node_id: `I_${i.number}` };
    }

    private responder(url: URL, init?: RequestInit): Response {
        const metodo = init?.method ?? "GET";
        const body = init?.body ? JSON.parse(String(init.body)) : null;
        const base = `/repos/${this.repo}/issues`;

        if (url.pathname === "/search/issues") {
            const q = url.searchParams.get("q") ?? "";
            const textos = [...q.matchAll(/"([^"]+)"/g)].map((m) => m[1]);
            const soloAbiertos = q.includes("is:open");
            const items = this.issues.filter((i) => (!soloAbiertos || i.state === "open") && textos.every((t) => i.body.includes(t) || i.title.includes(t)));
            return Response.json({ items: items.map((i) => this.issueJson(i)) });
        }
        if (url.pathname === base && metodo === "POST") {
            if (this.fallarAlCrear) {
                const codigo = this.fallarAlCrear;
                this.fallarAlCrear = null;
                return Response.json({ message: "Server Error" }, { status: codigo });
            }
            const issue: IssueSimulado = { number: this.siguiente++, title: body.title, body: body.body, labels: body.labels ?? [], state: "open", comments: [] };
            this.issues.push(issue);
            return Response.json(this.issueJson(issue), { status: 201 });
        }
        const comentarios = url.pathname.match(new RegExp(`^${base}/(\\d+)/comments$`));
        if (comentarios && metodo === "POST") {
            const issue = this.issues.find((i) => i.number === Number(comentarios[1]));
            if (!issue) return Response.json({ message: "Not Found" }, { status: 404 });
            const comentario = { id: this.siguienteComentario++, body: body.body };
            issue.comments.push(comentario);
            return Response.json({ id: comentario.id, html_url: `https://github.com/${this.repo}/issues/${issue.number}#issuecomment-${comentario.id}` }, { status: 201 });
        }
        const edicion = url.pathname.match(new RegExp(`^${base}/(\\d+)$`));
        if (edicion && metodo === "PATCH") {
            const issue = this.issues.find((i) => i.number === Number(edicion[1]));
            if (!issue) return Response.json({ message: "Not Found" }, { status: 404 });
            Object.assign(issue, { title: body.title ?? issue.title, body: body.body ?? issue.body });
            return Response.json(this.issueJson(issue));
        }
        if (url.pathname === "/graphql") {
            const query: string = body.query;
            if (query.includes("repository(")) {
                const repository: Record<string, unknown> = {};
                for (const [, n] of query.matchAll(/i(\d+): issue\(number: \d+\)/g)) {
                    const issue = this.issues.find((i) => i.number === Number(n));
                    repository[`i${n}`] = issue
                        ? {
                              number: issue.number,
                              state: issue.state.toUpperCase(),
                              stateReason: issue.stateReason ?? null,
                              comments: { nodes: issue.comments.map((c) => ({ databaseId: c.id, body: c.body, createdAt: "2026-10-22T20:00:00Z", author: { login: c.autor ?? "JAMRdev" } })) },
                              projectItems: { nodes: issue.tablero ? [{ project: { number: 3 }, fieldValueByName: { name: issue.tablero } }] : [] },
                          }
                        : null;
                }
                return Response.json({ data: { repository } });
            }
            if (query.includes("organization(")) {
                return Response.json({ data: { organization: { projectV2: { id: "P_3", field: { id: "F_QA", options: [{ id: "O_FALLO", name: "Falló" }] } } } } });
            }
            if (query.includes("addProjectV2ItemById")) {
                this.tablero.agregados.push(body.variables.contenido);
                return Response.json({ data: { addProjectV2ItemById: { item: { id: `ITEM_${body.variables.contenido}` } } } });
            }
            if (query.includes("updateProjectV2ItemFieldValue")) {
                this.tablero.campos.push({ item: body.variables.item, opcion: body.variables.opcion });
                return Response.json({ data: { updateProjectV2ItemFieldValue: { projectV2Item: { id: body.variables.item } } } });
            }
        }
        return Response.json({ message: `No simulado: ${metodo} ${url.pathname}` }, { status: 500 });
    }
}
