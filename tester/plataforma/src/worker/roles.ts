// Roles de la plataforma de QA (Issue #126), de menor a mayor:
// - viewer: ve sesiones, resultados, bugs y evidencias (para abrir los enlaces de los issues).
// - qa: además crea sesiones, registra resultados, sube evidencias, comenta, cambia estados y publica.
// - admin: además administra miembros, ve la auditoría y borra evidencias.
// Los admins de QA_ADMINS (wrangler.toml) son admin siempre; el resto sale de qa_members.
import type { Env } from "./env";

export const ROLES = ["viewer", "qa", "admin"] as const;
export type Rol = (typeof ROLES)[number];

// Usuario de GitHub: letras, números y guiones simples, sin guion al inicio ni al final, hasta 39
export const GITHUB_USERNAME = /^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$/;

export function alcanza(rol: Rol, minimo: Rol): boolean {
    return ROLES.indexOf(rol) >= ROLES.indexOf(minimo);
}

export function adminsFijos(env: Env): string[] {
    return (env.QA_ADMINS ?? "")
        .split(",")
        .map((u) => u.trim().replace(/^@/, ""))
        .filter((u) => GITHUB_USERNAME.test(u));
}

export function esAdminFijo(env: Env, github: string): boolean {
    return adminsFijos(env).some((u) => u.toLowerCase() === github.toLowerCase());
}

/** Rol vigente de una cuenta de GitHub, o null si no tiene acceso (o está suspendida). */
export async function rolDe(env: Env, github: string): Promise<Rol | null> {
    if (esAdminFijo(env, github)) return "admin";
    const fila = await env.DB.prepare("SELECT role FROM qa_members WHERE github_username = ? AND enabled = 1")
        .bind(github)
        .first<{ role: Rol }>();
    return fila?.role ?? null;
}
