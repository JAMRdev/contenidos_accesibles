// Clerk simulado para los tests: tokens de sesión firmados con la clave privada de los tests
// y la API de usuarios de Clerk respondida desde memoria (fetch espiado).
import { env, SELF } from "cloudflare:test";
import { importPKCS8, SignJWT } from "jose";
import { vi } from "vitest";
import { frontendApi } from "../src/worker/clerk";

export const ORIGEN = "https://qa.test";

type Cuenta = { github: string | null; verificada?: boolean };

const usuarios = new Map<string, Cuenta>();
export const llamadasAClerk: string[] = [];

// API de GitHub simulada: cada test define cómo responde (sin definir, 500)
type RespuestaGithub = (url: URL, init?: RequestInit) => Response | Promise<Response>;
let github: RespuestaGithub = () => Response.json({ message: "sin simular" }, { status: 500 });
export const llamadasAGithub: { metodo: string; ruta: string; body: unknown }[] = [];

export function githubSimulado(respuesta: RespuestaGithub) {
    github = respuesta;
}

/** Registra un usuario de Clerk con su cuenta de GitHub (null: sin GitHub). */
export function usuarioClerk(sub: string, cuenta: Cuenta) {
    usuarios.set(sub, cuenta);
}

export function instalarClerk() {
    usuarios.clear();
    llamadasAClerk.length = 0;
    llamadasAGithub.length = 0;
    github = () => Response.json({ message: "sin simular" }, { status: 500 });
    // Los límites por usuario se prueban aparte (auth.test.ts): acá no tienen que cortar los tests
    vi.spyOn(env.LIMITE_API, "limit").mockResolvedValue({ success: true });
    vi.spyOn(env.LIMITE_PUBLICAR, "limit").mockResolvedValue({ success: true });
    const real = globalThis.fetch;
    vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
        const url = new URL(input instanceof Request ? input.url : String(input));
        if (url.hostname === "api.github.com") {
            llamadasAGithub.push({ metodo: init?.method ?? "GET", ruta: url.pathname + url.search, body: init?.body ? JSON.parse(String(init.body)) : null });
            return github(url, init);
        }
        if (url.hostname !== "api.clerk.com") return real(input, init);
        llamadasAClerk.push(url.pathname);
        const sub = decodeURIComponent(url.pathname.split("/").pop() ?? "");
        const cuenta = usuarios.get(sub);
        if (!cuenta) return Response.json({ errors: [{ code: "resource_not_found" }] }, { status: 404 });
        return Response.json({
            first_name: "Persona",
            last_name: "De Prueba",
            image_url: "https://img.clerk.com/x.png",
            external_accounts: cuenta.github
                ? [{ provider: "oauth_github", username: cuenta.github, verification: { status: cuenta.verificada === false ? "unverified" : "verified" } }]
                : [],
        });
    });
}

type Opciones = { azp?: string; iss?: string; expiraEn?: string; clave?: string };

export async function tokenDe(sub: string, op: Opciones = {}): Promise<string> {
    const clave = await importPKCS8(op.clave ?? env.TEST_CLERK_PRIVATE_KEY, "RS256");
    return new SignJWT({ azp: op.azp ?? ORIGEN, sid: "sess_prueba" })
        .setProtectedHeader({ alg: "RS256" })
        .setSubject(sub)
        .setIssuer(op.iss ?? `https://${frontendApi(env.CLERK_PUBLISHABLE_KEY)}`)
        .setIssuedAt()
        .setExpirationTime(op.expiraEn ?? "60s")
        .sign(clave);
}

/** fetch a la API del Worker con el token de `sub` (o sin token si sub es null). */
export async function api(sub: string | null, ruta: string, init: RequestInit = {}) {
    const headers = new Headers(init.headers);
    if (sub) headers.set("Authorization", `Bearer ${await tokenDe(sub)}`);
    if (init.body) headers.set("Content-Type", "application/json");
    return SELF.fetch(`${ORIGEN}${ruta}`, { ...init, headers });
}
