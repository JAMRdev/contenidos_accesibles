// Bugs encontrados en los casos, su estado y sus comentarios.
//
// - GET   /api/bugs?estado=&caso=&seccion=&entorno=&severidad=&autor=&sesion=&desde=&hasta=&q=&antes=&limite=
// - POST  /api/bugs  { caso_id, sesion_id?, titulo, descripcion?, severidad? }   (qa) → borrador
// - GET   /api/bugs/:id   → el bug, su historial de estados y sus comentarios
// - PATCH /api/bugs/:id   { estado?, titulo?, descripcion?, severidad? }          (qa)
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
import { requiereRol, type AppEnv } from "../auth";
import { buscarCaso, ENTORNOS } from "../catalogo";
import { comentarioSchema, crearComentario, listarComentarios } from "../comentarios";
import type { Env } from "../env";
import { consultaLista, error, Filtros, idDe, leerBody, limiteDe } from "../validar";

export const ESTADOS_BUG = ["borrador", "abierto", "en_curso", "resuelto", "verificado", "reabierto", "descartado"] as const;
export type EstadoBug = (typeof ESTADOS_BUG)[number];
const PUBLICADOS: readonly EstadoBug[] = ["abierto", "en_curso", "resuelto", "verificado", "reabierto", "descartado"];

type FilaBug = {
    id: number;
    case_id: string;
    section: string;
    session_id: number | null;
    environment: string | null;
    title: string;
    description: string;
    severity: (typeof SEVERIDADES)[number];
    status: EstadoBug;
    issue_number: number | null;
    issue_url: string | null;
    github_state: string | null;
    github_state_reason: string | null;
    project_status: string | null;
    synced_at: string | null;
    created_by: string;
    created_at: string;
    updated_at: string;
};

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
const descripcion = z.string().trim().max(50000, "Hasta 50.000 caracteres.");

const altaSchema = z.strictObject({
    caso_id: z.string().trim(),
    sesion_id: z.number().int().positive().nullish(),
    titulo,
    descripcion: descripcion.default(""),
    severidad: z.enum(SEVERIDADES).optional(),
});

const cambioSchema = z
    .strictObject({ estado: z.enum(ESTADOS_BUG).optional(), titulo: titulo.optional(), descripcion: descripcion.optional(), severidad: z.enum(SEVERIDADES).optional() })
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

bugs.post("/", requiereRol("qa"), async (c) => {
    const datos = await leerBody(c, altaSchema);
    const caso = buscarCaso(datos.caso_id);
    if (!caso) throw error(404, `El caso ${datos.caso_id} no está en el catálogo.`);
    let entorno: string | null = null;
    if (datos.sesion_id) {
        const s = await c.env.DB.prepare("SELECT environment FROM qa_sessions WHERE id = ?").bind(datos.sesion_id).first<{ environment: string }>();
        if (!s) throw error(404, "Sesión no encontrada.");
        entorno = s.environment;
    }
    const yo = c.get("usuario").github;
    const b = await c.env.DB.prepare(
        `INSERT INTO qa_bugs (case_id, section, session_id, environment, title, description, severity, created_by)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?) RETURNING *`,
    )
        .bind(caso.id, caso.seccion, datos.sesion_id ?? null, entorno, datos.titulo, datos.descripcion, datos.severidad ?? caso.severidad, yo)
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
                 severity = COALESCE(?, severity), updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
             WHERE id = ?`,
        ).bind(datos.estado ?? null, datos.titulo ?? null, datos.descripcion ?? null, datos.severidad ?? null, b.id),
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

bugs.get("/:id/comentarios", async (c) => {
    const b = await bugOError(c.env, idDe(c.req.param("id"), "Bug"));
    return c.json({ comentarios: await listarComentarios(c.env, "bug", b.id) });
});

bugs.post("/:id/comentarios", requiereRol("qa"), async (c) => {
    const b = await bugOError(c.env, idDe(c.req.param("id"), "Bug"));
    const datos = await leerBody(c, comentarioSchema);
    return c.json({ comentarios: await crearComentario(c.env, "bug", b.id, c.get("usuario").github, datos) }, 201);
});
