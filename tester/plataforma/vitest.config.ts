// Tests del Worker dentro del runtime de Workers (Miniflare): D1 y R2 simulados, sin cuenta de Cloudflare.
import { generateKeyPairSync } from "node:crypto";
import { cloudflareTest, readD1Migrations } from "@cloudflare/vitest-plugin";
import { defineConfig } from "vitest/config";

// Par de claves propio de los tests: firman tokens de sesión "de Clerk" que el Worker valida con la pública
const { publicKey, privateKey } = generateKeyPairSync("rsa", {
    modulusLength: 2048,
    publicKeyEncoding: { type: "spki", format: "pem" },
    privateKeyEncoding: { type: "pkcs8", format: "pem" },
});

export default defineConfig(async () => {
    const migrations = await readD1Migrations("./migrations");
    return {
        plugins: [
            cloudflareTest({
                wrangler: { configPath: "./wrangler.toml" },
                miniflare: {
                    bindings: {
                        TEST_MIGRATIONS: migrations,
                        TEST_CLERK_PRIVATE_KEY: privateKey,
                        CLERK_JWT_KEY: publicKey,
                        CLERK_SECRET_KEY: "sk_test_prueba",
                        QA_ADMINS: "admin-fijo",
                    },
                },
            }),
        ],
        test: {
            include: ["test/**/*.test.ts"],
            restoreMocks: true,
            setupFiles: ["./test/apply-migrations.ts"],
        },
    };
});
