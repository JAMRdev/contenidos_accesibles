// Fila de qa_bugs y los datos que necesita su issue (reporte y evidencias). Lo usan las rutas de
// bugs (vista previa) y la publicación en GitHub (publicar.ts).
import type { SEVERIDADES } from "../catalogo/esquema.ts";
import type { Env } from "./env";
import type { DatosReporte } from "./reporte";

export const ESTADOS_BUG = ["borrador", "abierto", "en_curso", "resuelto", "verificado", "reabierto", "descartado"] as const;
export type EstadoBug = (typeof ESTADOS_BUG)[number];

export type FilaBug = {
    id: number;
    case_id: string;
    section: string;
    area: "backend" | "frontend";
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
    steps: string;
    expected: string;
    actual: string;
    tested_url: string | null;
    backend_version: string | null;
};

/** Datos del reporte y evidencias (del bug y del resultado del caso en su sesión) para armar el issue. */
export async function datosDelIssue(env: Env, b: FilaBug) {
    const { results: evidencias } = await env.DB.prepare(
        `SELECT id, label, filename, kind FROM qa_evidence
         WHERE bug_id = ?1 OR result_id = (SELECT id FROM qa_results WHERE session_id = ?2 AND case_id = ?3)
         ORDER BY id`,
    )
        .bind(b.id, b.session_id, b.case_id)
        .all<{ id: number; label: string | null; filename: string; kind: string }>();
    const reporte: DatosReporte = {
        id: b.id,
        titulo: b.title,
        severidad: b.severity,
        entorno: b.environment as DatosReporte["entorno"],
        url: b.tested_url,
        version: b.backend_version,
        pasos: b.steps,
        esperado: b.expected,
        obtenido: b.actual,
        notas: b.description,
        sesion_id: b.session_id,
        creado_por: b.created_by,
    };
    return { reporte, evidencias: evidencias.map((e) => ({ id: e.id, etiqueta: e.label, nombre: e.filename, tipo: e.kind })) };
}
