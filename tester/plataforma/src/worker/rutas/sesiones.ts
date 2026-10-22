// Sesiones de QA y resultados de cada caso.
//
// - GET    /api/sesiones?estado=&entorno=&autor=&q=&antes=&limite=   (cualquier rol)
// - POST   /api/sesiones                                               (qa)
// - GET    /api/sesiones/:id?seccion=&resultado=&q=&destructivo=&mock=&ia_real=   (cualquier rol)
// - PATCH  /api/sesiones/:id                                           (qa, sesión abierta)
// - POST   /api/sesiones/:id/cerrar                                    (qa)
// - POST   /api/sesiones/:id/reabrir                                   (admin)
// - PUT    /api/sesiones/:id/resultados/:caso   { estado, notas? }     (qa, sesión abierta)
// - DELETE /api/sesiones/:id/resultados/:caso   → vuelve a pendiente   (qa, sesión abierta)
// - GET    /api/sesiones/:id/publicacion         → qué se va a crear o comentar en GitHub (#130)
// - POST   /api/sesiones/:id/publicar            → publica la sesión en GitHub (qa, #130)
import { Hono, type Context } from "hono";
import { z } from "zod";
import { auditar } from "../auditoria";
import { limitePublicar, requiereRol, type AppEnv } from "../auth";
import { planDeSesion, publicarSesion } from "../publicar";
import { aplica, ENTORNOS, filtrarCasos, type Entorno } from "../catalogo";
import { AREAS, cargarCasos, casosDelArea, type Area, type Casos, type CasoQA } from "../casos";
import type { Env } from "../env";
import { consultaBooleana, consultaLista, error, Filtros, idDe, leerBody, limiteDe, textoOpcional } from "../validar";

export const ESTADOS_RESULTADO = ["paso", "fallo", "bloqueado", "omitido"] as const;
type EstadoResultado = (typeof ESTADOS_RESULTADO)[number];

type FilaSesion = {
    id: number;
    title: string;
    environment: Entorno;
    base_url: string;
    backend_version: string | null;
    notes: string | null;
    status: "abierta" | "cerrada";
    created_by: string;
    created_at: string;
    closed_by: string | null;
    closed_at: string | null;
    area: Area;
    execution_issue_number: number | null;
    execution_issue_url: string | null;
};

type FilaResultado = { id: number; case_id: string; status: EstadoResultado; notes: string | null; updated_by: string; updated_at: string };

const altaSchema = z.strictObject({
    titulo: z.string().trim().min(1, "Poné un título.").max(200, "Hasta 200 caracteres."),
    area: z.enum(AREAS).default("backend"),
    entorno: z.enum(ENTORNOS),
    url: z.url({ protocol: /^https?$/, error: "La URL tiene que empezar con http:// o https://." }),
    version: textoOpcional(100),
    notas: textoOpcional(5000),
});

const cambioSchema = z
    .strictObject({ titulo: altaSchema.shape.titulo.optional(), version: textoOpcional(100), notas: textoOpcional(5000) })
    .refine((c) => Object.keys(c).length > 0, "No hay nada para cambiar.");

const resultadoSchema = z.strictObject({ estado: z.enum(ESTADOS_RESULTADO), notas: textoOpcional(10000) });

function sesionJson(s: FilaSesion) {
    return {
        id: s.id,
        titulo: s.title,
        area: s.area,
        entorno: s.environment,
        url: s.base_url,
        version: s.backend_version,
        notas: s.notes,
        estado: s.status,
        creada_por: s.created_by,
        creada_en: s.created_at,
        cerrada_por: s.closed_by,
        cerrada_en: s.closed_at,
        ejecucion: s.execution_issue_number ? { numero: s.execution_issue_number, url: s.execution_issue_url } : null,
    };
}

/** Casos del área que aplican en el entorno de la sesión. */
function casosAplicables(casos: Casos, s: Pick<FilaSesion, "area" | "environment">): CasoQA[] {
    return casosDelArea(casos, s.area).filter((c) => aplica(c, s.environment));
}

/** Cuántos casos hay en cada resultado (los que faltan, pendientes). */
function avance(total: number, conteos: Partial<Record<EstadoResultado, number>>) {
    const porEstado = Object.fromEntries(ESTADOS_RESULTADO.map((e) => [e, conteos[e] ?? 0])) as Record<EstadoResultado, number>;
    const registrados = Object.values(porEstado).reduce((a, b) => a + b, 0);
    return { total, pendiente: Math.max(total - registrados, 0), ...porEstado };
}

async function sesionOError(env: Env, id: number): Promise<FilaSesion> {
    const s = await env.DB.prepare("SELECT * FROM qa_sessions WHERE id = ?").bind(id).first<FilaSesion>();
    if (!s) throw error(404, "Sesión no encontrada.");
    return s;
}

function exigirAbierta(s: FilaSesion) {
    if (s.status !== "abierta") throw error(409, "La sesión está cerrada: pedile a un admin que la reabra.");
}

export const sesiones = new Hono<AppEnv>();

sesiones.get("/", async (c) => {
    const q = c.req.query();
    const limite = limiteDe(q.limite, 20, 100);
    const estados = consultaLista(q.estado, ["abierta", "cerrada"] as const);
    const entornos = consultaLista(q.entorno, ENTORNOS);
    const where = new Filtros();
    where.en("status", estados);
    where.en("environment", entornos);
    where.en("area", consultaLista(q.area, AREAS));
    if (q.autor) where.agregar("created_by = ? COLLATE NOCASE", q.autor.replace(/^@/, ""));
    if (q.q) where.agregar("(title LIKE ? OR notes LIKE ?)", `%${q.q}%`, `%${q.q}%`);
    if (Number(q.antes)) where.agregar("id < ?", Number(q.antes));

    const { results } = await c.env.DB.prepare(`SELECT * FROM qa_sessions ${where.sql()} ORDER BY id DESC LIMIT ?`)
        .bind(...where.valores, limite + 1)
        .all<FilaSesion>();
    const pagina = results.slice(0, limite);
    const conteos = new Map<number, Partial<Record<EstadoResultado, number>>>();
    if (pagina.length) {
        const { results: filas } = await c.env.DB.prepare(
            `SELECT session_id, status, COUNT(*) AS n FROM qa_results
             WHERE session_id IN (${pagina.map(() => "?").join(",")}) GROUP BY session_id, status`,
        )
            .bind(...pagina.map((s) => s.id))
            .all<{ session_id: number; status: EstadoResultado; n: number }>();
        for (const f of filas) conteos.set(f.session_id, { ...conteos.get(f.session_id), [f.status]: f.n });
    }
    const casos = await cargarCasos(c.env);
    return c.json({
        sesiones: pagina.map((s) => ({ ...sesionJson(s), avance: avance(casosAplicables(casos, s).length, conteos.get(s.id) ?? {}) })),
        siguiente: results.length > limite ? pagina.at(-1)!.id : null,
    });
});

sesiones.post("/", requiereRol("qa"), async (c) => {
    const datos = await leerBody(c, altaSchema);
    const yo = c.get("usuario").github;
    const s = await c.env.DB.prepare(
        "INSERT INTO qa_sessions (title, area, environment, base_url, backend_version, notes, created_by) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING *",
    )
        .bind(datos.titulo, datos.area, datos.entorno, datos.url, datos.version ?? null, datos.notas ?? null, yo)
        .first<FilaSesion>();
    await auditar(c.env, yo, "sesion.crear", { id: s!.id, titulo: datos.titulo, area: datos.area, entorno: datos.entorno });
    return c.json({ ...sesionJson(s!), avance: avance(casosAplicables(await cargarCasos(c.env), s!).length, {}) }, 201);
});

sesiones.get("/:id", async (c) => {
    const s = await sesionOError(c.env, idDe(c.req.param("id"), "Sesión"));
    const q = c.req.query();
    const { results } = await c.env.DB.prepare("SELECT * FROM qa_results WHERE session_id = ?").bind(s.id).all<FilaResultado>();
    const porCaso = new Map(results.map((r) => [r.case_id, r]));
    const conteos: Partial<Record<EstadoResultado, number>> = {};
    for (const r of results) conteos[r.status] = (conteos[r.status] ?? 0) + 1;

    const filtroResultado = consultaLista(q.resultado, ["pendiente", ...ESTADOS_RESULTADO] as const);
    const casos = await cargarCasos(c.env);
    // Los casos vigentes del área y, si ya tienen resultado en esta sesión, también los archivados
    const delaSesion = casos.lista.filter((k) => k.area === s.area && (k.activo || porCaso.has(k.id)));
    const casosSesion = filtrarCasos(
        {
            seccion: q.seccion,
            etiqueta: q.etiqueta,
            q: q.q,
            destructivo: consultaBooleana(q.destructivo),
            mock: consultaBooleana(q.mock),
            ia_real: consultaBooleana(q.ia_real),
        },
        delaSesion,
    )
        .map((caso) => {
            const r = porCaso.get(caso.id);
            return {
                caso,
                aplica: aplica(caso, s.environment),
                resultado: r
                    ? { id: r.id, estado: r.status, notas: r.notes, actualizado_por: r.updated_by, actualizado_en: r.updated_at }
                    : null,
            };
        })
        .filter((x) => !filtroResultado.length || filtroResultado.includes(x.resultado?.estado ?? "pendiente"));

    return c.json({ sesion: sesionJson(s), avance: avance(casosAplicables(casos, s).length, conteos), casos: casosSesion });
});

sesiones.patch("/:id", requiereRol("qa"), async (c) => {
    const s = await sesionOError(c.env, idDe(c.req.param("id"), "Sesión"));
    exigirAbierta(s);
    const datos = await leerBody(c, cambioSchema);
    const actualizada = await c.env.DB.prepare(
        `UPDATE qa_sessions SET title = COALESCE(?, title),
             backend_version = CASE WHEN ? THEN ? ELSE backend_version END,
             notes = CASE WHEN ? THEN ? ELSE notes END
         WHERE id = ? RETURNING *`,
    )
        .bind(
            datos.titulo ?? null,
            Number("version" in datos), datos.version ?? null,
            Number("notas" in datos), datos.notas ?? null,
            s.id,
        )
        .first<FilaSesion>();
    return c.json(sesionJson(actualizada!));
});

async function cambiarEstado(c: Context<AppEnv>, estado: "abierta" | "cerrada") {
    const s = await sesionOError(c.env, idDe(c.req.param("id")!, "Sesión"));
    if (s.status === estado) throw error(409, `La sesión ya está ${estado}.`);
    const yo = c.get("usuario").github;
    const consulta =
        estado === "cerrada"
            ? c.env.DB.prepare(
                  "UPDATE qa_sessions SET status = 'cerrada', closed_by = ?, closed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ? RETURNING *",
              ).bind(yo, s.id)
            : c.env.DB.prepare("UPDATE qa_sessions SET status = 'abierta', closed_by = NULL, closed_at = NULL WHERE id = ? RETURNING *").bind(s.id);
    const actualizada = await consulta.first<FilaSesion>();
    await auditar(c.env, yo, estado === "cerrada" ? "sesion.cerrar" : "sesion.reabrir", { id: s.id });
    return c.json(sesionJson(actualizada!));
}

sesiones.get("/:id/publicacion", async (c) => c.json(await planDeSesion(c.env, idDe(c.req.param("id"), "Sesión"))));

sesiones.post("/:id/publicar", requiereRol("qa"), limitePublicar, async (c) =>
    c.json(await publicarSesion(c.env, idDe(c.req.param("id"), "Sesión"), c.get("usuario").github, new URL(c.req.url).origin)),
);

sesiones.post("/:id/cerrar", requiereRol("qa"), (c) => cambiarEstado(c, "cerrada"));
sesiones.post("/:id/reabrir", requiereRol("admin"), (c) => cambiarEstado(c, "abierta"));

async function casoDeLaSesion(env: Env, s: FilaSesion, casoId: string): Promise<CasoQA> {
    const caso = (await cargarCasos(env)).porId.get(casoId);
    if (!caso) throw error(404, `El caso ${casoId} no está en el catálogo.`);
    if (caso.area !== s.area) throw error(409, `${caso.id} es de ${caso.area} y la sesión es de ${s.area}.`);
    if (!caso.activo) throw error(409, `${caso.id} está archivado.`);
    if (!aplica(caso, s.environment)) throw error(409, `${caso.id} es destructivo: solo se prueba en local o staging.`);
    return caso;
}

sesiones.put("/:id/resultados/:caso", requiereRol("qa"), async (c) => {
    const s = await sesionOError(c.env, idDe(c.req.param("id"), "Sesión"));
    exigirAbierta(s);
    const caso = await casoDeLaSesion(c.env, s, c.req.param("caso"));
    const datos = await leerBody(c, resultadoSchema);
    const r = await c.env.DB.prepare(
        `INSERT INTO qa_results (session_id, case_id, status, notes, updated_by) VALUES (?, ?, ?, ?, ?)
         ON CONFLICT (session_id, case_id) DO UPDATE SET status = excluded.status, notes = excluded.notes,
             updated_by = excluded.updated_by, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
         RETURNING *`,
    )
        .bind(s.id, caso.id, datos.estado, datos.notas ?? null, c.get("usuario").github)
        .first<FilaResultado>();
    return c.json({ id: r!.id, caso_id: caso.id, estado: r!.status, notas: r!.notes, actualizado_por: r!.updated_by, actualizado_en: r!.updated_at });
});

sesiones.delete("/:id/resultados/:caso", requiereRol("qa"), async (c) => {
    const s = await sesionOError(c.env, idDe(c.req.param("id"), "Sesión"));
    exigirAbierta(s);
    const r = await c.env.DB.prepare("SELECT id FROM qa_results WHERE session_id = ? AND case_id = ?")
        .bind(s.id, c.req.param("caso"))
        .first<{ id: number }>();
    if (!r) throw error(404, "Ese caso todavía no tiene resultado en la sesión.");
    // Las evidencias no se pierden por volver a pendiente: se cambia el resultado en lugar de borrarlo
    const conEvidencia = await c.env.DB.prepare("SELECT 1 FROM qa_evidence WHERE result_id = ? LIMIT 1").bind(r.id).first();
    if (conEvidencia) throw error(409, "El resultado tiene evidencias: cambialo a otro estado en lugar de volverlo a pendiente.");
    // Sus comentarios se van con él
    await c.env.DB.batch([
        c.env.DB.prepare("DELETE FROM qa_comments WHERE target_type = 'resultado' AND target_id = ?").bind(r.id),
        c.env.DB.prepare("DELETE FROM qa_results WHERE id = ?").bind(r.id),
    ]);
    return c.json({ caso_id: c.req.param("caso"), estado: "pendiente" });
});
