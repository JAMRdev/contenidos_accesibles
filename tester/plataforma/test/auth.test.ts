import { env, SELF } from "cloudflare:test";
import { generateKeyPair, exportPKCS8 } from "jose";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { api, instalarClerk, llamadasAClerk, ORIGEN, tokenDe, usuarioClerk } from "./clerk-simulado";

beforeEach(() => {
    instalarClerk();
    usuarioClerk("user_admin", { github: "admin-fijo" });
    usuarioClerk("user_sin_rol", { github: "sin-rol" });
});

async function me(token: string | null) {
    return SELF.fetch(`${ORIGEN}/api/me`, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
}

describe("GET /api/config", () => {
    it("no pide login y devuelve la publishable key de Clerk", async () => {
        const res = await SELF.fetch(`${ORIGEN}/api/config`);
        expect(res.status).toBe(200);
        expect(await res.json()).toEqual({ clerk_publishable_key: env.CLERK_PUBLISHABLE_KEY });
    });
});

describe("token de sesión", () => {
    it("sin token responde 401", async () => {
        const res = await me(null);
        expect(res.status).toBe(401);
        expect(await res.json()).toEqual({ error: "Iniciá sesión para usar la plataforma de QA." });
    });

    it("un token válido de un admin fijo devuelve su identidad y permisos", async () => {
        const res = await me(await tokenDe("user_admin"));
        expect(res.status).toBe(200);
        expect(await res.json()).toEqual({
            github: "admin-fijo",
            nombre: "Persona De Prueba",
            avatar_url: "https://img.clerk.com/x.png",
            rol: "admin",
            permisos: { escribir: true, administrar: true },
        });
    });

    it.each([
        ["firmado con otra clave", async () => tokenDe("user_admin", { clave: await exportPKCS8((await generateKeyPair("RS256", { extractable: true })).privateKey) })],
        ["vencido", () => tokenDe("user_admin", { expiraEn: "-1m" })],
        ["de otro origen (azp)", () => tokenDe("user_admin", { azp: "https://otra-web.example" })],
        ["de otra instancia de Clerk (iss)", () => tokenDe("user_admin", { iss: "https://otra.clerk.accounts.dev" })],
    ])("un token %s responde 401", async (_caso, token) => {
        const res = await me(await token());
        expect(res.status).toBe(401);
        expect(llamadasAClerk).toEqual([]);
    });

    it("la identidad se guarda y no se consulta a Clerk en cada request", async () => {
        await me(await tokenDe("user_admin"));
        await me(await tokenDe("user_admin"));
        expect(llamadasAClerk).toEqual(["/v1/users/user_admin"]);
    });
});

describe("cuenta de GitHub y rol", () => {
    it("sin cuenta de GitHub o sin verificar responde 403", async () => {
        usuarioClerk("user_sin_github", { github: null });
        usuarioClerk("user_no_verificado", { github: "alguien", verificada: false });
        for (const sub of ["user_sin_github", "user_no_verificado"]) {
            const res = await me(await tokenDe(sub));
            expect(res.status).toBe(403);
            expect(await res.json()).toEqual({ error: "Entrá con tu cuenta de GitHub." });
        }
    });

    it("una cuenta sin rol recibe 403 en toda la API y queda en la auditoría", async () => {
        for (const ruta of ["/api/me", "/api/miembros", "/api/auditoria", "/api/lo-que-sea"]) {
            const res = await api("user_sin_rol", ruta);
            expect(res.status).toBe(403);
        }
        const { entradas } = await (await api("user_admin", "/api/auditoria")).json<{ entradas: { actor: string; accion: string }[] }>();
        expect(entradas.filter((e) => e.accion === "acceso.denegado" && e.actor === "sin-rol")).toHaveLength(4);
    });

    it("una cuenta de Clerk que ya no existe responde 401", async () => {
        const res = await me(await tokenDe("user_borrado"));
        expect(res.status).toBe(401);
    });

    it("si Clerk no responde, 502 con un mensaje claro", async () => {
        vi.mocked(globalThis.fetch).mockRejectedValueOnce(new TypeError("network"));
        const res = await me(await tokenDe("user_admin"));
        expect(res.status).toBe(502);
    });

    it("al superar el límite por usuario responde 429 con Retry-After", async () => {
        vi.spyOn(env.LIMITE_API, "limit").mockResolvedValueOnce({ success: false });
        const res = await me(await tokenDe("user_admin"));
        expect(res.status).toBe(429);
        expect(res.headers.get("Retry-After")).toBe("60");
    });
});
