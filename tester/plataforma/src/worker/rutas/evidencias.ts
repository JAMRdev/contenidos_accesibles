// Evidencias de resultados y bugs en el R2 de QA (Issue #128).
//
// - GET    /api/evidencias?resultado=|bug=     → evidencias de un resultado o un bug (cualquier rol)
// - POST   /api/evidencias?resultado=|bug=     → sube un archivo (qa). Body: el archivo;
//          headers Content-Type y X-Nombre (nombre del archivo, URL-encoded), X-Etiqueta opcional.
// - POST   /api/evidencias/texto               → { resultado_id | bug_id, etiqueta, texto } (qa)
// - GET    /api/evidencias/uso                 → cuántas hay y cuánto ocupan (admin)
// - GET    /api/evidencias/:id                 → metadatos (cualquier rol)
// - GET    /api/evidencias/:id/archivo         → el archivo (cualquier rol)
// - DELETE /api/evidencias/:id                 → borra archivo y metadatos (admin)
//
// Las imágenes, los videos y los PDF se validan por su firma (no alcanza con la extensión). El texto se
// guarda con los datos sensibles ocultos (redactar.ts). Desde GitHub se enlaza la página
// /evidencias/:id de la web, que pide login: el repo es público y las evidencias no.
import { Hono } from "hono";
import { z } from "zod";
import { auditar } from "../auditoria";
import { requiereRol, type AppEnv } from "../auth";
import type { Env } from "../env";
import { redactar } from "../redactar";
import { error, idDe, leerBody } from "../validar";

type Tipo = "imagen" | "video" | "texto" | "pdf";

const MB = 1024 * 1024;
const NOMBRE_TIPO: Record<Tipo, string> = { imagen: "imágenes", video: "videos", texto: "texto", pdf: "PDF" };
export const LIMITES: Record<Tipo, number> = { imagen: 10 * MB, video: 50 * MB, texto: 2 * MB, pdf: 10 * MB };

// Content-Type permitido → tipo de evidencia
const TIPOS: Record<string, Tipo> = {
    "image/png": "imagen",
    "image/jpeg": "imagen",
    "image/webp": "imagen",
    "image/gif": "imagen",
    "video/mp4": "video",
    "video/webm": "video",
    "application/pdf": "pdf",
    "application/json": "texto",
    "text/plain": "texto",
    "application/har+json": "texto",
};

const hex = (bytes: Uint8Array, desde: number, hasta: number) =>
    [...bytes.slice(desde, hasta)].map((b) => b.toString(16).padStart(2, "0")).join("");

/** ¿Los primeros bytes corresponden al Content-Type declarado? */
export function firmaValida(contentType: string, bytes: Uint8Array): boolean {
    switch (contentType) {
        case "image/png":
            return hex(bytes, 0, 8) === "89504e470d0a1a0a";
        case "image/jpeg":
            return hex(bytes, 0, 3) === "ffd8ff";
        case "image/gif":
            return new TextDecoder().decode(bytes.slice(0, 4)) === "GIF8";
        case "image/webp":
            return new TextDecoder().decode(bytes.slice(0, 4)) === "RIFF" && new TextDecoder().decode(bytes.slice(8, 12)) === "WEBP";
        case "video/mp4":
            return new TextDecoder().decode(bytes.slice(4, 8)) === "ftyp";
        case "video/webm":
            return hex(bytes, 0, 4) === "1a45dfa3";
        case "application/pdf":
            return new TextDecoder().decode(bytes.slice(0, 5)) === "%PDF-";
        default:
            return true;
    }
}

type FilaEvidencia = {
    id: number;
    result_id: number | null;
    bug_id: number | null;
    kind: Tipo;
    label: string | null;
    filename: string;
    content_type: string;
    size_bytes: number;
    r2_key: string;
    redactions: number;
    uploaded_by: string;
    created_at: string;
};

function evidenciaJson(e: FilaEvidencia) {
    return {
        id: e.id,
        resultado_id: e.result_id,
        bug_id: e.bug_id,
        tipo: e.kind,
        etiqueta: e.label,
        nombre: e.filename,
        content_type: e.content_type,
        bytes: e.size_bytes,
        datos_ocultos: e.redactions,
        subida_por: e.uploaded_by,
        subida_en: e.created_at,
        url: `/evidencias/${e.id}`,
    };
}

type Destino = { resultado: number | null; bug: number | null };

/** Resultado o bug al que va la evidencia (exactamente uno) y que exista. */
async function destino(env: Env, resultado: unknown, bug: unknown): Promise<Destino> {
    const r = Number(resultado) || null;
    const b = Number(bug) || null;
    if (Boolean(r) === Boolean(b)) throw error(422, "Indicá un resultado o un bug (uno solo).");
    const tabla = r ? "qa_results" : "qa_bugs";
    const fila = await env.DB.prepare(`SELECT id FROM ${tabla} WHERE id = ?`).bind(r ?? b).first();
    if (!fila) throw error(404, r ? "Resultado no encontrado." : "Bug no encontrado.");
    return { resultado: r, bug: b };
}

function nombreSeguro(nombre: string): string {
    return nombre.replace(/[^\w.-]+/g, "_").replace(/^\.+/, "").slice(0, 120) || "evidencia";
}

function claveR2(nombre: string): string {
    const ahora = new Date();
    const mes = `${ahora.getUTCFullYear()}/${String(ahora.getUTCMonth() + 1).padStart(2, "0")}`;
    return `evidencias/${mes}/${crypto.randomUUID()}-${nombreSeguro(nombre)}`;
}

async function guardar(
    env: Env,
    d: Destino,
    datos: { tipo: Tipo; etiqueta: string | null; nombre: string; contentType: string; bytes: number; clave: string; ocultos: number; autor: string },
) {
    const fila = await env.DB.prepare(
        `INSERT INTO qa_evidence (result_id, bug_id, kind, label, filename, content_type, size_bytes, r2_key, redactions, uploaded_by)
         VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) RETURNING *`,
    )
        .bind(d.resultado, d.bug, datos.tipo, datos.etiqueta, datos.nombre, datos.contentType, datos.bytes, datos.clave, datos.ocultos, datos.autor)
        .first<FilaEvidencia>();
    return evidenciaJson(fila!);
}

async function evidenciaOError(env: Env, valor: string): Promise<FilaEvidencia> {
    const e = await env.DB.prepare("SELECT * FROM qa_evidence WHERE id = ?").bind(idDe(valor, "Evidencia")).first<FilaEvidencia>();
    if (!e) throw error(404, "Evidencia no encontrada.");
    return e;
}

const textoSchema = z
    .strictObject({
        resultado_id: z.number().int().positive().optional(),
        bug_id: z.number().int().positive().optional(),
        etiqueta: z.string().trim().min(1, "Elegí qué es (request, response, consola…).").max(60),
        texto: z.string().min(1, "Pegá el texto.").max(LIMITES.texto, "Hasta 2 MB de texto."),
    });

export const evidencias = new Hono<AppEnv>();

evidencias.get("/", async (c) => {
    const d = await destino(c.env, c.req.query("resultado"), c.req.query("bug"));
    const { results } = await c.env.DB.prepare(`SELECT * FROM qa_evidence WHERE ${d.resultado ? "result_id" : "bug_id"} = ? ORDER BY id`)
        .bind(d.resultado ?? d.bug)
        .all<FilaEvidencia>();
    return c.json({ evidencias: results.map(evidenciaJson) });
});

evidencias.post("/", requiereRol("qa"), async (c) => {
    const d = await destino(c.env, c.req.query("resultado"), c.req.query("bug"));
    const contentType = (c.req.header("Content-Type") ?? "").split(";")[0].trim().toLowerCase();
    const tipo = TIPOS[contentType];
    if (!tipo) throw error(422, "Tipo de archivo no admitido: subí PNG, JPEG, WebP, GIF, MP4, WebM, PDF, JSON, HAR o texto.");
    const largo = Number(c.req.header("Content-Length"));
    if (!largo) throw error(422, "El archivo está vacío o falta Content-Length.");
    if (largo > LIMITES[tipo]) throw error(422, `El archivo supera el límite de ${LIMITES[tipo] / MB} MB para ${NOMBRE_TIPO[tipo]}.`);
    const nombre = decodeURIComponent(c.req.header("X-Nombre") ?? "evidencia").slice(0, 200);
    const etiqueta = c.req.header("X-Etiqueta") ? decodeURIComponent(c.req.header("X-Etiqueta")!).slice(0, 60) : null;
    const autor = c.get("usuario").github;
    const clave = claveR2(nombre);

    if (tipo === "texto") {
        // El texto se lee entero para ocultar datos sensibles antes de guardarlo
        const original = await c.req.text();
        if (new TextEncoder().encode(original).byteLength > LIMITES.texto) throw error(422, "El archivo supera el límite de 2 MB para texto.");
        const { texto, ocultos } = redactar(original);
        await c.env.EVIDENCIAS.put(clave, texto, { httpMetadata: { contentType: `${contentType}; charset=utf-8` } });
        const bytes = new TextEncoder().encode(texto).byteLength;
        return c.json(await guardar(c.env, d, { tipo, etiqueta, nombre, contentType, bytes, clave, ocultos, autor }), 201);
    }

    // Imágenes, videos y PDF van directo al bucket (sin cargarlos en memoria) y después se valida la firma
    if (!c.req.raw.body) throw error(422, "El archivo está vacío.");
    const subida = new FixedLengthStream(largo);
    const copia = c.req.raw.body.pipeTo(subida.writable);
    await Promise.all([c.env.EVIDENCIAS.put(clave, subida.readable, { httpMetadata: { contentType } }), copia]);
    const inicio = await c.env.EVIDENCIAS.get(clave, { range: { offset: 0, length: 16 } });
    const bytes = new Uint8Array(inicio ? await inicio.arrayBuffer() : new ArrayBuffer(0));
    if (!firmaValida(contentType, bytes)) {
        await c.env.EVIDENCIAS.delete(clave);
        throw error(422, "El contenido del archivo no coincide con su tipo.");
    }
    return c.json(await guardar(c.env, d, { tipo, etiqueta, nombre, contentType, bytes: largo, clave, ocultos: 0, autor }), 201);
});

evidencias.post("/texto", requiereRol("qa"), async (c) => {
    const datos = await leerBody(c, textoSchema);
    const d = await destino(c.env, datos.resultado_id, datos.bug_id);
    const { texto, ocultos } = redactar(datos.texto);
    // Si es JSON válido se guarda como JSON (se ve formateado); si no, como texto
    let esJson = false;
    try {
        JSON.parse(texto);
        esJson = true;
    } catch {
        esJson = false;
    }
    const contentType = esJson ? "application/json" : "text/plain";
    const nombre = `${nombreSeguro(datos.etiqueta.toLowerCase())}.${esJson ? "json" : "txt"}`;
    const clave = claveR2(nombre);
    await c.env.EVIDENCIAS.put(clave, texto, { httpMetadata: { contentType: `${contentType}; charset=utf-8` } });
    const bytes = new TextEncoder().encode(texto).byteLength;
    return c.json(await guardar(c.env, d, { tipo: "texto", etiqueta: datos.etiqueta, nombre, contentType, bytes, clave, ocultos, autor: c.get("usuario").github }), 201);
});

evidencias.get("/uso", requiereRol("admin"), async (c) => {
    const { results } = await c.env.DB.prepare("SELECT kind, COUNT(*) AS n, COALESCE(SUM(size_bytes), 0) AS bytes FROM qa_evidence GROUP BY kind").all<{
        kind: Tipo;
        n: number;
        bytes: number;
    }>();
    const porTipo = Object.fromEntries((["imagen", "video", "texto", "pdf"] as const).map((t) => [t, { cantidad: 0, bytes: 0 }])) as Record<Tipo, { cantidad: number; bytes: number }>;
    for (const r of results) porTipo[r.kind] = { cantidad: r.n, bytes: r.bytes };
    const total = Object.values(porTipo).reduce((a, t) => ({ cantidad: a.cantidad + t.cantidad, bytes: a.bytes + t.bytes }), { cantidad: 0, bytes: 0 });
    return c.json({ total, por_tipo: porTipo });
});

evidencias.get("/:id", async (c) => c.json(evidenciaJson(await evidenciaOError(c.env, c.req.param("id")))));

evidencias.get("/:id/archivo", async (c) => {
    const e = await evidenciaOError(c.env, c.req.param("id"));
    const objeto = await c.env.EVIDENCIAS.get(e.r2_key);
    if (!objeto) throw error(404, "El archivo de la evidencia ya no está en el bucket.");
    // El texto siempre como text/plain o JSON (nunca HTML) y sin que el navegador adivine el tipo
    const contentType = e.kind === "texto" ? `${e.content_type}; charset=utf-8` : e.content_type;
    return new Response(objeto.body, {
        headers: {
            "Content-Type": contentType,
            "Content-Length": String(objeto.size),
            "Content-Disposition": `inline; filename*=UTF-8''${encodeURIComponent(e.filename)}`,
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "private, no-store",
        },
    });
});

evidencias.delete("/:id", requiereRol("admin"), async (c) => {
    const e = await evidenciaOError(c.env, c.req.param("id"));
    await c.env.EVIDENCIAS.delete(e.r2_key);
    await c.env.DB.prepare("DELETE FROM qa_evidence WHERE id = ?").bind(e.id).run();
    await auditar(c.env, c.get("usuario").github, "evidencia.borrar", { id: e.id, nombre: e.filename, bytes: e.size_bytes });
    return c.json({ borrada: e.id });
});
