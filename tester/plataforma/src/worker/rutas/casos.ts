// Catálogo de casos (cualquier rol).
//
// - GET /api/casos?seccion=&etiqueta=&q=&destructivo=&mock=&ia_real=
// - GET /api/casos/secciones  → secciones del catálogo (para los filtros)
// - GET /api/casos/:id  → el caso, su historial en todas las sesiones y sus bugs
import { Hono } from "hono";
import type { AppEnv } from "../auth";
import { buscarCaso, catalogo, filtrarCasos } from "../catalogo";
import { consultaBooleana, error } from "../validar";

export const casos = new Hono<AppEnv>();

casos.get("/", (c) => {
    const q = c.req.query();
    const lista = filtrarCasos({
        seccion: q.seccion,
        etiqueta: q.etiqueta,
        q: q.q,
        destructivo: consultaBooleana(q.destructivo),
        mock: consultaBooleana(q.mock),
        ia_real: consultaBooleana(q.ia_real),
    });
    return c.json({ secciones: catalogo.secciones, total: lista.length, casos: lista });
});

casos.get("/secciones", (c) => c.json({ secciones: catalogo.secciones }));

casos.get("/:id", async (c) => {
    const caso = buscarCaso(c.req.param("id"));
    if (!caso) throw error(404, "Caso no encontrado.");
    const [historial, bugs] = await c.env.DB.batch([
        c.env.DB.prepare(
            `SELECT r.id, r.status, r.notes, r.updated_by, r.updated_at,
                    s.id AS session_id, s.title, s.environment, s.backend_version
             FROM qa_results r JOIN qa_sessions s ON s.id = r.session_id
             WHERE r.case_id = ? ORDER BY r.updated_at DESC`,
        ).bind(caso.id),
        c.env.DB.prepare(
            "SELECT id, title, status, severity, issue_number, issue_url, created_at FROM qa_bugs WHERE case_id = ? ORDER BY id DESC",
        ).bind(caso.id),
    ]);
    return c.json({
        caso,
        historial: (historial.results as Record<string, unknown>[]).map((r) => ({
            resultado_id: r.id,
            estado: r.status,
            notas: r.notes,
            actualizado_por: r.updated_by,
            actualizado_en: r.updated_at,
            sesion: { id: r.session_id, titulo: r.title, entorno: r.environment, version: r.backend_version },
        })),
        bugs: (bugs.results as Record<string, unknown>[]).map((b) => ({
            id: b.id,
            titulo: b.title,
            estado: b.status,
            severidad: b.severity,
            issue: b.issue_number ? { numero: b.issue_number, url: b.issue_url } : null,
            creado_en: b.created_at,
        })),
    });
});
