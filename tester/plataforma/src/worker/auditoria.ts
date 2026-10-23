// Registro de auditoría (tabla qa_audit): quién hizo qué y cuándo.
import type { Env } from "./env";

export type EntradaAuditoria = {
    id: number;
    creado_en: string;
    actor: string | null;
    accion: string;
    detalle: unknown;
};

export async function auditar(env: Env, actor: string | null, accion: string, detalle?: unknown): Promise<void> {
    await env.DB.prepare("INSERT INTO qa_audit (actor, action, detail) VALUES (?, ?, ?)")
        .bind(actor, accion, detalle === undefined ? null : JSON.stringify(detalle))
        .run();
}

/** Las más recientes primero; `antesDe` (un id) pagina hacia atrás. */
export async function listarAuditoria(env: Env, limite: number, antesDe?: number): Promise<EntradaAuditoria[]> {
    const { results } = await env.DB.prepare(
        `SELECT id, created_at, actor, action, detail FROM qa_audit
         WHERE (?1 IS NULL OR id < ?1) ORDER BY id DESC LIMIT ?2`,
    )
        .bind(antesDe ?? null, limite)
        .all<{ id: number; created_at: string; actor: string | null; action: string; detail: string | null }>();
    return results.map((r) => ({
        id: r.id,
        creado_en: r.created_at,
        actor: r.actor,
        accion: r.action,
        detalle: r.detail ? JSON.parse(r.detail) : null,
    }));
}
