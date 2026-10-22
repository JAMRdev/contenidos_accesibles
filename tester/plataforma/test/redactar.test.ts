import { describe, expect, it } from "vitest";
import { redactar } from "../src/worker/redactar";

describe("redactar", () => {
    it("oculta el header Authorization de un curl y cuenta lo ocultado", () => {
        const curl = `curl -X POST "$API/analysis/sample?mock=true" -H 'Authorization: Bearer eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ1c2VyXzEyMyJ9.c2lnbmF0dXJhLWZhbHNh' -H 'Content-Type: application/json'`;
        const { texto, ocultos } = redactar(curl);
        expect(texto).toContain("Authorization: [OCULTO]");
        expect(texto).not.toContain("eyJ");
        expect(texto).toContain("Content-Type: application/json");
        expect(ocultos).toBe(1);
    });

    it("oculta cookies, claves de proveedores y tokens sueltos", () => {
        const texto = [
            "Cookie: __session=abc123; other=1",
            "AI key: AIzaSyA1234567890abcdefghijklmnopqrstuv",
            "groq gsk_abcdefghijklmnopqrstuvwxyz123456",
            "clerk sk_test_abcdefghijklmnopqrstuvwxyz",
            "github ghp_abcdefghijklmnopqrstuvwxyz0123456789",
            "jwt eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",
        ].join("\n");
        const r = redactar(texto);
        expect(r.texto).not.toMatch(/abc123|AIzaSy|gsk_|sk_test_|ghp_|eyJ/);
        expect(r.ocultos).toBe(6);
    });

    it("oculta headers y campos secretos en JSON y HAR", () => {
        const har = JSON.stringify({
            request: { headers: [{ name: "Authorization", value: "Bearer abcdefghijklmnop" }, { name: "Accept", value: "application/json" }] },
            response: { content: { text: '{"token": "xyz-secreto", "file_id": "f_1"}' } },
            config: { password: "hunter2", Authorization: "Basic dXNlcjpwYXNz" },
        });
        const r = redactar(har);
        expect(r.texto).not.toMatch(/abcdefghijklmnop|hunter2|dXNlcjpwYXNz/);
        expect(r.texto).toContain("application/json");
        expect(r.texto).toContain("f_1");
    });

    it("oculta firmas de URLs firmadas y deja el resto de la URL", () => {
        const url = "https://bucket.r2.cloudflarestorage.com/users/x.pdf?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Credential=AKIA%2F2026&X-Amz-Signature=deadbeef1234";
        const r = redactar(url);
        expect(r.texto).toContain("X-Amz-Algorithm=AWS4-HMAC-SHA256");
        expect(r.texto).toContain("X-Amz-Credential=[OCULTO]");
        expect(r.texto).toContain("X-Amz-Signature=[OCULTO]");
    });

    it("no toca un texto sin datos sensibles", () => {
        const texto = '{"analysis_id": "1b2c", "estimated_score": 72, "detail": "Diagnóstico no encontrado"}';
        expect(redactar(texto)).toEqual({ texto, ocultos: 0 });
    });
});
