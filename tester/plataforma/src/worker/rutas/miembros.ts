// Miembros de la plataforma (solo admins): quién entra y con qué rol.
//
// - GET    /api/miembros            → admins fijos (QA_ADMINS) + qa_members
// - POST   /api/miembros            → { github, rol }
// - PATCH  /api/miembros/:github    → { rol?, habilitado? }
// - DELETE /api/miembros/:github
//
// Nadie cambia su propio acceso ni el de un admin fijo (se cambia en wrangler.toml).
import { Hono } from "hono";
import { z } from "zod";
import { auditar } from "../auditoria";
import { requiereRol, type AppEnv } from "../auth";
import { adminsFijos, esAdminFijo, GITHUB_USERNAME, ROLES } from "../roles";

type FilaMiembro = {
    github_username: string;
    role: string;
    enabled: number;
    added_by: string | null;
    created_at: string;
    updated_at: string;
};

const github = z
    .string()
    .trim()
    .transform((u) => u.replace(/^@/, ""))
    .pipe(z.string().regex(GITHUB_USERNAME, "No es un usuario de GitHub válido."));

const altaSchema = z.strictObject({ github, rol: z.enum(ROLES) });
const cambioSchema = z
    .strictObject({ rol: z.enum(ROLES).optional(), habilitado: z.boolean().optional() })
    .refine((c) => c.rol !== undefined || c.habilitado !== undefined, "Mandá rol o habilitado.");

export const miembros = new Hono<AppEnv>();

miembros.use("*", requiereRol("admin"));

async function listado(c: { env: AppEnv["Bindings"] }) {
    const { results } = await c.env.DB.prepare(
        `SELECT * FROM qa_members
         ORDER BY CASE role WHEN 'admin' THEN 0 WHEN 'qa' THEN 1 ELSE 2 END, github_username COLLATE NOCASE`,
    ).all<FilaMiembro>();
    return {
        fijos: adminsFijos(c.env).map((u) => ({ github: u, rol: "admin" })),
        miembros: results.map((m) => ({
            github: m.github_username,
            rol: m.role,
            habilitado: Boolean(m.enabled),
            agregado_por: m.added_by,
            creado_en: m.created_at,
            actualizado_en: m.updated_at,
        })),
    };
}

function primerError(error: z.ZodError): string {
    return error.issues[0]?.message ?? "Datos inválidos.";
}

miembros.get("/", async (c) => c.json(await listado(c)));

miembros.post("/", async (c) => {
    const body = altaSchema.safeParse(await c.req.json().catch(() => null));
    if (!body.success) return c.json({ error: primerError(body.error) }, 422);
    const { github: usuario, rol } = body.data;
    if (esAdminFijo(c.env, usuario)) return c.json({ error: `@${usuario} ya es admin fijo (QA_ADMINS).` }, 409);
    const yo = c.get("usuario").github;
    const { meta } = await c.env.DB.prepare(
        "INSERT INTO qa_members (github_username, role, added_by) VALUES (?, ?, ?) ON CONFLICT DO NOTHING",
    )
        .bind(usuario, rol, yo)
        .run();
    if (!meta.changes) return c.json({ error: `@${usuario} ya tiene acceso.` }, 409);
    await auditar(c.env, yo, "miembro.agregar", { github: usuario, rol });
    return c.json(await listado(c), 201);
});

miembros.patch("/:github", async (c) => {
    const usuario = c.req.param("github");
    const body = cambioSchema.safeParse(await c.req.json().catch(() => null));
    if (!body.success) return c.json({ error: primerError(body.error) }, 422);
    const yo = c.get("usuario").github;
    if (usuario.toLowerCase() === yo.toLowerCase()) {
        return c.json({ error: "No podés cambiar tu propio acceso: pedíselo a otro admin." }, 409);
    }
    if (esAdminFijo(c.env, usuario)) return c.json({ error: "Los admins fijos se cambian en QA_ADMINS (wrangler.toml)." }, 409);
    const { rol, habilitado } = body.data;
    const { meta } = await c.env.DB.prepare(
        `UPDATE qa_members SET role = COALESCE(?, role), enabled = COALESCE(?, enabled),
             updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
         WHERE github_username = ?`,
    )
        .bind(rol ?? null, habilitado === undefined ? null : Number(habilitado), usuario)
        .run();
    if (!meta.changes) return c.json({ error: "Miembro no encontrado." }, 404);
    await auditar(c.env, yo, "miembro.cambiar", { github: usuario, ...body.data });
    return c.json(await listado(c));
});

miembros.delete("/:github", async (c) => {
    const usuario = c.req.param("github");
    const yo = c.get("usuario").github;
    if (usuario.toLowerCase() === yo.toLowerCase()) {
        return c.json({ error: "No podés quitarte el acceso: pedíselo a otro admin." }, 409);
    }
    const { meta } = await c.env.DB.prepare("DELETE FROM qa_members WHERE github_username = ?").bind(usuario).run();
    if (!meta.changes) return c.json({ error: "Miembro no encontrado." }, 404);
    await auditar(c.env, yo, "miembro.quitar", { github: usuario });
    return c.json(await listado(c));
});
