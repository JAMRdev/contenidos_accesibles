// Sesión y permisos de la API (/api/*, Issue #126).
//
// Toda la API, menos /api/config, exige `Authorization: Bearer <token de sesión de Clerk>` y una
// cuenta de GitHub con rol. Sin token o con uno inválido: 401. Sin rol: 403 (queda en la auditoría).
// Además, cada usuario tiene un límite de requests por minuto (binding LIMITE_API).
import { createMiddleware } from "hono/factory";
import { auditar } from "./auditoria";
import { AuthError, identificar, verificarToken, type Identidad } from "./clerk";
import type { Env } from "./env";
import { alcanza, rolDe, type Rol } from "./roles";

export type Usuario = Identidad & { rol: Rol };

export type AppEnv = { Bindings: Env; Variables: { usuario: Usuario } };

export const requiereSesion = createMiddleware<AppEnv>(async (c, next) => {
    const [esquema, token] = (c.req.header("Authorization") ?? "").split(" ");
    if (esquema !== "Bearer" || !token) {
        return c.json({ error: "Iniciá sesión para usar la plataforma de QA." }, 401);
    }
    let identidad: Identidad;
    try {
        const sub = await verificarToken(c.env, token, new URL(c.req.url).origin);
        identidad = await identificar(c.env, sub);
    } catch (e) {
        if (e instanceof AuthError) return c.json({ error: e.message }, e.status);
        throw e;
    }

    const rol = await rolDe(c.env, identidad.github);
    if (!rol) {
        await auditar(c.env, identidad.github, "acceso.denegado", { ruta: c.req.path });
        return c.json(
            { error: `La cuenta de GitHub @${identidad.github} no tiene acceso a la plataforma de QA. Pedile acceso a un admin.` },
            403,
        );
    }

    const { success } = await c.env.LIMITE_API.limit({ key: identidad.github.toLowerCase() });
    if (!success) {
        c.header("Retry-After", "60");
        return c.json({ error: "Demasiados pedidos seguidos. Esperá un minuto y probá de nuevo." }, 429);
    }

    c.set("usuario", { ...identidad, rol });
    await next();
});

const DESCRIPCION: Record<Rol, string> = { viewer: "solo lectura", qa: "QA", admin: "admin" };

export function requiereRol(minimo: Rol) {
    return createMiddleware<AppEnv>(async (c, next) => {
        const { rol } = c.get("usuario");
        if (!alcanza(rol, minimo)) {
            return c.json({ error: `Tu rol (${DESCRIPCION[rol]}) no permite esta acción: hace falta ${DESCRIPCION[minimo]}.` }, 403);
        }
        await next();
    });
}
