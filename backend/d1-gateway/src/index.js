// Worker pasarela entre el backend (Railway) y Cloudflare D1 (Issue #58).
//
// La API REST de D1 es de administración y comparte el límite general de la API
// de Cloudflare; el backend escribe en cada llamada a la IA, así que va por este
// Worker con binding a D1.
//
// - GET  /health  → { ok: true } (sin token: para monitoreo)
// - POST /query   → { sql, params? }                      una sentencia
// - POST /batch   → { statements: [{ sql, params? }, ...] } varias, atómicas
//
// Todo menos /health exige `Authorization: Bearer <D1_GATEWAY_TOKEN>`. Solo se
// aceptan sentencias parametrizadas (`?` + params): los datos nunca se concatenan
// al SQL. El token da acceso completo a la base: es un secreto del backend.

const MAX_BATCH = 100;
const MAX_SQL_CHARS = 20_000;
const MAX_PARAMS = 100;

class BadRequest extends Error {}

function json(status, body) {
    return new Response(JSON.stringify(body), {
        status,
        headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
    });
}

// Comparación en tiempo constante (no revela cuántos caracteres coinciden)
function sameSecret(given, expected) {
    const enc = new TextEncoder();
    const a = enc.encode(given);
    const b = enc.encode(expected);
    if (a.byteLength !== b.byteLength) {
        crypto.subtle.timingSafeEqual(b, b); // mismo trabajo aunque el largo difiera
        return false;
    }
    return crypto.subtle.timingSafeEqual(a, b);
}

function authorized(request, env) {
    if (!env.D1_GATEWAY_TOKEN) return false; // sin secreto configurado, nadie entra
    const header = request.headers.get("Authorization") || "";
    const [scheme, token] = header.split(" ");
    return scheme === "Bearer" && Boolean(token) && sameSecret(token, env.D1_GATEWAY_TOKEN);
}

function toParam(value) {
    if (value === null || typeof value === "string" || typeof value === "number") return value;
    if (typeof value === "boolean") return value ? 1 : 0;
    throw new BadRequest("Los parámetros solo pueden ser texto, número, booleano o null.");
}

function prepare(env, item) {
    if (!item || typeof item.sql !== "string" || !item.sql.trim()) {
        throw new BadRequest("Cada sentencia necesita un campo `sql` con texto.");
    }
    if (item.sql.length > MAX_SQL_CHARS) throw new BadRequest("La sentencia es demasiado larga.");
    // Una sola sentencia por pedido: un `;` solo se admite al final
    if (item.sql.trim().replace(/;\s*$/, "").includes(";")) {
        throw new BadRequest("Se admite una sola sentencia por elemento (usá /batch para varias).");
    }
    const params = item.params ?? [];
    if (!Array.isArray(params) || params.length > MAX_PARAMS) {
        throw new BadRequest(`\`params\` tiene que ser una lista de hasta ${MAX_PARAMS} valores.`);
    }
    return env.DB.prepare(item.sql).bind(...params.map(toParam));
}

function result(r) {
    return { results: r.results ?? [], meta: r.meta ?? {} };
}

async function readJson(request) {
    try {
        return await request.json();
    } catch {
        throw new BadRequest("El cuerpo tiene que ser JSON.");
    }
}

export default {
    async fetch(request, env) {
        const { pathname } = new URL(request.url);

        if (pathname === "/health" && request.method === "GET") return json(200, { ok: true });
        if (!["/query", "/batch"].includes(pathname)) return json(404, { error: "Ruta inexistente." });
        if (request.method !== "POST") return json(405, { error: "Usá POST." });
        if (!authorized(request, env)) return json(401, { error: "Token inválido o ausente." });

        const started = Date.now();
        try {
            const body = await readJson(request);
            if (pathname === "/query") {
                const r = await prepare(env, body).all();
                return json(200, { ...result(r), elapsed_ms: Date.now() - started });
            }
            const statements = body?.statements;
            if (!Array.isArray(statements) || statements.length === 0 || statements.length > MAX_BATCH) {
                throw new BadRequest(`\`statements\` tiene que ser una lista de 1 a ${MAX_BATCH} sentencias.`);
            }
            // batch es atómico: si una sentencia falla, ninguna se aplica
            const rs = await env.DB.batch(statements.map((s) => prepare(env, s)));
            return json(200, { results: rs.map(result), elapsed_ms: Date.now() - started });
        } catch (err) {
            if (err instanceof BadRequest) return json(400, { error: err.message });
            // Errores de SQL (sintaxis, restricciones) vuelven como 422 con el mensaje de D1
            const message = String(err && err.message ? err.message : err);
            const isSql = /D1_ERROR|SQLITE|constraint|syntax|no such/i.test(message);
            return json(isSql ? 422 : 500, { error: message });
        }
    },
};
