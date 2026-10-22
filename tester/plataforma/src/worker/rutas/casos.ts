// Casos de prueba: los del repo (área backend, solo lectura) y los manuales (cualquier área).
//
// - GET    /api/casos?area=&seccion=&etiqueta=&q=&destructivo=&mock=&ia_real=&archivados=   (cualquier rol)
// - GET    /api/casos/secciones?area=   → secciones o casos de uso del área (para los filtros)
// - POST   /api/casos                   → caso manual nuevo (qa); el id lo asigna la plataforma
// - GET    /api/casos/:id               → el caso, su historial en todas las sesiones y sus bugs
// - PATCH  /api/casos/:id               → editar un caso manual (qa)
// - POST   /api/casos/:id/archivar | /reactivar   (qa): los casos no se borran (sus resultados y bugs los nombran)
import { Hono } from "hono";
import { z } from "zod";
import { SEVERIDADES } from "../../catalogo/esquema.ts";
import { auditar } from "../auditoria";
import { requiereRol, type AppEnv } from "../auth";
import { filtrarCasos } from "../catalogo";
import { AREAS, cargarCasos, casoDeFila, obtenerCaso, proximoId, seccionesDelArea, type Area, type FilaCaso } from "../casos";
import { consultaBooleana, error, leerBody } from "../validar";

const texto = (max: number) => z.string().trim().min(1, "No puede quedar vacío.").max(max, `Hasta ${max} caracteres.`);
const lista = (max: number) => z.array(z.string().trim().min(1).max(max)).max(50);

const campos = {
    seccion: texto(120),
    titulo: texto(200),
    ubicacion: z.string().trim().max(300).nullish().transform((v) => v || null),
    precondiciones: lista(500).default([]),
    pasos: lista(1000).min(1, "Agregá al menos un paso."),
    datos: z.string().trim().max(5000).nullish().transform((v) => v || null),
    esperado: texto(5000),
    severidad: z.enum(SEVERIDADES),
    etiquetas: z.array(z.string().trim().toLowerCase().regex(/^[a-z0-9-]+$/, "Etiquetas en minúsculas, con guiones.")).max(20).default([]),
    mock: z.boolean().default(false),
    ia_real: z.boolean().default(false),
    destructivo: z.boolean().default(false),
};

const altaSchema = z.strictObject({ area: z.enum(AREAS), ...campos }).refine((c) => !(c.mock && c.ia_real), "Un caso no puede ser mock e IA real a la vez.");
const cambioSchema = z
    .strictObject(Object.fromEntries(Object.entries(campos).map(([k, v]) => [k, (v as z.ZodType).optional()])) as { [K in keyof typeof campos]: z.ZodOptional<(typeof campos)[K]> })
    .refine((c) => Object.values(c).some((v) => v !== undefined), "No hay nada para cambiar.");

function areaDe(valor: string | undefined): Area | undefined {
    return (AREAS as readonly string[]).includes(valor ?? "") ? (valor as Area) : undefined;
}

export const casos = new Hono<AppEnv>();

casos.get("/", async (c) => {
    const q = c.req.query();
    const todos = await cargarCasos(c.env);
    const area = areaDe(q.area);
    const conArchivados = q.archivados === "true";
    const base = todos.lista.filter((k) => (!area || k.area === area) && (conArchivados || k.activo));
    const filtrados = filtrarCasos(
        { seccion: q.seccion, etiqueta: q.etiqueta, q: q.q, destructivo: consultaBooleana(q.destructivo), mock: consultaBooleana(q.mock), ia_real: consultaBooleana(q.ia_real) },
        base,
    );
    return c.json({ total: filtrados.length, casos: filtrados });
});

casos.get("/secciones", async (c) => {
    const area = areaDe(c.req.query("area")) ?? "backend";
    return c.json({ secciones: seccionesDelArea(await cargarCasos(c.env), area) });
});

casos.post("/", requiereRol("qa"), async (c) => {
    const d = await leerBody(c, altaSchema);
    const yo = c.get("usuario").github;
    // Dos altas a la vez pueden pedir el mismo id: se reintenta con el siguiente
    for (let intento = 0; intento < 3; intento++) {
        const id = await proximoId(c.env, d.area);
        const fila = await c.env.DB.prepare(
            `INSERT INTO qa_cases (id, area, section, title, location, preconditions, steps, test_data, expected, severity, tags, mock, ia_real, destructive, created_by)
             VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO NOTHING RETURNING *`,
        )
            .bind(
                id, d.area, d.seccion, d.titulo, d.ubicacion, JSON.stringify(d.precondiciones), JSON.stringify(d.pasos), d.datos, d.esperado, d.severidad,
                JSON.stringify(d.etiquetas), Number(d.mock), Number(d.ia_real), Number(d.destructivo), yo,
            )
            .first<FilaCaso>();
        if (fila) {
            await auditar(c.env, yo, "caso.crear", { id, area: d.area });
            return c.json(casoDeFila(fila), 201);
        }
    }
    throw error(409, "No se pudo asignar un id al caso. Probá de nuevo.");
});

casos.get("/:id", async (c) => {
    const caso = await obtenerCaso(c.env, c.req.param("id"));
    if (!caso) throw error(404, "Caso no encontrado.");
    const [historial, bugs] = await c.env.DB.batch([
        c.env.DB.prepare(
            `SELECT r.id, r.status, r.notes, r.updated_by, r.updated_at,
                    s.id AS session_id, s.title, s.environment, s.backend_version
             FROM qa_results r JOIN qa_sessions s ON s.id = r.session_id
             WHERE r.case_id = ? ORDER BY r.updated_at DESC`,
        ).bind(caso.id),
        c.env.DB.prepare("SELECT id, title, status, severity, issue_number, issue_url, created_at FROM qa_bugs WHERE case_id = ? ORDER BY id DESC").bind(caso.id),
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

async function casoManual(c: { env: AppEnv["Bindings"] }, id: string) {
    const caso = await obtenerCaso(c.env, id);
    if (!caso) throw error(404, "Caso no encontrado.");
    if (!caso.manual) throw error(409, "Los casos del repo se editan en tester/casos con un PR.");
    return caso;
}

casos.patch("/:id", requiereRol("qa"), async (c) => {
    const caso = await casoManual(c, c.req.param("id"));
    const d = await leerBody(c, cambioSchema);
    const mock = d.mock ?? caso.mock;
    const iaReal = d.ia_real ?? caso.ia_real;
    if (mock && iaReal) throw error(422, "Un caso no puede ser mock e IA real a la vez.");
    const json = (v: unknown) => (v === undefined ? null : JSON.stringify(v));
    const fila = await c.env.DB.prepare(
        `UPDATE qa_cases SET section = COALESCE(?, section), title = COALESCE(?, title),
             location = CASE WHEN ? THEN ? ELSE location END,
             preconditions = COALESCE(?, preconditions), steps = COALESCE(?, steps),
             test_data = CASE WHEN ? THEN ? ELSE test_data END,
             expected = COALESCE(?, expected), severity = COALESCE(?, severity), tags = COALESCE(?, tags),
             mock = ?, ia_real = ?, destructive = COALESCE(?, destructive),
             updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
         WHERE id = ? RETURNING *`,
    )
        .bind(
            d.seccion ?? null, d.titulo ?? null,
            Number(d.ubicacion !== undefined), d.ubicacion ?? null,
            json(d.precondiciones), json(d.pasos),
            Number(d.datos !== undefined), d.datos ?? null,
            d.esperado ?? null, d.severidad ?? null, json(d.etiquetas),
            Number(mock), Number(iaReal), d.destructivo === undefined ? null : Number(d.destructivo),
            caso.id,
        )
        .first<FilaCaso>();
    await auditar(c.env, c.get("usuario").github, "caso.editar", { id: caso.id });
    return c.json(casoDeFila(fila!));
});

for (const [ruta, activo] of [["archivar", 0], ["reactivar", 1]] as const) {
    casos.post(`/:id/${ruta}`, requiereRol("qa"), async (c) => {
        const caso = await casoManual(c, c.req.param("id"));
        const fila = await c.env.DB.prepare("UPDATE qa_cases SET active = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ? RETURNING *")
            .bind(activo, caso.id)
            .first<FilaCaso>();
        await auditar(c.env, c.get("usuario").github, `caso.${ruta}`, { id: caso.id });
        return c.json(casoDeFila(fila!));
    });
}
