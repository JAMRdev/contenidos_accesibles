// Sincronización desde GitHub (Issue #134): trae el estado, el Status del tablero y los comentarios
// de los issues de los bugs publicados, con una consulta GraphQL por cada tanda de hasta 50 bugs.
// La corren el Cron Trigger (cada 10 minutos), el botón "Sincronizar ahora" y la página de un bug.
//
// Estado del bug según GitHub:
//   - cerrado como completado                → resuelto (si no estaba resuelto o verificado)
//   - cerrado como no planificado o duplicado → descartado
//   - abierto después de haber estado cerrado (o marcado como reabierto en GitHub) → reabierto
//   - abierto con Status "In progress" o "In review" en el tablero → en_curso (si estaba abierto o reabierto)
//   - abierto con Status "Done"              → resuelto
// Cada cambio queda en qa_bug_history con origen "github" (botón o página) o "sincronizacion" (Cron).
import { auditar } from "./auditoria";
import type { EstadoBug, FilaBug } from "./bugs-datos";
import type { Env } from "./env";
import { githubDisponible, graphql, GithubError } from "./github";
import { setMeta } from "./meta";

const TANDA = 50;
export const ULTIMA_SINCRONIZACION = "ultima_sincronizacion";

type IssueGithub = {
    number: number;
    state: "OPEN" | "CLOSED";
    stateReason: "COMPLETED" | "NOT_PLANNED" | "DUPLICATE" | "REOPENED" | null;
    comments: { nodes: { databaseId: number; body: string; createdAt: string; author: { login: string } | null }[] };
    projectItems: { nodes: { project: { number: number }; fieldValueByName: { name?: string } | null }[] };
} | null;

export type CambioEstado = { bug: number; de: EstadoBug; a: EstadoBug; motivo: string };
export type ResultadoSincronizacion = { bugs: number; cambios: CambioEstado[]; comentarios: number; errores: string[] };

const MOTIVO: Record<string, string> = {
    COMPLETED: "completado",
    NOT_PLANNED: "no planificado",
    DUPLICATE: "duplicado",
};

/** Estado al que tiene que pasar el bug según GitHub, o null si no cambia. */
export function estadoSegunGithub(
    actual: EstadoBug,
    antes: string | null,
    gh: { estado: "open" | "closed"; motivo: string | null; tablero: string | null },
): { estado: EstadoBug; motivo: string } | null {
    if (gh.estado === "closed") {
        if (gh.motivo === "not_planned" || gh.motivo === "duplicate") {
            return actual === "descartado" ? null : { estado: "descartado", motivo: `Cerrado en GitHub como ${MOTIVO[gh.motivo.toUpperCase()]}` };
        }
        return actual === "resuelto" || actual === "verificado" ? null : { estado: "resuelto", motivo: "Cerrado en GitHub como completado" };
    }
    // Reabierto: se vio cerrado en una sincronización anterior, o GitHub lo marca como reabierto
    // (cerrado y reabierto entre dos sincronizaciones) y en la plataforma figuraba como terminado
    const terminado = actual === "resuelto" || actual === "verificado" || actual === "descartado";
    if (antes === "closed" || (gh.motivo === "reopened" && terminado)) {
        return actual === "reabierto" ? null : { estado: "reabierto", motivo: "Reabierto en GitHub" };
    }
    const tablero = gh.tablero?.toLowerCase() ?? "";
    if ((tablero === "in progress" || tablero === "in review") && (actual === "abierto" || actual === "reabierto")) {
        return { estado: "en_curso", motivo: `En el tablero: ${gh.tablero}` };
    }
    if (tablero === "done" && !terminado) {
        return { estado: "resuelto", motivo: "En el tablero: Done" };
    }
    return null;
}

function consulta(numeros: number[]): string {
    const issues = numeros
        .map(
            (n) => `i${n}: issue(number: ${n}) {
                number state stateReason
                comments(last: 50) { nodes { databaseId body createdAt author { login } } }
                projectItems(first: 10) { nodes { project { number } fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } } } }
            }`,
        )
        .join("\n");
    return `query($owner: String!, $repo: String!) { repository(owner: $owner, name: $repo) { ${issues} } }`;
}

async function aplicar(env: Env, bug: FilaBug, issue: NonNullable<IssueGithub>, origen: "github" | "sincronizacion", r: ResultadoSincronizacion, propios: Set<number>) {
    const estadoGithub = issue.state === "CLOSED" ? "closed" : "open";
    const motivo = issue.stateReason ? issue.stateReason.toLowerCase() : null;
    const numeroTablero = Number(env.GITHUB_PROJECT_NUMBER);
    const item = issue.projectItems.nodes.find((n) => n.project.number === numeroTablero) ?? issue.projectItems.nodes[0];
    const tablero = item?.fieldValueByName?.name ?? null;

    const cambio = estadoSegunGithub(bug.status, bug.github_state, { estado: estadoGithub, motivo, tablero });
    const consultas = [
        env.DB.prepare(
            `UPDATE qa_bugs SET github_state = ?, github_state_reason = ?, project_status = ?, synced_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
                 ${cambio ? ", status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')" : ""}
             WHERE id = ?`,
        ).bind(...[estadoGithub, motivo, tablero, ...(cambio ? [cambio.estado] : []), bug.id]),
    ];
    if (cambio) {
        consultas.push(
            env.DB.prepare("INSERT INTO qa_bug_history (bug_id, from_status, to_status, source, actor, note) VALUES (?, ?, ?, ?, NULL, ?)").bind(
                bug.id, bug.status, cambio.estado, origen, cambio.motivo,
            ),
        );
        r.cambios.push({ bug: bug.id, de: bug.status, a: cambio.estado, motivo: cambio.motivo });
    }
    // Comentarios nuevos de GitHub (los que publicó la plataforma ya tienen su github_comment_id)
    for (const c of issue.comments.nodes) {
        if (propios.has(c.databaseId)) continue;
        consultas.push(
            env.DB.prepare(
                `INSERT INTO qa_comments (target_type, target_id, author, body, origin, github_comment_id, created_at)
                 VALUES ('bug', ?, ?, ?, 'github', ?, ?) ON CONFLICT (github_comment_id) WHERE github_comment_id IS NOT NULL DO NOTHING`,
            ).bind(bug.id, c.author?.login ?? "ghost", c.body, c.databaseId, c.createdAt),
        );
    }
    const resultados = await env.DB.batch(consultas);
    // Las inserciones de comentarios son las últimas sentencias del lote
    r.comentarios += resultados.slice(cambio ? 2 : 1).reduce((t, x) => t + (x.meta.changes ?? 0), 0);
}

/**
 * Sincroniza los bugs publicados (o solo los indicados) con GitHub. No falla si GitHub no está
 * configurado o no responde: lo informa en `errores` y la próxima corrida lo intenta de nuevo.
 */
export async function sincronizar(env: Env, opciones: { bugs?: number[]; origen: "github" | "sincronizacion"; actor?: string }): Promise<ResultadoSincronizacion> {
    const r: ResultadoSincronizacion = { bugs: 0, cambios: [], comentarios: 0, errores: [] };
    if (!githubDisponible(env)) {
        r.errores.push("GitHub no está configurado (falta GITHUB_TOKEN).");
        return r;
    }
    const filtro = opciones.bugs?.length ? `AND id IN (${opciones.bugs.map(() => "?").join(", ")})` : "";
    const { results: bugs } = await env.DB.prepare(`SELECT * FROM qa_bugs WHERE issue_number IS NOT NULL ${filtro} ORDER BY id`)
        .bind(...(opciones.bugs ?? []))
        .all<FilaBug>();
    if (!bugs.length) return r;

    // Comentarios que publicó la propia plataforma (reincidencias y propuestas de cierre): no se importan
    const { results: publicados } = await env.DB.prepare("SELECT comment_id FROM qa_publications WHERE comment_id IS NOT NULL").all<{ comment_id: number }>();
    const propios = new Set(publicados.map((p) => p.comment_id));

    const [owner, repo] = env.GITHUB_REPO.split("/");
    for (let i = 0; i < bugs.length; i += TANDA) {
        const tanda = bugs.slice(i, i + TANDA);
        const numeros = [...new Set(tanda.map((b) => b.issue_number!))];
        try {
            const datos = await graphql<{ repository: Record<string, IssueGithub> }>(env, consulta(numeros), { owner, repo });
            for (const bug of tanda) {
                const issue = datos.repository[`i${bug.issue_number}`];
                if (!issue) {
                    r.errores.push(`El issue #${bug.issue_number} del bug #${bug.id} no existe en ${env.GITHUB_REPO}.`);
                    continue;
                }
                await aplicar(env, bug, issue, opciones.origen, r, propios);
                r.bugs++;
            }
        } catch (e) {
            if (!(e instanceof GithubError)) throw e;
            r.errores.push(e.message);
        }
    }
    await setMeta(env, ULTIMA_SINCRONIZACION, new Date().toISOString());
    if (r.cambios.length || r.comentarios) {
        await auditar(env, opciones.actor ?? null, "github.sincronizar", { origen: opciones.origen, cambios: r.cambios.length, comentarios: r.comentarios });
    }
    return r;
}
