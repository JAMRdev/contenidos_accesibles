// Bugs encontrados en los casos, su estado y sus comentarios.
//
// - GET   /api/bugs?estado=&caso=&seccion=&entorno=&severidad=&autor=&sesion=&desde=&hasta=&q=&antes=&limite=
// - GET   /api/bugs/resumen   → cuántos bugs hay en cada estado
// - POST  /api/bugs  { caso_id, sesion_id?, titulo, severidad?, pasos?, esperado?, obtenido?, url?, version?, descripcion? }
//                     (qa) → borrador; lo que no venga se precarga del caso, la sesión y el resultado
// - GET   /api/bugs/:id   → el bug, su historial de estados y sus comentarios
// - PATCH /api/bugs/:id   { estado?, titulo?, severidad?, pasos?, esperado?, obtenido?, url?, version?, descripcion? } (qa)
// - GET   /api/bugs/:id/vista-previa  → el issue tal como se publicaría y qué le falta (#129)
// - GET   /api/bugs/:id/duplicados    → bugs activos del mismo caso e issues abiertos en GitHub (#129)
// - POST  /api/bugs/:id/publicar      → publica en GitHub o comenta en el issue del caso (qa, #130)
// - GET/POST /api/bugs/:id/comentarios                                           (POST: qa)
//
// Estados: borrador → (publicar en GitHub, #130) → abierto → en_curso → resuelto → verificado,
// más reabierto y descartado. Sin issue, solo se pasa entre borrador y descartado; con issue,
// QA puede mover el estado entre los publicados y GitHub lo actualiza con la sincronización (#134).
import { Hono } from "hono";
import { z } from "zod";
import type { Caso } from "../../catalogo/esquema.ts";
import { SEVERIDADES } from "../../catalogo/esquema.ts";
import { auditar } from "../auditoria";
import { limitePublicar, requiereRol, type AppEnv } from "../auth";
import { publicarBug } from "../publicar";
import { buscarCaso, ENTORNOS } from "../catalogo";
import { comentarioSchema, crearComentario, listarComentarios } from "../comentarios";
import type { Env } from "../env";
import { buscarIssuesDelCaso, githubDisponible, GithubError } from "../github";
import { armarIssue, faltantes } from "../reporte";
import { datosDelIssue, ESTADOS_BUG, type EstadoBug, type FilaBug } from "../bugs-datos";
import { consultaLista, error, Filtros, idDe, leerBody, limiteDe, textoOpcional } from "../validar";

const PUBLICADOS: readonly EstadoBug[] = ["abierto", "en_curso", "resuelto", "verificado", "reabierto", "descartado"];


/** Mensaje de por qué no se puede pasar al estado pedido, o null si se puede. */
export function motivoTransicion(bug: Pick<FilaBug, "status" | "issue_number">, nuevo: EstadoBug): string | null {
    if (bug.status === nuevo) return `El bug ya está en ${nuevo}.`;
    if (bug.issue_number === null) {
        if (nuevo === "abierto") return "Un borrador se abre al publicarlo en GitHub.";
        if (nuevo !== "borrador" && nuevo !== "descartado") return "Sin issue en GitHub, un bug solo puede estar en borrador o descartado.";
        return null;
    }
    if (!PUBLICADOS.includes(nuevo)) return "Un bug publicado en GitHub no vuelve a borrador.";
    return null;
}

const titulo = z.string().trim().min(1, "Poné un título.").max(200, "Hasta 200 caracteres.");
const texto = z.string().trim().max(50000, "Hasta 50.000 caracteres.");

// Campos del reporte (#129); en el alta, los que falten se precargan
const camposReporte = {
    severidad: z.enum(SEVERIDADES).optional(),
    pasos: texto.optional(),
    esperado: texto.optional(),
    obtenido: texto.optional(),
    url: textoOpcional(500),
    version: textoOpcional(100),
    descripcion: texto.optional(),
};

const altaSchema = z.strictObject({ caso_id: z.string().trim(), sesion_id: z.number().int().positive().nullish(), titulo, ...camposReporte });

const cambioSchema = z
    .strictObject({ estado: z.enum(ESTADOS_BUG).optional(), titulo: titulo.optional(), ...camposReporte })
    .refine((c) => Object.values(c).some((v) => v !== undefined), "No hay nada para cambiar.");

function bugJson(b: FilaBug, caso: Caso | undefined) {
    return {
        id: b.id,
        caso: { id: b.case_id, seccion: b.section, titulo: caso?.titulo ?? null },
        sesion_id: b.session_id,
        entorno: b.environment,
        titulo: b.title,
        descripcion: b.description,
        severidad: b.severity,
        reporte: { pasos: b.steps, esperado: b.expected, obtenido: b.actual, url: b.tested_url, version: b.backend_version },
        estado: b.status,
        issue: b.issue_number ? { numero: b.issue_number, url: b.issue_url, estado: b.github_state, motivo: b.github_state_reason, tablero: b.project_status } : null,
        sincronizado_en: b.synced_at,
        creado_por: b.created_by,
        creado_en: b.created_at,
        actualizado_en: b.updated_at,
    };
}

async function bugOError(env: Env, id: number): Promise<FilaBug> {
    const b = await env.DB.prepare("SELECT * FROM qa_bugs WHERE id = ?").bind(id).first<FilaBug>();
    if (!b) throw error(404, "Bug no encontrado.");
    return b;
}

export const bugs = new Hono<AppEnv>();

bugs.get("/", async (c) => {
    const q = c.req.query();
    const limite = limiteDe(q.limite, 50, 200);
    const where = new Filtros();
    where.en("status", consultaLista(q.estado, ESTADOS_BUG));
    where.en("severity", consultaLista(q.severidad, SEVERIDADES));
    where.en("environment", consultaLista(q.entorno, ENTORNOS));
    if (q.caso) where.agregar("case_id = ?", q.caso);
    if (q.seccion) where.agregar("section = ?", q.seccion);
    if (Number(q.sesion)) where.agregar("session_id = ?", Number(q.sesion));
    if (q.autor) where.agregar("created_by = ? COLLATE NOCASE", q.autor.replace(/^@/, ""));
    if (q.desde) where.agregar("created_at >= ?", q.desde);
    if (q.hasta) where.agregar("created_at < date(?, '+1 day')", q.hasta);
    if (q.q) where.agregar("(title LIKE ? OR description LIKE ? OR case_id LIKE ?)", `%${q.q}%`, `%${q.q}%`, `%${q.q}%`);
    if (Number(q.antes)) where.agregar("id < ?", Number(q.antes));

    const { results } = await c.env.DB.prepare(`SELECT * FROM qa_bugs ${where.sql()} ORDER BY id DESC LIMIT ?`)
        .bind(...where.valores, limite + 1)
        .all<FilaBug>();
    const pagina = results.slice(0, limite);
    return c.json({
        bugs: pagina.map((b) => bugJson(b, buscarCaso(b.case_id))),
        siguiente: results.length > limite ? pagina.at(-1)!.id : null,
    });
});

bugs.get("/resumen", async (c) => {
    const { results } = await c.env.DB.prepare("SELECT status, COUNT(*) AS n FROM qa_bugs GROUP BY status").all<{ status: EstadoBug; n: number }>();
    const porEstado = Object.fromEntries(ESTADOS_BUG.map((e) => [e, 0])) as Record<EstadoBug, number>;
    for (const r of results) porEstado[r.status] = r.n;
    return c.json({ por_estado: porEstado });
});

bugs.post("/", requiereRol("qa"), async (c) => {
    const datos = await leerBody(c, altaSchema);
    const caso = buscarCaso(datos.caso_id);
    if (!caso) throw error(404, `El caso ${datos.caso_id} no está en el catálogo.`);
    // Precarga del reporte: pasos y esperado del catálogo; entorno, URL y versión de la sesión;
    // obtenido, de las notas del resultado del caso en esa sesión
    let sesion: { environment: string; base_url: string; backend_version: string | null } | null = null;
    let notasResultado: string | null = null;
    if (datos.sesion_id) {
        sesion = await c.env.DB.prepare("SELECT environment, base_url, backend_version FROM qa_sessions WHERE id = ?")
            .bind(datos.sesion_id)
            .first<{ environment: string; base_url: string; backend_version: string | null }>();
        if (!sesion) throw error(404, "Sesión no encontrada.");
        const r = await c.env.DB.prepare("SELECT notes FROM qa_results WHERE session_id = ? AND case_id = ?")
            .bind(datos.sesion_id, caso.id)
            .first<{ notes: string | null }>();
        notasResultado = r?.notes ?? null;
    }
    const yo = c.get("usuario").github;
    const b = await c.env.DB.prepare(
        `INSERT INTO qa_bugs (case_id, section, session_id, environment, title, description, severity, created_by,
                              steps, expected, actual, tested_url, backend_version)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *`,
    )
        .bind(
            caso.id, caso.seccion, datos.sesion_id ?? null, sesion?.environment ?? null, datos.titulo, datos.descripcion ?? "",
            datos.severidad ?? caso.severidad, yo,
            datos.pasos ?? caso.pasos.join("\n"),
            datos.esperado ?? caso.esperado,
            datos.obtenido ?? notasResultado ?? "",
            datos.url !== undefined ? datos.url : (sesion?.base_url ?? null),
            datos.version !== undefined ? datos.version : (sesion?.backend_version ?? null),
        )
        .first<FilaBug>();
    await c.env.DB.prepare("INSERT INTO qa_bug_history (bug_id, from_status, to_status, source, actor) VALUES (?, NULL, 'borrador', 'qa', ?)")
        .bind(b!.id, yo)
        .run();
    await auditar(c.env, yo, "bug.crear", { id: b!.id, caso: caso.id });
    return c.json(bugJson(b!, caso), 201);
});

bugs.get("/:id", async (c) => {
    const b = await bugOError(c.env, idDe(c.req.param("id"), "Bug"));
    const { results: historial } = await c.env.DB.prepare(
        "SELECT from_status, to_status, source, actor, note, created_at FROM qa_bug_history WHERE bug_id = ? ORDER BY id",
    )
        .bind(b.id)
        .all<{ from_status: string | null; to_status: string; source: string; actor: string | null; note: string | null; created_at: string }>();
    return c.json({
        ...bugJson(b, buscarCaso(b.case_id)),
        historial: historial.map((h) => ({ de: h.from_status, a: h.to_status, origen: h.source, actor: h.actor, nota: h.note, en: h.created_at })),
        comentarios: await listarComentarios(c.env, "bug", b.id),
    });
});

bugs.patch("/:id", requiereRol("qa"), async (c) => {
    const b = await bugOError(c.env, idDe(c.req.param("id"), "Bug"));
    const datos = await leerBody(c, cambioSchema);
    if (datos.estado) {
        const motivo = motivoTransicion(b, datos.estado);
        if (motivo) throw error(409, motivo);
    }
    const yo = c.get("usuario").github;
    const consultas = [
        c.env.DB.prepare(
            `UPDATE qa_bugs SET status = COALESCE(?, status), title = COALESCE(?, title), description = COALESCE(?, description),
                 severity = COALESCE(?, severity), steps = COALESCE(?, steps), expected = COALESCE(?, expected),
                 actual = COALESCE(?, actual),
                 tested_url = CASE WHEN ? THEN ? ELSE tested_url END,
                 backend_version = CASE WHEN ? THEN ? ELSE backend_version END,
                 updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
             WHERE id = ?`,
        ).bind(
            datos.estado ?? null, datos.titulo ?? null, datos.descripcion ?? null, datos.severidad ?? null,
            datos.pasos ?? null, datos.esperado ?? null, datos.obtenido ?? null,
            // url y version se pueden vaciar: solo cambian si vienen en el body
            Number(datos.url !== undefined), datos.url ?? null,
            Number(datos.version !== undefined), datos.version ?? null,
            b.id,
        ),
    ];
    if (datos.estado) {
        consultas.push(
            c.env.DB.prepare("INSERT INTO qa_bug_history (bug_id, from_status, to_status, source, actor) VALUES (?, ?, ?, 'qa', ?)").bind(
                b.id, b.status, datos.estado, yo,
            ),
        );
    }
    await c.env.DB.batch(consultas);
    if (datos.estado) await auditar(c.env, yo, "bug.estado", { id: b.id, de: b.status, a: datos.estado });
    return c.json(bugJson(await bugOError(c.env, b.id), buscarCaso(b.case_id)));
});

bugs.get("/:id/vista-previa", async (c) => {
    const b = await bugOError(c.env, idDe(c.req.param("id"), "Bug"));
    const caso = buscarCaso(b.case_id);
    if (!caso) throw error(409, `El caso ${b.case_id} ya no está en el catálogo.`);
    const { reporte, evidencias } = await datosDelIssue(c.env, b);
    return c.json({
        ...armarIssue(reporte, caso, evidencias, new URL(c.req.url).origin),
        repo: c.env.GITHUB_REPO,
        faltan: faltantes(reporte),
    });
});

// Bugs que siguen activos (no descartados ni verificados)
const ACTIVOS: readonly EstadoBug[] = ["borrador", "abierto", "en_curso", "resuelto", "reabierto"];

bugs.get("/:id/duplicados", async (c) => {
    const b = await bugOError(c.env, idDe(c.req.param("id"), "Bug"));
    const { results } = await c.env.DB.prepare(
        `SELECT * FROM qa_bugs WHERE case_id = ? AND id != ? AND status IN (${ACTIVOS.map(() => "?").join(", ")}) ORDER BY id DESC`,
    )
        .bind(b.case_id, b.id, ...ACTIVOS)
        .all<FilaBug>();
    const enGithub: { disponible: boolean; issues: Awaited<ReturnType<typeof buscarIssuesDelCaso>>; error: string | null } = {
        disponible: githubDisponible(c.env),
        issues: [],
        error: null,
    };
    if (enGithub.disponible) {
        try {
            // Sin el propio issue, si ya está publicado
            enGithub.issues = (await buscarIssuesDelCaso(c.env, b.case_id)).filter((i) => i.numero !== b.issue_number);
        } catch (e) {
            if (!(e instanceof GithubError)) throw e;
            enGithub.error = e.message;
        }
    }
    return c.json({ plataforma: results.map((d) => bugJson(d, buscarCaso(d.case_id))), github: enGithub });
});

bugs.post("/:id/publicar", requiereRol("qa"), limitePublicar, async (c) => {
    const b = await bugOError(c.env, idDe(c.req.param("id"), "Bug"));
    const resultado = await publicarBug(c.env, b, c.get("usuario").github, new URL(c.req.url).origin);
    return c.json({ ...resultado, bug: bugJson(await bugOError(c.env, b.id), buscarCaso(b.case_id)) });
});

bugs.get("/:id/comentarios", async (c) => {
    const b = await bugOError(c.env, idDe(c.req.param("id"), "Bug"));
    return c.json({ comentarios: await listarComentarios(c.env, "bug", b.id) });
});

bugs.post("/:id/comentarios", requiereRol("qa"), async (c) => {
    const b = await bugOError(c.env, idDe(c.req.param("id"), "Bug"));
    const datos = await leerBody(c, comentarioSchema);
    return c.json({ comentarios: await crearComentario(c.env, "bug", b.id, c.get("usuario").github, datos) }, 201);
});
