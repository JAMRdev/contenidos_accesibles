// Comentarios sobre el resultado de un caso en una sesión.
//
// - GET  /api/resultados/:id/comentarios   (cualquier rol)
// - POST /api/resultados/:id/comentarios   (qa)
import { Hono } from "hono";
import { requiereRol, type AppEnv } from "../auth";
import { comentarioSchema, crearComentario, listarComentarios } from "../comentarios";
import type { Env } from "../env";
import { error, idDe, leerBody } from "../validar";

async function resultadoOError(env: Env, valor: string): Promise<number> {
    const id = idDe(valor, "Resultado");
    const fila = await env.DB.prepare("SELECT id FROM qa_results WHERE id = ?").bind(id).first<{ id: number }>();
    if (!fila) throw error(404, "Resultado no encontrado.");
    return id;
}

export const resultados = new Hono<AppEnv>();

resultados.get("/:id/comentarios", async (c) => {
    const id = await resultadoOError(c.env, c.req.param("id"));
    return c.json({ comentarios: await listarComentarios(c.env, "resultado", id) });
});

resultados.post("/:id/comentarios", requiereRol("qa"), async (c) => {
    const id = await resultadoOError(c.env, c.req.param("id"));
    const datos = await leerBody(c, comentarioSchema);
    return c.json({ comentarios: await crearComentario(c.env, "resultado", id, c.get("usuario").github, datos) }, 201);
});
