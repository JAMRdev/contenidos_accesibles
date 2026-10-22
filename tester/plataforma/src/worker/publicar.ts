// Publicación en GitHub (Issue #130). Nada se publica sin que QA lo pida, nada se cierra solo y
// publicar dos veces no duplica: cada publicación queda en qa_publications con una clave única.
//
// Un bug:
//   1. Si ya tiene issue, solo se envían sus comentarios pendientes.
//   2. Si hay un issue abierto del mismo caso (de otro bug de la plataforma o con la marca del caso
//      en GitHub), se comenta en él: el bug se vincula a ese issue o, si ya es de otro bug, queda
//      descartado como duplicado.
//   3. Si no, se crea el issue (sin asignar) y se agrega al tablero con "Estado QA" = Falló.
// Una sesión: publica sus bugs en borrador, propone cerrar los bugs abiertos de casos que ahora
// pasaron, crea o actualiza el issue de ejecución y envía los comentarios pendientes.
import { auditar } from "./auditoria";
import { aplica, type Entorno } from "./catalogo";
import { cargarCasos, casosDelArea, obtenerCaso, type Area, type CasoQA } from "./casos";
import type { Env } from "./env";
import {
    agregarAlTablero,
    buscarPorMarca,
    comentarIssue,
    crearIssue,
    editarIssue,
    GithubError,
    githubDisponible,
    tableroAplica,
} from "./github";
import {
    armarEjecucion,
    armarIssue,
    comentarioInterno,
    comentarioPropuestaCierre,
    comentarioReincidencia,
    faltantes,
    marcaBug,
    marcaCaso,
    marcaSesion,
    type FilaEjecucion,
} from "./reporte";
import { datosDelIssue, type FilaBug } from "./bugs-datos";
import { error } from "./validar";

type Clase = "issue" | "duplicado" | "comentario" | "ejecucion" | "propuesta_cierre";
type Publicacion = { id: number; key: string; status: "pendiente" | "ok" | "error"; issue_number: number | null; updated_at: string };

const ACTIVOS_EN_GITHUB = ["abierto", "en_curso", "reabierto"];

/** Fila de la publicación (la crea como pendiente si no existe) y si es nueva. */
async function registrar(env: Env, key: string, clase: Clase, datos: { bug?: number; sesion?: number; actor: string }) {
    const nueva = await env.DB.prepare(
        "INSERT INTO qa_publications (key, kind, bug_id, session_id, actor) VALUES (?, ?, ?, ?, ?) ON CONFLICT (key) DO NOTHING RETURNING *",
    )
        .bind(key, clase, datos.bug ?? null, datos.sesion ?? null, datos.actor)
        .first<Publicacion>();
    if (nueva) return { fila: nueva, nueva: true };
    const fila = await env.DB.prepare("SELECT * FROM qa_publications WHERE key = ?").bind(key).first<Publicacion>();
    return { fila: fila!, nueva: false };
}

async function terminar(env: Env, key: string, datos: { issue?: number; comentario?: number; error?: string }) {
    await env.DB.prepare(
        `UPDATE qa_publications SET status = ?, issue_number = COALESCE(?, issue_number), comment_id = COALESCE(?, comment_id),
             error = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE key = ?`,
    )
        .bind(datos.error ? "error" : "ok", datos.issue ?? null, datos.comentario ?? null, datos.error ?? null, key)
        .run();
}

/** Corre una publicación y deja asentado el resultado (también si GitHub falla a mitad de camino). */
async function conRegistro<T>(env: Env, key: string, clase: Clase, datos: { bug?: number; sesion?: number; actor: string }, accion: (reintento: boolean) => Promise<T & { issue?: number; comentario?: number }>) {
    const { fila, nueva } = await registrar(env, key, clase, datos);
    if (!nueva && fila.status === "pendiente" && Date.now() - Date.parse(fila.updated_at) < 60_000) {
        throw error(409, "Esta publicación ya está en curso. Esperá unos segundos y recargá.");
    }
    try {
        const resultado = await accion(!nueva);
        await terminar(env, key, { issue: resultado.issue, comentario: resultado.comentario });
        return resultado;
    } catch (e) {
        await terminar(env, key, { error: e instanceof Error ? e.message : String(e) });
        throw e;
    }
}

function exigirGithub(env: Env) {
    if (!githubDisponible(env)) throw error(409, "GitHub no está configurado: falta el secreto GITHUB_TOKEN del Worker.");
}

async function historial(env: Env, bugId: number, de: string, a: string, actor: string, nota: string) {
    await env.DB.prepare("INSERT INTO qa_bug_history (bug_id, from_status, to_status, source, actor, note) VALUES (?, ?, ?, 'qa', ?, ?)")
        .bind(bugId, de, a, actor, nota)
        .run();
}

/** Comentarios del bug marcados "también en GitHub" que todavía no se enviaron. */
async function enviarComentarios(env: Env, bugId: number, issue: number, actor: string, origen: string): Promise<number> {
    const { results } = await env.DB.prepare(
        "SELECT id, author, body FROM qa_comments WHERE target_type = 'bug' AND target_id = ? AND to_github = 1 AND github_comment_id IS NULL AND origin = 'plataforma' ORDER BY id",
    )
        .bind(bugId)
        .all<{ id: number; author: string; body: string }>();
    for (const c of results) {
        const enviado = await conRegistro(env, `comentario:${c.id}`, "comentario", { bug: bugId, actor }, async () => {
            const r = await comentarIssue(env, issue, comentarioInterno(c.author, c.body, bugId, origen));
            return { issue, comentario: r.id };
        });
        await env.DB.prepare("UPDATE qa_comments SET github_comment_id = ? WHERE id = ?").bind(enviado.comentario, c.id).run();
    }
    return results.length;
}

export type ResultadoBug = {
    bug_id: number;
    accion: "creado" | "actualizado" | "vinculado" | "duplicado" | "ya_publicado";
    issue: { numero: number; url: string };
    comentarios: number;
    avisos: string[];
};

/** Issue abierto del mismo caso: de otro bug de la plataforma o, si no, con la marca del caso en GitHub. */
async function issueDelCaso(env: Env, bug: FilaBug): Promise<{ numero: number; url: string; bugId: number | null } | null> {
    const otro = await env.DB.prepare(
        `SELECT id, issue_number, issue_url FROM qa_bugs WHERE case_id = ? AND id != ? AND issue_number IS NOT NULL
             AND status IN (${ACTIVOS_EN_GITHUB.map(() => "?").join(", ")}) ORDER BY id DESC LIMIT 1`,
    )
        .bind(bug.case_id, bug.id, ...ACTIVOS_EN_GITHUB)
        .first<{ id: number; issue_number: number; issue_url: string }>();
    if (otro) return { numero: otro.issue_number, url: otro.issue_url, bugId: otro.id };
    const [enGithub] = await buscarPorMarca(env, marcaCaso(bug.case_id), true);
    if (!enGithub) return null;
    // Si el issue ya es de un bug de la plataforma (aunque esté en otro estado), se respeta
    const dueno = await env.DB.prepare("SELECT id FROM qa_bugs WHERE issue_number = ?").bind(enGithub.numero).first<{ id: number }>();
    return { numero: enGithub.numero, url: enGithub.url, bugId: dueno?.id ?? null };
}

export async function publicarBug(env: Env, bug: FilaBug, actor: string, origen: string): Promise<ResultadoBug> {
    exigirGithub(env);
    if (bug.issue_number) {
        // Si el issue lo creó este bug (no se vinculó a uno existente con un comentario), se actualiza
        // su cuerpo con el reporte actual: así toma las evidencias y cambios posteriores
        let accion: ResultadoBug["accion"] = "ya_publicado";
        const propio = await env.DB.prepare("SELECT comment_id FROM qa_publications WHERE key = ? AND kind = 'issue' AND status = 'ok'")
            .bind(`bug:${bug.id}:issue`)
            .first<{ comment_id: number | null }>();
        const caso = await obtenerCaso(env, bug.case_id);
        if (propio && propio.comment_id === null && caso) {
            const { reporte, evidencias } = await datosDelIssue(env, bug);
            await editarIssue(env, bug.issue_number, armarIssue(reporte, caso, evidencias, origen));
            await auditar(env, actor, "github.actualizar_bug", { bug: bug.id, issue: bug.issue_number, evidencias: evidencias.length });
            accion = "actualizado";
        }
        const comentarios = await enviarComentarios(env, bug.id, bug.issue_number, actor, origen);
        return { bug_id: bug.id, accion, issue: { numero: bug.issue_number, url: bug.issue_url! }, comentarios, avisos: [] };
    }
    if (bug.status !== "borrador") throw error(409, "Solo se publican bugs en borrador.");
    const caso = await obtenerCaso(env, bug.case_id);
    if (!caso) throw error(409, `El caso ${bug.case_id} ya no está en el catálogo.`);
    const { reporte, evidencias } = await datosDelIssue(env, bug);
    const faltan = faltantes(reporte);
    if (faltan.length) throw error(422, `Para publicar falta completar: ${faltan.join(", ")}.`);

    const existente = await issueDelCaso(env, bug);
    if (existente) {
        const duplicado = existente.bugId !== null;
        await conRegistro(env, `bug:${bug.id}:issue`, duplicado ? "duplicado" : "issue", { bug: bug.id, actor }, async () => {
            const c = await comentarIssue(env, existente.numero, comentarioReincidencia(reporte, caso, evidencias, origen));
            return { issue: existente.numero, comentario: c.id };
        });
        if (duplicado) {
            await env.DB.prepare("UPDATE qa_bugs SET status = 'descartado', updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?").bind(bug.id).run();
            await historial(env, bug.id, bug.status, "descartado", actor, `Duplicado del bug #${existente.bugId}: se comentó en el issue #${existente.numero}`);
        } else {
            await env.DB.prepare(
                `UPDATE qa_bugs SET status = 'abierto', issue_number = ?, issue_url = ?, github_state = 'open',
                     synced_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?`,
            )
                .bind(existente.numero, existente.url, bug.id)
                .run();
            await historial(env, bug.id, bug.status, "abierto", actor, `Vinculado al issue existente #${existente.numero} (se comentó en él)`);
        }
        await auditar(env, actor, "github.comentar_existente", { bug: bug.id, issue: existente.numero, duplicado });
        const comentarios = duplicado ? 0 : await enviarComentarios(env, bug.id, existente.numero, actor, origen);
        return { bug_id: bug.id, accion: duplicado ? "duplicado" : "vinculado", issue: { numero: existente.numero, url: existente.url }, comentarios, avisos: [] };
    }

    const issue = armarIssue(reporte, caso, evidencias, origen);
    const creado = await conRegistro(env, `bug:${bug.id}:issue`, "issue", { bug: bug.id, actor }, async (reintento) => {
        // Un intento anterior pudo crear el issue y fallar antes de guardarlo: se busca por la marca del bug
        if (reintento) {
            const [previo] = await buscarPorMarca(env, marcaBug(bug.id));
            if (previo) return { issue: previo.numero, url: previo.url, nodeId: null as string | null };
        }
        const i = await crearIssue(env, issue);
        return { issue: i.numero, url: i.url, nodeId: i.nodeId as string | null };
    });
    await env.DB.prepare(
        `UPDATE qa_bugs SET status = 'abierto', issue_number = ?, issue_url = ?, github_state = 'open',
             synced_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'), updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?`,
    )
        .bind(creado.issue, creado.url, bug.id)
        .run();
    await historial(env, bug.id, bug.status, "abierto", actor, `Publicado en GitHub como #${creado.issue}`);
    await auditar(env, actor, "github.publicar_bug", { bug: bug.id, issue: creado.issue, repo: env.GITHUB_REPO });

    const avisos: string[] = [];
    if (creado.nodeId && tableroAplica(env)) {
        try {
            const aviso = await agregarAlTablero(env, creado.nodeId, "Falló");
            if (aviso) avisos.push(aviso);
        } catch (e) {
            avisos.push(`El issue se creó, pero no se pudo agregar al tablero: ${(e as Error).message}`);
        }
    }
    const comentarios = await enviarComentarios(env, bug.id, creado.issue, actor, origen);
    return { bug_id: bug.id, accion: "creado", issue: { numero: creado.issue, url: creado.url }, comentarios, avisos };
}

type FilaSesion = {
    id: number;
    title: string;
    environment: Entorno;
    base_url: string;
    backend_version: string | null;
    status: "abierta" | "cerrada";
    created_by: string;
    created_at: string;
    area: Area;
    execution_issue_number: number | null;
    execution_issue_url: string | null;
};

export type PlanSesion = {
    bugs: { id: number; titulo: string; caso: string; listo: boolean; faltan: string[] }[];
    propuestas_cierre: { bug_id: number; issue: number; caso: string }[];
    ejecucion: { accion: "crear" | "actualizar"; numero: number | null };
    comentarios: number;
    repo: string;
    github_disponible: boolean;
};

async function armarPlan(env: Env, s: FilaSesion): Promise<PlanSesion & { _bugs: FilaBug[]; _propuestas: { bug: number; issue: number; caso: CasoQA }[] }> {
    const { results: bugs } = await env.DB.prepare("SELECT * FROM qa_bugs WHERE session_id = ? ORDER BY id").bind(s.id).all<FilaBug>();
    const borradores = bugs.filter((b) => b.status === "borrador");
    const planBugs = await Promise.all(
        borradores.map(async (b) => {
            const { reporte } = await datosDelIssue(env, b);
            const faltan = faltantes(reporte);
            return { id: b.id, titulo: b.title, caso: b.case_id, listo: faltan.length === 0, faltan };
        }),
    );
    // Casos que pasaron en esta sesión y tienen un bug abierto en GitHub (de cualquier sesión)
    const { results: abiertos } = await env.DB.prepare(
        `SELECT b.id, b.issue_number, b.case_id FROM qa_bugs b
         JOIN qa_results r ON r.case_id = b.case_id AND r.session_id = ? AND r.status = 'paso'
         WHERE b.issue_number IS NOT NULL AND b.status IN ('abierto', 'en_curso', 'reabierto', 'resuelto')`,
    )
        .bind(s.id)
        .all<{ id: number; issue_number: number; case_id: string }>();
    const casos = await cargarCasos(env);
    const propuestas = abiertos
        .map((b) => ({ bug: b.id, issue: b.issue_number, caso: casos.porId.get(b.case_id) }))
        .filter((p): p is { bug: number; issue: number; caso: CasoQA } => Boolean(p.caso));
    const ids = bugs.filter((b) => b.issue_number || b.status === "borrador").map((b) => b.id);
    const pendientes = ids.length
        ? (
              await env.DB.prepare(
                  `SELECT COUNT(*) AS n FROM qa_comments WHERE target_type = 'bug' AND to_github = 1 AND github_comment_id IS NULL
                   AND origin = 'plataforma' AND target_id IN (${ids.map(() => "?").join(", ")})`,
              )
                  .bind(...ids)
                  .first<{ n: number }>()
          )?.n ?? 0
        : 0;
    return {
        bugs: planBugs,
        propuestas_cierre: propuestas.map((p) => ({ bug_id: p.bug, issue: p.issue, caso: p.caso.id })),
        ejecucion: { accion: s.execution_issue_number ? "actualizar" : "crear", numero: s.execution_issue_number },
        comentarios: pendientes,
        repo: env.GITHUB_REPO,
        github_disponible: githubDisponible(env),
        _bugs: borradores,
        _propuestas: propuestas,
    };
}

export async function sesionOError(env: Env, id: number): Promise<FilaSesion> {
    const s = await env.DB.prepare("SELECT * FROM qa_sessions WHERE id = ?").bind(id).first<FilaSesion>();
    if (!s) throw error(404, "Sesión no encontrada.");
    return s;
}

/** Qué se va a crear o comentar si se publica la sesión (para la pantalla de publicación). */
export async function planDeSesion(env: Env, id: number): Promise<PlanSesion> {
    const { _bugs, _propuestas, ...plan } = await armarPlan(env, await sesionOError(env, id));
    return plan;
}

async function filasEjecucion(env: Env, sesionId: number, casos: CasoQA[]): Promise<FilaEjecucion[]> {
    const [resultados, bugs, evidencias] = await env.DB.batch([
        env.DB.prepare("SELECT id, case_id, status FROM qa_results WHERE session_id = ?").bind(sesionId),
        env.DB.prepare("SELECT case_id, issue_number, issue_url FROM qa_bugs WHERE session_id = ? AND issue_number IS NOT NULL ORDER BY id DESC").bind(sesionId),
        env.DB.prepare("SELECT e.id, e.result_id FROM qa_evidence e JOIN qa_results r ON r.id = e.result_id WHERE r.session_id = ? ORDER BY e.id").bind(sesionId),
    ]);
    const porCaso = new Map((resultados.results as { id: number; case_id: string; status: FilaEjecucion["estado"] }[]).map((r) => [r.case_id, r]));
    const bugPorCaso = new Map<string, { numero: number; url: string }>();
    for (const b of bugs.results as { case_id: string; issue_number: number; issue_url: string }[]) {
        if (!bugPorCaso.has(b.case_id)) bugPorCaso.set(b.case_id, { numero: b.issue_number, url: b.issue_url });
    }
    const evidenciasPorResultado = new Map<number, number[]>();
    for (const e of evidencias.results as { id: number; result_id: number }[]) {
        evidenciasPorResultado.set(e.result_id, [...(evidenciasPorResultado.get(e.result_id) ?? []), e.id]);
    }
    // En el orden del catálogo
    return casos
        .filter((c) => porCaso.has(c.id))
        .map((caso) => {
            const r = porCaso.get(caso.id)!;
            return { caso, estado: r.status, bug: bugPorCaso.get(caso.id) ?? null, evidencias: evidenciasPorResultado.get(r.id) ?? [] };
        });
}

export type ResultadoSesion = {
    ejecucion: { numero: number; url: string };
    bugs: ResultadoBug[];
    omitidos: { id: number; faltan: string[] }[];
    propuestas_cierre: number[];
    comentarios: number;
    errores: string[];
    avisos: string[];
};

export async function publicarSesion(env: Env, id: number, actor: string, origen: string): Promise<ResultadoSesion> {
    exigirGithub(env);
    const s = await sesionOError(env, id);
    const plan = await armarPlan(env, s);
    const resultado: ResultadoSesion = { ejecucion: { numero: 0, url: "" }, bugs: [], omitidos: [], propuestas_cierre: [], comentarios: 0, errores: [], avisos: [] };

    // 1. Bugs en borrador (los incompletos se omiten)
    for (const b of plan._bugs) {
        const p = plan.bugs.find((x) => x.id === b.id)!;
        if (!p.listo) {
            resultado.omitidos.push({ id: b.id, faltan: p.faltan });
            continue;
        }
        try {
            const r = await publicarBug(env, b, actor, origen);
            resultado.bugs.push(r);
            resultado.avisos.push(...r.avisos);
        } catch (e) {
            resultado.errores.push(`Bug #${b.id}: ${(e as Error).message}`);
        }
    }

    // 2. Propuestas de cierre (una por bug y sesión)
    const sesion = { id: s.id, titulo: s.title, version: s.backend_version };
    for (const p of plan._propuestas) {
        try {
            await conRegistro(env, `sesion:${s.id}:propuesta:${p.bug}`, "propuesta_cierre", { bug: p.bug, sesion: s.id, actor }, async (reintento) => {
                if (reintento) return { issue: p.issue };
                const c = await comentarIssue(env, p.issue, comentarioPropuestaCierre(p.caso, sesion, origen));
                return { issue: p.issue, comentario: c.id };
            });
            resultado.propuestas_cierre.push(p.issue);
        } catch (e) {
            if (!(e instanceof GithubError)) throw e;
            resultado.errores.push(`Propuesta de cierre en #${p.issue}: ${e.message}`);
        }
    }

    // 3. Bugs de la sesión ya publicados antes: se actualiza su issue y se envían sus comentarios pendientes
    const { results: publicados } = await env.DB.prepare("SELECT * FROM qa_bugs WHERE session_id = ? AND issue_number IS NOT NULL ORDER BY id").bind(s.id).all<FilaBug>();
    const yaPublicados = new Set(resultado.bugs.map((b) => b.bug_id));
    for (const b of publicados.filter((x) => !yaPublicados.has(x.id))) {
        try {
            resultado.bugs.push(await publicarBug(env, b, actor, origen));
        } catch (e) {
            resultado.errores.push(`Bug #${b.id}: ${(e as Error).message}`);
        }
    }
    resultado.comentarios += resultado.bugs.reduce((t, b) => t + b.comentarios, 0);

    // 4. Issue de ejecución (al final, para que la tabla enlace los issues recién creados)
    const casos = await cargarCasos(env);
    const total = casosDelArea(casos, s.area).filter((c) => aplica(c, s.environment)).length;
    const ejecucion = armarEjecucion(
        { id: s.id, titulo: s.title, area: s.area, entorno: s.environment, url: s.base_url, version: s.backend_version, creada_por: s.created_by, creada_en: s.created_at, estado: s.status },
        await filasEjecucion(env, s.id, casos.lista),
        total,
        origen,
    );
    const issue = await conRegistro(env, `sesion:${s.id}:ejecucion`, "ejecucion", { sesion: s.id, actor }, async (reintento) => {
        let numero = s.execution_issue_number;
        if (!numero && reintento) numero = (await buscarPorMarca(env, marcaSesion(s.id)))[0]?.numero ?? null;
        if (numero) {
            const i = await editarIssue(env, numero, ejecucion);
            return { issue: i.numero, url: i.url, nodeId: null as string | null };
        }
        const i = await crearIssue(env, ejecucion);
        return { issue: i.numero, url: i.url, nodeId: i.nodeId as string | null };
    });
    // La clave de la ejecución se reutiliza en cada publicación: actualiza el mismo issue
    await env.DB.prepare("UPDATE qa_sessions SET execution_issue_number = ?, execution_issue_url = ? WHERE id = ?").bind(issue.issue, issue.url, s.id).run();
    if (issue.nodeId && tableroAplica(env)) {
        try {
            await agregarAlTablero(env, issue.nodeId);
        } catch (e) {
            resultado.avisos.push(`El issue de ejecución se creó, pero no se pudo agregar al tablero: ${(e as Error).message}`);
        }
    }
    resultado.ejecucion = { numero: issue.issue, url: issue.url };
    await auditar(env, actor, "github.publicar_sesion", {
        sesion: s.id,
        ejecucion: issue.issue,
        bugs: resultado.bugs.map((b) => `${b.accion}:${b.issue.numero}`),
        omitidos: resultado.omitidos.length,
        errores: resultado.errores.length,
    });
    return resultado;
}
