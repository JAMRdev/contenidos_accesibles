// Comentarios sobre un resultado o un bug (tabla qa_comments).
import { z } from "zod";
import type { Env } from "./env";

export type Objetivo = "resultado" | "bug";

export const comentarioSchema = z.strictObject({
    texto: z.string().trim().min(1, "Escribí el comentario.").max(10000, "Hasta 10.000 caracteres."),
    // Publicarlo también en el issue de GitHub (se envía al publicar, #130)
    en_github: z.boolean().default(false),
});

type Fila = {
    id: number;
    author: string;
    body: string;
    origin: string;
    to_github: number;
    github_comment_id: number | null;
    created_at: string;
};

export async function listarComentarios(env: Env, tipo: Objetivo, id: number) {
    const { results } = await env.DB.prepare(
        "SELECT * FROM qa_comments WHERE target_type = ? AND target_id = ? ORDER BY id",
    )
        .bind(tipo, id)
        .all<Fila>();
    return results.map((c) => ({
        id: c.id,
        autor: c.author,
        texto: c.body,
        origen: c.origin,
        en_github: Boolean(c.to_github),
        publicado_en_github: c.github_comment_id !== null,
        creado_en: c.created_at,
    }));
}

export async function crearComentario(env: Env, tipo: Objetivo, id: number, autor: string, datos: z.output<typeof comentarioSchema>) {
    await env.DB.prepare("INSERT INTO qa_comments (target_type, target_id, author, body, to_github) VALUES (?, ?, ?, ?, ?)")
        .bind(tipo, id, autor, datos.texto, Number(datos.en_github))
        .run();
    return listarComentarios(env, tipo, id);
}
