// Login con Clerk (Issue #126): la misma instancia de Clerk que el panel /admin del backend.
//
// - El token de sesión se valida sin llamadas de red con la clave pública (CLERK_JWT_KEY), solo si
//   lo emitió la instancia de la publishable key (iss) y la web de QA (azp).
// - El usuario de GitHub sale de la API de Clerk (cuenta de GitHub verificada por OAuth) y se guarda
//   en qa_users una hora, así no se consulta a Clerk en cada request. Railway no participa.
import { importSPKI, jwtVerify } from "jose";
import type { Env } from "./env";

const CLERK_API = "https://api.clerk.com/v1";
const GITHUB_PROVIDERS = new Set(["oauth_github", "github"]);
const IDENTIDAD_TTL_MS = 60 * 60 * 1000;
const API_TIMEOUT_MS = 8000;

export class AuthError extends Error {
    constructor(
        readonly status: 401 | 403 | 502,
        message: string,
    ) {
        super(message);
    }
}

export type Identidad = {
    clerkUserId: string;
    github: string;
    nombre: string | null;
    avatarUrl: string | null;
};

// Dominio de la Frontend API, codificado en la publishable key (pk_test_<base64>$)
export function frontendApi(publishableKey: string): string | null {
    const encoded = publishableKey.match(/^pk_(?:test|live)_(.+)$/)?.[1];
    if (!encoded) return null;
    try {
        return atob(encoded).replace(/\$$/, "") || null;
    } catch {
        return null;
    }
}

const claves = new Map<string, Promise<CryptoKey>>();

function clavePublica(pem: string): Promise<CryptoKey> {
    // En variables de entorno los saltos de línea suelen llegar como "\n" literal
    const normalizada = pem.replace(/\\n/g, "\n").trim();
    let clave = claves.get(normalizada);
    if (!clave) {
        clave = importSPKI(normalizada, "RS256") as Promise<CryptoKey>;
        claves.set(normalizada, clave);
    }
    return clave;
}

export function origenesPermitidos(env: Env, origenPropio: string): string[] {
    const extra = (env.ORIGENES_EXTRA ?? "").split(",").map((o) => o.trim().replace(/\/$/, ""));
    return [origenPropio, ...extra].filter(Boolean);
}

/** Id de usuario de Clerk (sub) del token de sesión. Lanza AuthError(401) si no es válido. */
export async function verificarToken(env: Env, token: string, origenPropio: string): Promise<string> {
    const dominio = frontendApi(env.CLERK_PUBLISHABLE_KEY);
    if (!dominio || !env.CLERK_JWT_KEY) throw new AuthError(502, "El login con Clerk no está configurado.");
    try {
        const { payload } = await jwtVerify(token, await clavePublica(env.CLERK_JWT_KEY), {
            issuer: `https://${dominio}`,
            algorithms: ["RS256"],
            clockTolerance: 5,
        });
        if (typeof payload.azp !== "string" || !origenesPermitidos(env, origenPropio).includes(payload.azp)) {
            throw new Error("azp");
        }
        if (!payload.sub) throw new Error("sub");
        return payload.sub;
    } catch {
        throw new AuthError(401, "La sesión no es válida o venció. Volvé a iniciar sesión.");
    }
}

type ClerkUser = {
    first_name: string | null;
    last_name: string | null;
    image_url: string | null;
    external_accounts?: {
        provider: string;
        username: string | null;
        image_url?: string | null;
        verification?: { status?: string } | null;
    }[];
};

async function consultarClerk(env: Env, clerkUserId: string): Promise<Identidad> {
    let res: Response;
    try {
        res = await fetch(`${CLERK_API}/users/${encodeURIComponent(clerkUserId)}`, {
            headers: { Authorization: `Bearer ${env.CLERK_SECRET_KEY}` },
            signal: AbortSignal.timeout(API_TIMEOUT_MS),
        });
    } catch {
        throw new AuthError(502, "No se pudo consultar Clerk. Probá de nuevo en unos minutos.");
    }
    if (res.status === 404) throw new AuthError(401, "La cuenta ya no existe en Clerk. Volvé a iniciar sesión.");
    if (!res.ok) throw new AuthError(502, "No se pudo consultar Clerk. Probá de nuevo en unos minutos.");
    const user = (await res.json()) as ClerkUser;
    const cuenta = (user.external_accounts ?? []).find(
        (a) => GITHUB_PROVIDERS.has(a.provider) && a.username && a.verification?.status === "verified",
    );
    if (!cuenta?.username) throw new AuthError(403, "Entrá con tu cuenta de GitHub.");
    const nombre = [user.first_name, user.last_name].filter(Boolean).join(" ") || null;
    return { clerkUserId, github: cuenta.username, nombre, avatarUrl: cuenta.image_url || user.image_url || null };
}

/** Quién es el usuario de Clerk: de qa_users si se consultó hace menos de una hora, o de la API de Clerk. */
export async function identificar(env: Env, clerkUserId: string): Promise<Identidad> {
    const ahora = new Date();
    const fila = await env.DB.prepare(
        "SELECT github_username, display_name, avatar_url, fetched_at FROM qa_users WHERE clerk_user_id = ?",
    )
        .bind(clerkUserId)
        .first<{ github_username: string; display_name: string | null; avatar_url: string | null; fetched_at: string }>();
    if (fila && ahora.getTime() - Date.parse(fila.fetched_at) < IDENTIDAD_TTL_MS) {
        await env.DB.prepare("UPDATE qa_users SET last_seen_at = ? WHERE clerk_user_id = ?")
            .bind(ahora.toISOString(), clerkUserId)
            .run();
        return { clerkUserId, github: fila.github_username, nombre: fila.display_name, avatarUrl: fila.avatar_url };
    }
    const identidad = await consultarClerk(env, clerkUserId);
    await env.DB.prepare(
        `INSERT INTO qa_users (clerk_user_id, github_username, display_name, avatar_url, fetched_at, last_seen_at)
         VALUES (?, ?, ?, ?, ?, ?)
         ON CONFLICT (clerk_user_id) DO UPDATE SET github_username = excluded.github_username,
             display_name = excluded.display_name, avatar_url = excluded.avatar_url,
             fetched_at = excluded.fetched_at, last_seen_at = excluded.last_seen_at`,
    )
        .bind(clerkUserId, identidad.github, identidad.nombre, identidad.avatarUrl, ahora.toISOString(), ahora.toISOString())
        .run();
    return identidad;
}
