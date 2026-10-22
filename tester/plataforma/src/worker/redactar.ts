// Oculta datos sensibles en la evidencia de texto (request, response, curl, consola, HAR) antes de
// guardarla (Issue #128). Mejor ocultar de más que filtrar un token en un issue público.

const OCULTO = "[OCULTO]";

type Regla = { patron: RegExp; reemplazo: string | ((...m: string[]) => string) };

const REGLAS: Regla[] = [
    // Headers sensibles en texto plano, curl (-H 'Authorization: …') o consola
    { patron: /\b(authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key|x-auth-token)(\s*[:=]\s*)([^\r\n'"]+)/gi, reemplazo: (_m, h, sep) => `${h}${sep}${OCULTO}` },
    // Los mismos headers en JSON o HAR: "name": "Authorization", "value": "…"  /  "Authorization": "…"
    { patron: /("(?:authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key|x-auth-token)"\s*:\s*")([^"]*)(")/gi, reemplazo: (_m, a, _v, c) => `${a}${OCULTO}${c}` },
    {
        patron: /("name"\s*:\s*"(?:authorization|proxy-authorization|cookie|set-cookie|x-api-key|api-key|x-auth-token)"\s*,\s*"value"\s*:\s*")([^"]*)(")/gi,
        reemplazo: (_m, a, _v, c) => `${a}${OCULTO}${c}`,
    },
    // Campos con nombre de secreto en JSON: "token": "…", "password": "…", "secret": "…"
    { patron: /("(?:[\w-]*token|password|passwd|secret|client_secret|api_key|apikey|key_value|raw_key)"\s*:\s*")([^"]*)(")/gi, reemplazo: (_m, a, _v, c) => `${a}${OCULTO}${c}` },
    // Bearer y Basic sueltos
    { patron: /\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]{8,}/g, reemplazo: (_m, t) => `${t} ${OCULTO}` },
    // JWT (tres partes base64url)
    { patron: /\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b/g, reemplazo: OCULTO },
    // Claves conocidas: Clerk/Stripe (sk_, pk_, rk_), Google (AIza…), Groq (gsk_), OpenAI (sk-…), GitHub (ghp_, github_pat_)
    { patron: /\b(?:sk|pk|rk)_(?:test|live)_[A-Za-z0-9]{10,}\b/g, reemplazo: OCULTO },
    { patron: /\bAIza[0-9A-Za-z_-]{30,}\b/g, reemplazo: OCULTO },
    { patron: /\bgsk_[A-Za-z0-9]{20,}\b/g, reemplazo: OCULTO },
    { patron: /\bsk-[A-Za-z0-9_-]{20,}\b/g, reemplazo: OCULTO },
    { patron: /\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{30,}\b/g, reemplazo: OCULTO },
    // Firmas y credenciales en URLs (R2/S3 firmadas, ?token=, ?sig=)
    {
        patron: /([?&](?:X-Amz-Signature|X-Amz-Credential|X-Amz-Security-Token|Signature|sig|token|access_token|api_key|key)=)[^&\s"'#]+/gi,
        reemplazo: (_m, p) => `${p}${OCULTO}`,
    },
];

export function redactar(texto: string): { texto: string; ocultos: number } {
    let ocultos = 0;
    let resultado = texto;
    for (const { patron, reemplazo } of REGLAS) {
        resultado = resultado.replace(patron, (...args) => {
            const m = args as string[];
            // No contar lo que ya estaba oculto
            if (m[0].includes(OCULTO)) return m[0];
            ocultos++;
            return typeof reemplazo === "string" ? reemplazo : reemplazo(...m);
        });
    }
    return { texto: resultado, ocultos };
}
